/**
 * WhatsApp Web bridge.
 *
 * Written from scratch after reading how OpenWA structures this, but kept to
 * the one job we need: connect ONE number, forward incoming messages to the
 * Python backend, and send replies back out. No multi-session, no plugins, no
 * RBAC — the backend already owns all of that.
 *
 * whatsapp-web.js (headless Chromium) rather than Baileys: heavier on RAM but
 * meaningfully lower ban risk, and this runs beside the app, not at scale.
 *
 * ⚠ This drives WhatsApp Web, which is against WhatsApp's Terms of Service.
 *   Use a dedicated number you can afford to lose. Never a personal number.
 */

import fs, { existsSync } from "node:fs";

import express from "express";
import qrcode from "qrcode";
import qrcodeTerminal from "qrcode-terminal";
import pkg from "whatsapp-web.js";

const { Client, LocalAuth, MessageMedia } = pkg;

const PORT = Number(process.env.BRIDGE_PORT || 8100);
const BACKEND = (process.env.BACKEND_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
const TOKEN = process.env.BRIDGE_TOKEN || "";

// Everything the backend needs to know about the link, in one place.
const state = {
  status: "starting",
  qr: null,
  me: null,
  error: null,
  lastEventAt: null,   // last time WhatsApp actually told us anything
  lastCheckAt: null,   // last successful liveness poll
  recoveries: 0,
};

/**
 * Log to a file synchronously.
 *
 * Node block-buffers stdout when it is redirected to a file, so the log can be
 * hours behind reality — which is exactly when you need it. This appends
 * immediately.
 */
const LOG_FILE = process.env.BRIDGE_LOG || "bridge.log";
function log(...parts) {
  const line = `${new Date().toISOString()} ${parts.join(" ")}\n`;
  process.stdout.write(line);
  try {
    fs.appendFileSync(LOG_FILE, line);
  } catch {
    /* logging must never take the bridge down */
  }
}

/**
 * Puppeteer downloads its own Chromium on install, which is ~150 MB and the
 * step most likely to have failed. If a normal Chrome is already on the
 * machine, use that instead — set CHROME_PATH, or let these defaults find it.
 */
function findChrome() {
  if (process.env.CHROME_PATH) return process.env.CHROME_PATH;
  const candidates = [
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    `${process.env.LOCALAPPDATA}/Google/Chrome/Application/chrome.exe`,
    "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  ];
  return candidates.find((p) => p && existsSync(p)) || undefined;
}

const chromePath = findChrome();
if (chromePath) console.log(`Using browser: ${chromePath}`);

const client = new Client({
  authStrategy: new LocalAuth({ dataPath: "./.session" }),
  puppeteer: {
    headless: true,
    executablePath: chromePath, // undefined = fall back to Puppeteer's own copy
    args: ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
  },
});

client.on("qr", async (qr) => {
  state.status = "qr";
  state.qr = await qrcode.toDataURL(qr);
  console.log("\nScan this with WhatsApp → Settings → Linked devices:\n");
  qrcodeTerminal.generate(qr, { small: true });
  console.log(`\nOr open http://localhost:${PORT}/qr in a browser.\n`);
});

client.on("ready", () => {
  state.status = "connected";
  state.qr = null;
  state.me = client.info?.wid?.user ?? null;
  state.lastEventAt = Date.now();
  log(`Connected as ${state.me}`);
});

client.on("auth_failure", (m) => {
  state.status = "auth_failure";
  state.error = m;
  console.error("Auth failed:", m);
});

client.on("disconnected", (reason) => {
  state.status = "disconnected";
  state.error = reason;
  log("Disconnected:", reason);
  recover("disconnected event");
});

/**
 * Bring the session back up.
 *
 * Guarded so overlapping triggers (a disconnect event and a failed heartbeat
 * arriving together) don't start two browsers fighting over the same profile.
 */
let recovering = false;

/** Never await a hang forever — a stuck promise here freezes the watchdog. */
function withTimeout(promise, ms, label) {
  return Promise.race([
    promise,
    new Promise((_, reject) =>
      setTimeout(() => reject(new Error(`${label} timed out after ${ms}ms`)), ms)
    ),
  ]);
}

async function recover(why) {
  if (recovering) return;
  recovering = true;
  state.recoveries += 1;
  log(`Recovering (${why}) — attempt ${state.recoveries}`);
  try {
    await withTimeout(client.destroy().catch(() => {}), 30_000, "destroy");
    await withTimeout(client.initialize(), 120_000, "initialize");
    state.lastCheckAt = Date.now();
    log("Recovery finished");
  } catch (e) {
    state.status = "error";
    state.error = e.message;
    log("Recovery failed:", e.message);
  } finally {
    // Always released, even on timeout. Leaving this true would silently
    // disable every future heartbeat — the exact failure we are fixing.
    recovering = false;
  }
}

/**
 * Ask WhatsApp whether it is really there.
 *
 * The `disconnected` event is not reliable — the page can die, or WhatsApp can
 * stop delivering, while the library still believes it is connected. That is
 * how this bridge sat "connected" for nearly three hours without receiving a
 * single message. getState() round-trips to the page, so it fails when the
 * session is actually gone.
 */
const HEARTBEAT_MS = Number(process.env.BRIDGE_HEARTBEAT_MS || 60_000);

// If getState() keeps saying CONNECTED but nothing at all arrives for this
// long, stop believing it and rebuild the session anyway. Groups and status
// updates land constantly on a live account, so real silence this long means
// the socket is dead even when the library disagrees.
const STALE_MS = Number(process.env.BRIDGE_STALE_MS || 30 * 60_000);

async function heartbeat() {
  if (recovering || state.status === "qr") return;
  try {
    const reported = await withTimeout(client.getState(), 20_000, "getState");

    if (reported !== "CONNECTED") {
      log(`Heartbeat: WhatsApp reports ${reported}`);
      state.status = "disconnected";
      return await recover(`state ${reported}`);
    }

    state.lastCheckAt = Date.now();
    if (state.status !== "connected") {
      state.status = "connected";
      state.error = null;
      log("Session healthy again");
    }

    const quiet = state.lastEventAt ? Date.now() - state.lastEventAt : 0;
    if (quiet > STALE_MS) {
      log(`Heartbeat: says CONNECTED but silent for ${Math.round(quiet / 60000)} min`);
      await recover("stale despite healthy state");
    }
  } catch (e) {
    log("Heartbeat failed:", e.message);
    state.status = "disconnected";
    state.error = e.message;
    await recover("heartbeat failed");
  }
}

// `await` inside setInterval doesn't chain, so a slow beat could overlap the
// next — the `recovering` guard and the timeouts above keep that harmless.
setInterval(() => {
  heartbeat().catch((e) => log("Heartbeat threw:", e.message));
}, HEARTBEAT_MS);

// A crash anywhere else must not take the bridge down silently. Log it and
// keep running; the heartbeat repairs whatever broke.
process.on("unhandledRejection", (e) => log("Unhandled rejection:", e?.message ?? e));
process.on("uncaughtException", (e) => log("Uncaught exception:", e?.message ?? e));

// Ids already forwarded. Two events can describe the same message, and a
// customer must never get two replies. Bounded so a long-running bridge does
// not grow this forever.
const seen = new Set();

/**
 * Dedupe key for a message.
 *
 * `msg.id._serialized` is undefined for @lid senders on this whatsapp-web.js
 * version, so it cannot be trusted on its own — an earlier version of this
 * function treated a missing id as "already seen" and silently dropped every
 * real message. Fall back to sender+timestamp+body, which is stable across
 * both the `message` and `message_create` events for the same message.
 */
function dedupeKey(msg) {
  return (
    msg.id?._serialized ||
    `${msg.from}|${msg.timestamp}|${(msg.body || "").slice(0, 60)}`
  );
}

function firstTime(key) {
  if (seen.has(key)) return false;
  seen.add(key);
  if (seen.size > 500) seen.delete(seen.values().next().value);
  return true;
}

async function onIncoming(msg, source) {
  // Log before filtering: when "the agent isn't replying", the first thing you
  // need to know is whether the event fired at all.
  state.lastEventAt = Date.now();
  log(
    `[msg:${source}] from=${msg.from} type=${msg.type} fromMe=${msg.fromMe} status=${msg.isStatus}`
  );

  // Ignore groups, status broadcasts and our own messages — this agent answers
  // one-to-one customer chats only.
  if (msg.fromMe || msg.isStatus || msg.from?.endsWith("@g.us")) {
    log(`[msg:${source}] skipped (own message, status, or group)`);
    return;
  }
  if (!firstTime(dedupeKey(msg))) {
    log(`[msg:${source}] skipped (already handled)`);
    return;
  }

  const contact = await msg.getContact().catch(() => null);
  const from = phoneOf(contact) || msg.from.replace(/@(c\.us|lid)$/, "");

  const payload = {
    from,
    name: contact?.pushname || contact?.name || null,
    type: msg.type === "ptt" || msg.type === "audio" ? "audio" : "text",
    text: msg.body || "",
    audio_base64: null,
  };
  log(`[msg:${source}] forwarding as ${from} (${payload.name || "no name"})`);

  if (payload.type === "audio" && msg.hasMedia) {
    const media = await msg.downloadMedia().catch(() => null);
    // Send the bytes inline. Meta needs a second fetch to resolve a media id;
    // here we already have the data, so the backend gets it in one hop.
    if (media) payload.audio_base64 = media.data;
  }

  await forward(payload);
}

/**
 * Hand a message to the backend, retrying briefly.
 *
 * The backend restarts on every code change, and a customer message that lands
 * in that window used to be dropped with one line in the log. A few spaced
 * retries cover a restart without holding the message forever.
 */
async function forward(payload, attempt = 1) {
  const MAX = 5;
  try {
    const r = await fetch(`${BACKEND}/api/whatsapp/web/incoming`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Bridge-Token": TOKEN },
      body: JSON.stringify(payload),
    });
    if (r.ok) {
      if (attempt > 1) log(`Forwarded on attempt ${attempt}`);
      return;
    }
    // A 4xx is our bug, not a blip — retrying can't fix a rejected payload.
    if (r.status < 500) return log("Backend rejected message:", r.status, await r.text());
    throw new Error(`backend returned ${r.status}`);
  } catch (e) {
    if (attempt >= MAX) {
      return log(`Backend unreachable after ${MAX} attempts — message lost:`, e.message);
    }
    const wait = attempt * 3000;
    log(`Backend unreachable (${e.message}) — retry ${attempt + 1}/${MAX} in ${wait / 1000}s`);
    setTimeout(() => forward(payload, attempt + 1), wait);
  }
}

// Two listeners on purpose. `message` is the documented one, but this version
// of whatsapp-web.js is already broken in other places against the current
// WhatsApp Web, so `message_create` (a superset, includes our own sends) is
// wired up as a fallback. Duplicates are filtered above, not here.
client.on("message", (msg) => onIncoming(msg, "message"));
client.on("message_create", (msg) => onIncoming(msg, "create"));

// --- HTTP API the backend calls -----------------------------------------

const app = express();
app.use(express.json({ limit: "25mb" }));

/** Shared-secret check. The bridge can send messages, so it is not open. */
app.use((req, res, next) => {
  if (req.path === "/qr" || !TOKEN) return next();
  if (req.get("X-Bridge-Token") !== TOKEN) return res.status(401).json({ error: "bad token" });
  next();
});

app.get("/status", (_req, res) => res.json(state));

/** QR as a plain image so the dashboard can show it without extra plumbing. */
app.get("/qr", (_req, res) => {
  if (!state.qr) return res.status(404).json({ status: state.status });
  res.type("png").send(Buffer.from(state.qr.split(",")[1], "base64"));
});

app.post("/send/text", async (req, res) => {
  const { to, text } = req.body;
  try {
    const sent = await client.sendMessage(await resolveChatId(to), text);
    // sendMessage can return undefined on some whatsapp-web.js/WhatsApp Web
    // combinations even when the message goes out. Don't fail the send over a
    // missing receipt.
    res.json({ id: sent?.id?._serialized ?? null, receipt: Boolean(sent) });
  } catch (e) {
    log("send/text failed:", e);
    res.status(502).json({ error: e.message, stack: String(e.stack || "").slice(0, 600) });
  }
});

app.post("/send/audio", async (req, res) => {
  const { to, audio_base64, mime } = req.body;
  try {
    const media = new MessageMedia(mime || "audio/mpeg", audio_base64, "reply.mp3");
    // sendAudioAsVoice renders it as a voice note rather than a file card.
    const sent = await client.sendMessage(await resolveChatId(to), media, {
      sendAudioAsVoice: true,
    });
    // sendMessage can return undefined on some whatsapp-web.js/WhatsApp Web
    // combinations even when the message goes out. Don't fail the send over a
    // missing receipt.
    res.json({ id: sent?.id?._serialized ?? null, receipt: Boolean(sent) });
  } catch (e) {
    log("send/audio failed:", e);
    res.status(502).json({ error: e.message, stack: String(e.stack || "").slice(0, 600) });
  }
});

/**
 * Recent one-to-one chats with their latest messages.
 *
 * Read-only history so the dashboard isn't empty before the first new message
 * arrives. The backend stores these directly — it does NOT run them through
 * the agent, so importing never fires replies at old conversations.
 */
app.post("/sync", async (req, res) => {
  const chatLimit = Number(req.body?.chats || 20);
  const msgLimit = Number(req.body?.messages || 10);
  let allChats;
  try {
    allChats = await client.getChats();
  } catch (e) {
    // getChats() reaches into WhatsApp Web's own minified bundle, so it breaks
    // whenever WhatsApp ships a change ahead of whatsapp-web.js. Live message
    // events use a different path and keep working, so this is not fatal —
    // report it plainly instead of a one-letter error from the bundle.
    log("getChats failed:", e);
    return res.status(503).json({
      error:
        "Chat import unavailable — WhatsApp Web changed. Live messages still work.",
      detail: e.message,
    });
  }

  try {
    const chats = allChats
      .filter((c) => !c.isGroup && c.id?.server === "c.us")
      .slice(0, chatLimit);

    const threads = [];
    for (const chat of chats) {
      const msgs = await chat.fetchMessages({ limit: msgLimit }).catch(() => []);
      threads.push({
        from: chat.id.user,
        name: chat.name || null,
        messages: msgs
          .filter((m) => m.body || m.type === "ptt" || m.type === "audio")
          .map((m) => ({
            id: m.id?._serialized ?? null,
            sender: m.fromMe ? "us" : "them",
            type: m.type === "ptt" || m.type === "audio" ? "audio" : "text",
            text: m.body || "",
            at: new Date(m.timestamp * 1000).toISOString(),
          })),
      });
    }
    res.json({ threads });
  } catch (e) {
    // whatsapp-web.js surfaces errors from WhatsApp's own minified bundle, so
    // e.message is often a single letter. Log the stack or it's undebuggable.
    log("sync failed:", e);
    res.status(502).json({ error: e.message, stack: String(e.stack || "").slice(0, 800) });
  }
});

/** Diagnostic: what does WhatsApp actually give us for a given id? */
app.get("/debug/contact/:id", async (req, res) => {
  try {
    const raw = req.params.id;
    const id = raw.includes("@") ? raw : `${raw}@lid`;
    const contact = await client.getContactById(id);
    let mapped = null;
    try {
      // WhatsApp keeps a LID -> phone-number mapping in its own store.
      mapped = await client.pupPage.evaluate((lid) => {
        const s = window.Store;
        const wid = s?.WidFactory?.createWid?.(lid);
        const pn =
          s?.LidUtils?.getPhoneNumber?.(wid) ??
          s?.LidUtils?.getCurrentLid?.(wid) ??
          null;
        return pn ? (pn._serialized ?? String(pn)) : null;
      }, id);
    } catch (e) {
      mapped = `evaluate failed: ${e.message}`;
    }
    res.json({
      queried: id,
      number: contact?.number ?? null,
      pushname: contact?.pushname ?? null,
      name: contact?.name ?? null,
      idUser: contact?.id?.user ?? null,
      idServer: contact?.id?.server ?? null,
      formatted: await contact?.getFormattedNumber?.().catch(() => null),
      lidMapped: mapped,
      keys: contact ? Object.keys(contact).slice(0, 30) : [],
    });
  } catch (e) {
    res.status(502).json({ error: e.message });
  }
});

app.post("/logout", async (_req, res) => {
  await client.logout().catch(() => {});
  state.status = "disconnected";
  state.me = null;
  res.json({ ok: true });
});

/**
 * Resolve a plain number to the id WhatsApp actually wants.
 *
 * Building "<digits>@c.us" by hand used to work, but WhatsApp has been moving
 * accounts to LID addressing (@lid), and a hand-built c.us id fails with an
 * unhelpful "cannot read properties of undefined". getNumberId asks WhatsApp
 * for the real id, which also tells us whether the number exists at all.
 */
/**
 * The real phone number behind a contact.
 *
 * WhatsApp is migrating senders to LID addressing, so msg.from is often
 * "<opaque-id>@lid". Do NOT use contact.number for these — it echoes the LID
 * straight back, which is how LIDs ended up stored as phone numbers and made
 * the allowlist unmatchable. contact.id resolves to the real c.us wid:
 *
 *   number  : "201769057575153"   <- the LID
 *   id.user : "917067133630"      <- the actual number
 */
function phoneOf(contact) {
  if (!contact) return null;
  if (contact.id?.server === "c.us" && contact.id.user) {
    return String(contact.id.user).replace(/\D/g, "");
  }
  // Only trust contact.number when it isn't just the LID repeated back.
  const n = String(contact.number || "").replace(/\D/g, "");
  return n && n !== String(contact.id?.user || "") ? n : null;
}

async function resolveChatId(number) {
  const digits = String(number).replace(/\D/g, "");
  const info = await client.getNumberId(digits);
  if (!info) throw new Error(`${digits} is not registered on WhatsApp`);
  return info._serialized;
}

const server = app.listen(PORT, () =>
  console.log(`Bridge listening on http://localhost:${PORT}`)
);

/**
 * Shut the headless browser down with us.
 *
 * Killing the node process alone leaves Chrome running and holding a lock on
 * .session — the next start then hangs in "starting" forever with no error.
 * A handful of restarts is enough to wedge it completely.
 */
let closing = false;
async function shutdown(signal) {
  if (closing) return;
  closing = true;
  console.log(`\n${signal} — closing browser…`);
  server.close();
  await client.destroy().catch((e) => console.error("destroy failed:", e.message));
  process.exit(0);
}
for (const sig of ["SIGINT", "SIGTERM", "SIGHUP", "SIGBREAK"]) {
  process.on(sig, () => shutdown(sig));
}

client.initialize().catch((e) => {
  state.status = "error";
  state.error = e.message;
  console.error("Failed to start:", e);
});
