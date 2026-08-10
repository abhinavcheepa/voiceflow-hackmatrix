import { useEffect, useState } from "react";
import {
  Mic,
  Send,
  Play,
  MessageSquare,
  AlertTriangle,
  QrCode,
  LogOut,
  RefreshCw,
} from "lucide-react";
import { Card, EmptyState, ErrorNote, Waveform } from "../ui.jsx";
import { useApi, post, put } from "../api.js";

/** Small switch. `on` drives the colour; the label sits outside. */
function Toggle({ on, onChange, disabled, title }) {
  return (
    <button
      role="switch"
      aria-checked={on}
      title={title}
      disabled={disabled}
      onClick={() => onChange(!on)}
      className={`relative h-5 w-9 shrink-0 rounded-full transition disabled:opacity-40 ${
        on ? "bg-mint" : "bg-panel-2 ring-1 ring-line"
      }`}
    >
      <span
        className={`absolute top-0.5 size-4 rounded-full bg-white transition-all ${
          on ? "left-[1.125rem]" : "left-0.5"
        }`}
      />
    </button>
  );
}

const LINK_TEXT = {
  starting: "Bridge starting…",
  qr: "Scan to connect",
  connected: "Connected",
  disconnected: "Disconnected — restart the bridge",
  auth_failure: "WhatsApp rejected the login",
  offline: "Bridge not running",
};

/**
 * Shown until a number is linked. Only relevant for the WhatsApp Web provider —
 * the Meta Cloud API has no QR step.
 */
function LinkPanel({ link, onChanged }) {
  if (!link || link.provider !== "web" || link.status === "connected") return null;

  return (
    <div className="border-b border-line p-5">
      <p className="flex items-center gap-2 text-sm font-medium">
        <QrCode className="size-4 text-violet-soft" />
        {LINK_TEXT[link.status] ?? link.status}
      </p>

      {link.qr ? (
        <>
          <img
            src={link.qr}
            alt="WhatsApp QR code"
            className="mt-4 w-full max-w-[200px] rounded-xl bg-white p-2"
          />
          <p className="mt-3 text-xs leading-relaxed text-dim">
            WhatsApp → Settings → Linked devices → Link a device
          </p>
        </>
      ) : (
        <p className="mt-2 text-xs leading-relaxed text-dim">
          {link.status === "offline" ? (
            <>
              Start it with <code className="font-mono">npm start</code> in{" "}
              <code className="font-mono">wa-bridge/</code>.
            </>
          ) : (
            link.error || "Waiting for the bridge…"
          )}
        </p>
      )}

      <p className="mt-3 rounded-lg border border-saffron/25 bg-saffron/5 px-3 py-2 text-[11px] leading-relaxed text-saffron">
        Use a dedicated number. Driving WhatsApp Web is against WhatsApp's terms
        and numbers can be banned.
      </p>
      {link.status === "connected" && (
        <button
          onClick={() => post("/api/whatsapp/logout", {}).then(onChanged)}
          className="mt-3 flex items-center gap-1.5 text-xs text-dim transition hover:text-white"
        >
          <LogOut className="size-3" /> Unlink
        </button>
      )}
    </div>
  );
}

function Bubble({ m }) {
  const mine = m.from === "us";
  return (
    <div className={`flex ${mine ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[78%] rounded-2xl px-3.5 py-2.5 text-sm ${
          m.failed
            ? "rounded-tr-sm bg-saffron/10 ring-1 ring-saffron/30"
            : mine
              ? "rounded-tr-sm bg-violet/20 ring-1 ring-violet/30"
              : "rounded-tl-sm bg-panel-2 ring-1 ring-line"
        }`}
      >
        {m.type === "voice" && !m.failed && (
          <div className="mb-2 flex items-center gap-2.5">
            <Play className="size-3.5 shrink-0 text-violet-soft" />
            <Waveform bars={14} active={false} className="h-4" />
            <span className="shrink-0 text-xs text-dim">
              0:{String(m.seconds ?? 0).padStart(2, "0")}
            </span>
          </div>
        )}
        <p
          className={
            m.failed
              ? "flex items-start gap-2 text-saffron"
              : m.type === "voice"
                ? "text-dim italic"
                : ""
          }
        >
          {m.failed && <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />}
          {m.text}
        </p>
        <p className="mt-1 text-right text-[10px] text-dim">
          {m.time}
          {mine && m.type === "voice" && !m.failed && " · cloned voice"}
        </p>
      </div>
    </div>
  );
}

export default function WhatsApp() {
  const { data: conversations, error, reload } = useApi("/api/conversations", [], 5000);
  const { data: link, reload: reloadLink } = useApi("/api/whatsapp/link", null, 5000);
  const { data: autoReply, reload: reloadAuto } = useApi("/api/whatsapp/auto-reply", null, 0);
  const [activeId, setActiveId] = useState(null);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [importing, setImporting] = useState(false);
  const [imported, setImported] = useState(null);
  const [allowlist, setAllowlist] = useState("");

  useEffect(() => {
    if (autoReply?.allowlist) setAllowlist(autoReply.allowlist.join("\n"));
  }, [autoReply?.allowlist]);

  /** History only — the backend stores these without running the agent. */
  async function importChats() {
    setImporting(true);
    setImported(null);
    try {
      const r = await post("/api/whatsapp/sync", { chats: 25, messages: 15 });
      setImported({ ok: true, text: `Imported ${r.messages} messages from ${r.threads} chats` });
      reload();
    } catch (e) {
      setImported({ ok: false, text: e.message });
    } finally {
      setImporting(false);
    }
  }

  const active = conversations.find((c) => c.id === activeId) ?? conversations[0];

  async function send() {
    if (!draft.trim() || !active) return;
    setSending(true);
    try {
      await post("/api/whatsapp/send", { to: active.phone, text: draft.trim() });
      setDraft("");
      reload();
    } catch (e) {
      alert(`Could not send: ${e.message}`);
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="flex h-screen flex-col md:flex-row">
      <div className="shrink-0 overflow-y-auto border-b border-line md:h-full md:w-80 md:border-r md:border-b-0">
        <div className="border-b border-line p-5">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <h1 className="text-lg font-semibold tracking-tight">WhatsApp</h1>
              <p className="mt-1 truncate text-xs text-dim">
                {link?.status === "connected" && link.number
                  ? `Connected as ${link.number}`
                  : "Auto-replied by your agent, in your style"}
              </p>
            </div>
            {link?.status === "connected" && (
              <button
                onClick={importChats}
                disabled={importing}
                title="Pull your existing WhatsApp chats into the dashboard"
                className="shrink-0 rounded-lg border border-line px-2.5 py-1.5 text-dim transition hover:text-white disabled:opacity-40"
              >
                <RefreshCw className={`size-3.5 ${importing ? "animate-spin" : ""}`} />
              </button>
            )}
          </div>
          {imported && (
            <p
              className={`mt-2 text-[11px] leading-relaxed ${
                imported.ok ? "text-mint" : "text-saffron"
              }`}
            >
              {imported.text}
            </p>
          )}

          {/* Master switch. Off means nobody gets an automatic answer. */}
          <div className="mt-4 flex items-center justify-between gap-3 rounded-xl border border-line bg-panel-2 px-3 py-2.5">
            <div className="min-w-0">
              <p className="text-xs font-medium">Auto-reply</p>
              <p className="mt-0.5 text-[11px] leading-tight text-dim">
                {autoReply?.enabled
                  ? "Agent answers every new message"
                  : "Messages arrive but go unanswered"}
              </p>
            </div>
            <Toggle
              on={!!autoReply?.enabled}
              onChange={(v) => put("/api/whatsapp/auto-reply", { enabled: v }).then(reloadAuto)}
              title="Turn the agent on or off for every conversation"
            />
          </div>

          {/* Essential when the agent runs on a number that also carries
              personal chats — otherwise friends get answered by a bot. */}
          <div className="mt-2 rounded-xl border border-line bg-panel-2 px-3 py-2.5">
            <p className="text-xs font-medium">Only reply to these numbers</p>
            <p className="mt-0.5 text-[11px] leading-tight text-dim">
              {allowlist.trim()
                ? "Everyone else is stored but not answered."
                : "Empty = the agent answers everyone who messages this number."}
            </p>
            <textarea
              value={allowlist}
              onChange={(e) => setAllowlist(e.target.value)}
              onBlur={() => put("/api/whatsapp/allowlist", { numbers: allowlist }).then(reloadAuto)}
              rows={2}
              placeholder={"919812345678\n919876543210"}
              className="mt-2 w-full resize-none rounded-lg border border-line bg-panel px-2.5 py-2 font-mono text-[11px] outline-none focus:border-violet/50"
            />
          </div>
        </div>
        <LinkPanel link={link} onChanged={reloadLink} />
        {conversations.map((c) => (
          <button
            key={c.id}
            onClick={() => setActiveId(c.id)}
            className={`flex w-full items-start gap-3 border-b border-line/50 p-4 text-left transition ${
              c.id === active?.id ? "bg-violet/10" : "hover:bg-panel"
            }`}
          >
            <span className="grid size-10 shrink-0 place-items-center rounded-full bg-panel-2 text-sm font-medium ring-1 ring-line">
              {c.name[0]}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex items-baseline justify-between gap-2">
                <p className="truncate text-sm font-medium">{c.name}</p>
                <span className="shrink-0 text-[10px] text-dim">{c.time}</span>
              </div>
              <p className="mt-0.5 truncate text-xs text-dim">{c.last}</p>
              <span className="mt-1.5 inline-block rounded-full bg-panel-2 px-2 py-0.5 text-[10px] text-dim ring-1 ring-line">
                {c.language}
              </span>
            </div>
            {c.unread > 0 && (
              <span className="mt-1 grid size-5 shrink-0 place-items-center rounded-full bg-mint text-[10px] font-semibold text-ink">
                {c.unread}
              </span>
            )}
          </button>
        ))}
      </div>

      <div className="flex min-h-0 flex-1 flex-col">
        {!active ? (
          <div className="grid flex-1 place-items-center p-6">
            {error ? (
              <ErrorNote error={error} />
            ) : (
              <EmptyState
                icon={MessageSquare}
                title="No conversations yet"
                sub="Once your WhatsApp number is connected, incoming messages appear here and the agent replies on its own."
              />
            )}
          </div>
        ) : (
          <>
            <div className="flex items-center justify-between border-b border-line px-5 py-4">
              <div>
                <p className="text-sm font-medium">{active.name}</p>
                <p className="text-xs text-dim">
                  {active.phone} · replying in {active.language}
                </p>
              </div>
              {/* Per-thread override, so one chat can be handled by hand
                  without switching the agent off for everyone. */}
              <div className="flex shrink-0 items-center gap-2.5">
                <span
                  className={`text-xs ${
                    autoReply?.enabled && active.autoReply ? "text-mint" : "text-dim"
                  }`}
                >
                  {!autoReply?.enabled
                    ? "Agent off"
                    : active.autoReply
                      ? "Agent replying"
                      : "You reply"}
                </span>
                <Toggle
                  on={!!active.autoReply}
                  disabled={!autoReply?.enabled}
                  onChange={(v) =>
                    put(`/api/conversations/${active.phone}/auto-reply`, { enabled: v }).then(reload)
                  }
                  title={
                    autoReply?.enabled
                      ? "Let the agent answer this chat, or take it over yourself"
                      : "Auto-reply is off for every chat"
                  }
                />
              </div>
            </div>

            <div className="flex-1 space-y-3 overflow-y-auto p-5">
              {active.messages.map((m, i) => (
                <Bubble key={i} m={m} />
              ))}
            </div>

            {/* The agent replies on its own; this is the owner taking over. */}
            <div className="flex items-center gap-3 border-t border-line p-4">
              <input
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && send()}
                placeholder="The agent replies automatically — type to take over"
                className="flex-1 rounded-xl border border-line bg-panel px-4 py-2.5 text-sm outline-none placeholder:text-dim focus:border-violet/50"
              />
              <button
                disabled
                title="Voice replies are sent automatically when the customer sends a voice note"
                className="grid size-10 shrink-0 place-items-center rounded-xl border border-line bg-panel text-dim opacity-40"
              >
                <Mic className="size-4" />
              </button>
              <button
                onClick={send}
                disabled={sending || !draft.trim()}
                className="grid size-10 shrink-0 place-items-center rounded-xl bg-violet text-white transition hover:bg-violet-soft disabled:opacity-40"
              >
                <Send className="size-4" />
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
