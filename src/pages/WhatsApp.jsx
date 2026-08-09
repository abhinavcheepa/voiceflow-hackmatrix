import { useState } from "react";
import { Mic, Send, Play } from "lucide-react";
import { Waveform } from "../ui.jsx";
import { conversations } from "../data.js";

function Bubble({ m }) {
  const mine = m.from === "us";
  return (
    <div className={`flex ${mine ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[78%] rounded-2xl px-3.5 py-2.5 text-sm ${
          mine
            ? "rounded-tr-sm bg-violet/20 ring-1 ring-violet/30"
            : "rounded-tl-sm bg-panel-2 ring-1 ring-line"
        }`}
      >
        {m.type === "voice" && (
          <div className="mb-2 flex items-center gap-2.5">
            <Play className="size-3.5 shrink-0 text-violet-soft" />
            <Waveform bars={14} active={false} className="h-4" />
            <span className="shrink-0 text-xs text-dim">0:{String(m.seconds).padStart(2, "0")}</span>
          </div>
        )}
        <p className={m.type === "voice" ? "text-dim italic" : ""}>{m.text}</p>
        <p className="mt-1 text-right text-[10px] text-dim">
          {m.time}
          {mine && m.type === "voice" && " · cloned voice"}
        </p>
      </div>
    </div>
  );
}

export default function WhatsApp() {
  const [activeId, setActiveId] = useState(conversations[0].id);
  const active = conversations.find((c) => c.id === activeId);

  return (
    <div className="flex h-screen flex-col md:flex-row">
      <div className="shrink-0 overflow-y-auto border-b border-line md:h-full md:w-80 md:border-r md:border-b-0">
        <div className="border-b border-line p-5">
          <h1 className="text-lg font-semibold tracking-tight">WhatsApp</h1>
          <p className="mt-1 text-xs text-dim">Auto-replied by your agent, in your style</p>
        </div>
        {conversations.map((c) => (
          <button
            key={c.id}
            onClick={() => setActiveId(c.id)}
            className={`flex w-full items-start gap-3 border-b border-line/50 p-4 text-left transition ${
              c.id === activeId ? "bg-violet/10" : "hover:bg-panel"
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
        <div className="flex items-center justify-between border-b border-line px-5 py-4">
          <div>
            <p className="text-sm font-medium">{active.name}</p>
            <p className="text-xs text-dim">
              {active.phone} · replying in {active.language}
            </p>
          </div>
          <span className="flex items-center gap-1.5 rounded-full bg-mint/10 px-3 py-1.5 text-xs text-mint ring-1 ring-mint/25">
            <span className="size-1.5 rounded-full bg-mint" /> Auto-reply on
          </span>
        </div>

        <div className="flex-1 space-y-3 overflow-y-auto p-5">
          {active.messages.map((m, i) => (
            <Bubble key={i} m={m} />
          ))}
        </div>

        {/* Composer is display-only until the backend lands. */}
        <div className="flex items-center gap-3 border-t border-line p-4">
          <input
            disabled
            placeholder="The agent replies automatically — type to take over"
            className="flex-1 rounded-xl border border-line bg-panel px-4 py-2.5 text-sm outline-none placeholder:text-dim"
          />
          <button className="grid size-10 shrink-0 place-items-center rounded-xl border border-line bg-panel text-dim transition hover:text-white">
            <Mic className="size-4" />
          </button>
          <button className="grid size-10 shrink-0 place-items-center rounded-xl bg-violet text-white transition hover:bg-violet-soft">
            <Send className="size-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
