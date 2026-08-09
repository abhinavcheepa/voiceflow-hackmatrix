import { useState } from "react";
import { Search, PhoneIncoming, PhoneOutgoing, Loader2, X } from "lucide-react";
import { Card, EmptyState, ErrorNote } from "../ui.jsx";
import { OutcomeBadge } from "./Dashboard.jsx";
import { useApi, post } from "../api.js";

const filters = ["All", "Booked", "Lead captured", "Answered", "Escalated", "Missed"];

/** E.164-ish: optional +, 8–15 digits. Spaces and dashes are stripped first. */
const normalise = (s) => s.replace(/[\s-()]/g, "");
const isValidNumber = (s) => /^\+?\d{8,15}$/.test(normalise(s));

function NewCall({ onPlaced }) {
  const [open, setOpen] = useState(false);
  const [number, setNumber] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);

  async function place() {
    setBusy(true);
    setResult(null);
    try {
      await post("/api/calls/outbound", { number: normalise(number) });
      setResult({ ok: true, text: `Calling ${number}…` });
      setNumber("");
      onPlaced();
    } catch (e) {
      setResult({ ok: false, text: e.message });
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="flex items-center gap-2 rounded-xl bg-violet px-4 py-2.5 text-sm font-medium text-white transition hover:bg-violet-soft"
      >
        <PhoneOutgoing className="size-4" /> New call
      </button>
    );
  }

  return (
    <Card className="w-full sm:w-96">
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium">Place a call</p>
        <button onClick={() => setOpen(false)} className="text-dim transition hover:text-white">
          <X className="size-4" />
        </button>
      </div>
      <p className="mt-1 text-xs text-dim">
        The agent dials and handles the conversation on its own.
      </p>
      <div className="mt-4 flex gap-2">
        <input
          value={number}
          onChange={(e) => setNumber(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && isValidNumber(number) && place()}
          placeholder="+91 98765 43210"
          inputMode="tel"
          autoFocus
          className="flex-1 rounded-xl border border-line bg-panel-2 px-3.5 py-2.5 text-sm outline-none focus:border-violet/50"
        />
        <button
          onClick={place}
          disabled={busy || !isValidNumber(number)}
          className="flex items-center gap-2 rounded-xl bg-violet px-4 py-2.5 text-sm font-medium text-white transition hover:bg-violet-soft disabled:opacity-40"
        >
          {busy ? <Loader2 className="size-4 animate-spin" /> : <PhoneOutgoing className="size-4" />}
          Call
        </button>
      </div>
      {number && !isValidNumber(number) && (
        <p className="mt-2 text-xs text-dim">Enter 8–15 digits, optionally starting with +.</p>
      )}
      {result && (
        <p className={`mt-3 text-xs ${result.ok ? "text-mint" : "text-saffron"}`}>{result.text}</p>
      )}
    </Card>
  );
}

export default function Calls() {
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState("All");
  const { data: calls, error, reload } = useApi("/api/calls?limit=200", []);

  const needle = q.trim().toLowerCase();
  const rows = calls.filter(
    (c) =>
      (filter === "All" || c.outcome === filter) &&
      (needle === "" ||
        `${c.name} ${c.phone} ${c.intent} ${c.language}`.toLowerCase().includes(needle)),
  );

  return (
    <div className="mx-auto max-w-6xl p-6 lg:p-8">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Calls</h1>
          <p className="mt-1 text-sm text-dim">
            Every call, inbound and outbound, transcribed and logged automatically.
          </p>
        </div>
        <NewCall onPlaced={reload} />
      </header>

      {error && (
        <div className="mt-5">
          <ErrorNote error={error} />
        </div>
      )}

      <div className="mt-7 flex flex-wrap items-center gap-3">
        <label className="flex flex-1 items-center gap-2.5 rounded-xl border border-line bg-panel px-3.5 py-2.5 focus-within:border-violet/50 sm:max-w-xs">
          <Search className="size-4 shrink-0 text-dim" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search name, number, intent…"
            className="w-full bg-transparent text-sm outline-none placeholder:text-dim"
          />
        </label>
        <div className="flex flex-wrap gap-2">
          {filters.map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`rounded-full px-3.5 py-2 text-xs transition ${
                filter === f
                  ? "bg-violet text-white"
                  : "border border-line bg-panel text-dim hover:text-white"
              }`}
            >
              {f}
            </button>
          ))}
        </div>
      </div>

      <Card className="mt-4 overflow-x-auto p-0">
        <table className="w-full min-w-[820px] text-sm whitespace-nowrap">
          <thead>
            <tr className="border-b border-line text-left text-xs text-dim">
              {["Caller", "Language", "Intent", "Duration", "Outcome", "When"].map((h) => (
                <th key={h} className="px-5 py-3.5 font-medium">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.id} className="border-b border-line/50 last:border-0 hover:bg-panel-2">
                <td className="px-5 py-4">
                  <div className="flex items-center gap-3">
                    <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-violet/10 ring-1 ring-violet/25">
                      <PhoneIncoming className="size-4 text-violet-soft" />
                    </span>
                    <div>
                      <p className="font-medium">{c.name}</p>
                      <p className="text-xs text-dim">{c.phone}</p>
                    </div>
                  </div>
                </td>
                <td className="px-5 py-4 text-dim">{c.language}</td>
                <td className="px-5 py-4">{c.intent}</td>
                <td className="px-5 py-4 text-dim">{c.duration}</td>
                <td className="px-5 py-4">
                  <OutcomeBadge outcome={c.outcome} />
                </td>
                <td className="px-5 py-4 text-dim">{c.time}</td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={6}>
                  <EmptyState
                    icon={PhoneIncoming}
                    title={calls.length === 0 ? "No calls yet" : "No calls match that filter"}
                    sub={
                      calls.length === 0
                        ? "Once your number is connected, every answered call lands here with its transcript and outcome."
                        : undefined
                    }
                  />
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
