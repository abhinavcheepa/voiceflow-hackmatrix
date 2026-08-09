import { useState } from "react";
import { Search, PhoneIncoming } from "lucide-react";
import { Card, EmptyState, ErrorNote } from "../ui.jsx";
import { OutcomeBadge } from "./Dashboard.jsx";
import { useApi } from "../api.js";

const filters = ["All", "Booked", "Lead captured", "Answered", "Escalated", "Missed"];

export default function Calls() {
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState("All");
  const { data: calls, error } = useApi("/api/calls?limit=200", []);

  const needle = q.trim().toLowerCase();
  const rows = calls.filter(
    (c) =>
      (filter === "All" || c.outcome === filter) &&
      (needle === "" ||
        `${c.name} ${c.phone} ${c.intent} ${c.language}`.toLowerCase().includes(needle)),
  );

  return (
    <div className="mx-auto max-w-6xl p-6 lg:p-8">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Calls</h1>
        <p className="mt-1 text-sm text-dim">
          Every inbound call, transcribed and logged automatically.
        </p>
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
