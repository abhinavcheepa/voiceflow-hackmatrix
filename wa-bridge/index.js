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

import { exec, spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import fs, { existsSync } from "node:fs";

import express from "express";
import qrcode from "qrcode";
import qrcodeTerminal from "qrcode-terminal";
import pkg from "whatsapp-web.js";

const { Client, LocalAuth, MessageMedia } = pkg;

const PORT = Number(process.env.BRIDGE_PORT || 8100);
function getBackendUrl() {
  return (process.env.BACKEND_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
}
const TOKEN = process.env.BRIDGE_SECRET || process.env.BRIDGE_TOKEN || "";

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
const SESSION_DIR = "./.session";
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
    "/usr/bin/chromium",
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

/**
 * Kill the headless browser for real.
 *
 * `client.destroy()` does not reliably kill Chrome once the page has detached —
 * the processes survive, keep the lock on .session, and every later
 * `initialize()` dies with "The browser is already running". That loop ran 15
 * times here before anyone noticed. So: ask nicely, then kill the process tree.
 */
async function killBrowser() {
  const browser = client.pupBrowser;
  const pid = browser?.process?.()?.pid;

  await withTimeout(Promise.resolve(client.destroy()), 15_000, "destroy").catch(() => {});
  await withTimeout(Promise.resolve(browser?.close?.()), 10_000, "close").catch(() => {});

  if (pid) {
    // Chrome spawns renderers and GPU helpers; killing the parent alone leaves
    // them holding the profile. /T takes the whole tree.
    const cmd =
      process.platform === "win32"
        ? `taskkill /PID ${pid} /T /F`
        : `kill -9 -${pid} 2>/dev/null || kill -9 ${pid}`;
    await new Promise((resolve) => exec(cmd, () => resolve()));
    log(`Killed browser process tree (pid ${pid})`);
  }

  // Chrome leaves these behind on an unclean exit and refuses to start again.
  for (const name of ["SingletonLock", "SingletonCookie", "SingletonSocket"]) {
    try {
      fs.rmSync(`${SESSION_DIR}/session/${name}`, { force: true });
    } catch {
      /* best effort */
    }
  }
  await new Promise((r) => setTimeout(r, 2000)); // let the OS release the handles
}

/**
 * Rebuild in a brand new process.
 *
 * whatsapp-web.js does not survive re-initialization once its browser has been
 * killed. The socket comes back — getState() reports CONNECTED, the heartbeat
 * looks fine — but `ready` never fires again, so client.info stays empty and
 * not one message is ever delivered. Measured here: an in-process rebuild sat
 * that way for 6+ minutes; a fresh process was ready in seconds.
 *
 * Nothing supervises this process, so it relaunches itself. log() writes
 * straight to the log file, so the replacement keeps logging even detached.
 */
async function relaunch(why) {
  log(`Relaunching in a fresh process (${why})`);

  // Leave nothing behind for the replacement to trip over: a surviving Chrome
  // keeps the lock on .session and the new process dies with "the browser is
  // already running", and a held port 8100 stops it binding at all.
  await killBrowser().catch(() => {});
  await new Promise((done) => (server ? server.close(done) : done()));

  try {
    spawn(process.execPath, [fileURLToPath(import.meta.url)], {
      cwd: process.cwd(),
      env: process.env,
      detached: true,
      stdio: "ignore",
    }).unref();
  } catch (e) {
    log("Relaunch failed — the bridge stays down until it is started again:", e.message);
    return;
  }
  setTimeout(() => process.exit(0), 500);
}

// How long after a rebuild to keep waiting for `ready` before giving up on this
// process. Generous on purpose: a slow sync is normal, and relaunching a
// session that was about to come back costs another two-minute outage.
const READY_GRACE_MS = 120_000;

// When the current attempt to become ready began — process start, or the last
// recovery. Without it the first heartbeat would call a normal, still-syncing
// startup "degraded".
let initStartedAt = Date.now();

async function recover(why) {
  if (recovering) return;
  recovering = true;
  state.recoveries += 1;
  state.me = null; // stale until `ready` fires again — the degraded check reads this
  initStartedAt = Date.now();
  log(`Recovering (${why}) — attempt ${state.recoveries}`);
  try {
    await killBrowser();
    // Loading a ~270 MB session profile and waiting for WhatsApp Web to finish
    // syncing takes well over two minutes on a busy machine. 120s was timing
    // out mid-rebuild and leaving the bridge stuck in "error".
    await withTimeout(client.initialize(), 300_000, "initialize");
    state.lastCheckAt = Date.now();
    state.error = null;
    log("Recovery finished");

    // initialize() resolving is not the same as being usable — `ready` lands a
    // little afterwards (12s, measured here). Wait for it before concluding
    // anything: relaunching on the gap would kill recoveries that were working.
    const readyBy = Date.now() + READY_GRACE_MS;
    while (!state.me && Date.now() < readyBy) {
      await new Promise((r) => setTimeout(r, 2000));
    }
    if (!state.me) return relaunch("ready never fired after recovery");
  } catch (e) {
    state.status = "error";
    state.error = e.message;
    log("Recovery failed:", e.message);
    return relaunch("recovery threw");
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

// NOTE: there is deliberately no "silent for too long, rebuild anyway" check.
// One existed and it was harmful: a quiet half hour is completely normal on a
// WhatsApp account, and rebuilding on that killed healthy sessions — losing
// every message that arrived during the ~2 minute restart.
//
// getState() is the authoritative signal. The failure the staleness check was
// meant to catch (a dead page) already surfaces there as a thrown
// "detached Frame", so the heuristic added risk without adding detection.

// WhatsApp Web passes through these on its own during a normal reconnect.
// Treating them as a fault is how the heartbeat ended up killing healthy
// sessions and causing the very disconnects it was meant to fix.
const TRANSIENT_STATES = new Set(["OPENING", "PAIRING", "TIMEOUT", "UNPAIRED_IDLE"]);

// How many consecutive bad beats before rebuilding. One bad reading is noise;
// three in a row (three minutes) is a real problem.
const STRIKES_BEFORE_RECOVERY = 3;
let strikes = 0;

async function heartbeat() {
  if (recovering || state.status === "qr") return;
  try {
    const reported = await withTimeout(client.getState(), 20_000, "getState");

    if (reported !== "CONNECTED") {
      strikes += 1;
      const transient = TRANSIENT_STATES.has(reported);
      log(
        `Heartbeat: WhatsApp reports ${reported} ` +
          `(strike ${strikes}/${STRIKES_BEFORE_RECOVERY}${transient ? ", transient" : ""})`
      );
      if (strikes < STRIKES_BEFORE_RECOVERY) return; // give it time to settle
      state.status = "disconnected";
      strikes = 0;
      return await recover(`state ${reported}`);
    }

    strikes = 0;
    state.lastCheckAt = Date.now();

    // getState() only proves the socket is up. Until `ready` has fired there is
    // no client.info, no chat list, and no message delivery: whatsapp-web.js
    // reads WhatsApp Web's own internal modules, and those break whenever
    // WhatsApp ships a new web build (the "getChats failed: r: r" in this log).
    // Calling that "connected" is how the bridge sat green for hours while
    // nothing arrived and nothing could be answered.
    if (!state.me && Date.now() - initStartedAt > READY_GRACE_MS) {
      if (state.status !== "degraded") {
        state.status = "degraded";
        state.error =
          "WhatsApp Web is reachable but whatsapp-web.js never became ready — " +
          "usually a WhatsApp web-build change. Messages will not arrive.";
        log(`Degraded: ${state.error}`);
      }
      return;
    }

    if (state.status !== "connected") {
      state.status = "connected";
      state.error = null;
      log("Session healthy again");
    }

  } catch (e) {
    // A thrown getState is a harder signal than a transient state, but still
    // worth one retry — a single slow beat under load is not a dead session.
    strikes += 1;
    log(`Heartbeat failed (strike ${strikes}/2): ${e.message}`);
    if (strikes < 2) return;
    state.status = "disconnected";
    state.error = e.message;
    strikes = 0;
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

  const contact = await msg.getContact().catch((e) => {
    log(`[msg:${source}] getContact failed:`, e);
    return null;
  });
  const from = phoneOf(contact) || msg.from.replace(/@(c\.us|lid)$/, "");

  // A LID is an opaque WhatsApp id, not a phone number. Without a contact to
  // resolve it, the backend gets something no allowlist entry can ever match,
  // and the reply is dropped further downstream with no obvious cause. Say so
  // here, where the reason is still visible.
  if (!phoneOf(contact) && msg.from?.endsWith("@lid")) {
    log(`[msg:${source}] WARNING: could not resolve ${msg.from} to a phone number — ` +
        `forwarding the raw id, which will not match the allowlist`);
  }

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
    const r = await fetch(`${getBackendUrl()}/api/whatsapp/web/incoming`, {
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

app.get("/status", (_req, res) => {
  state.lastCheckAt = Date.now();
  res.json(state);
});

/** QR as an image or HTML page so dashboard or browser can view it cleanly. */
app.get("/qr", (req, res) => {
  state.lastCheckAt = Date.now();
  const acceptsHtml = (req.get("accept") || "").includes("text/html");
  if (!state.qr) {
    if (acceptsHtml) {
      return res.send(`<!DOCTYPE html>
<html><head><title>WhatsApp Bridge QR</title><meta http-equiv="refresh" content="3"></head>
<body style="font-family:sans-serif;text-align:center;padding:50px;">
  <h2>Status: ${state.status}</h2>
  <p>${state.status === "ready" ? `WhatsApp is already connected! (${state.me || ""})` : "Waiting for QR code... page will auto-refresh."}</p>
</body></html>`);
    }
    return res.status(200).json({ status: state.status, qr: null });
  }
  if (acceptsHtml) {
    return res.send(`<!DOCTYPE html>
<html><head><title>Scan WhatsApp QR</title></head>
<body style="font-family:sans-serif;text-align:center;padding:40px;">
  <h2>Scan with WhatsApp</h2>
  <p>Open WhatsApp &rarr; Settings &rarr; Linked Devices &rarr; Link a device</p>
  <img src="${state.qr}" alt="WhatsApp QR Code" style="border:1px solid #ddd;padding:10px;border-radius:8px;"/>
</body></html>`);
  }
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

/**
 * Clear anything left over from a previous run before the first launch.
 *
 * A hard kill (Ctrl+C, a tool timeout, a crash) leaves Chrome running and the
 * profile locked. The next start then fails with "browser is already running"
 * and the bridge sits there receiving nothing.
 */
function clearStaleSession() {
  for (const name of ["SingletonLock", "SingletonCookie", "SingletonSocket"]) {
    try {
      fs.rmSync(`${SESSION_DIR}/session/${name}`, { force: true });
    } catch {
      /* best effort */
    }
  }
  if (process.platform === "win32") {
    // Only Chrome instances pointed at OUR profile — never the user's browser.
    const dir = fs.realpathSync(SESSION_DIR).replace(/\\/g, "\\\\");
    exec(
      `wmic process where "name='chrome.exe' and commandline like '%${dir}%'" get processid`,
      (err, out) => {
        const pids = (out || "").match(/\d+/g) || [];
        for (const pid of pids) exec(`taskkill /PID ${pid} /T /F`);
        if (pids.length) log(`Cleared ${pids.length} stale browser process(es)`);
      }
    );
  }
}

clearStaleSession();

client.initialize().catch((e) => {
  state.status = "error";
  state.error = e.message;
  console.error("Failed to start:", e);
});
