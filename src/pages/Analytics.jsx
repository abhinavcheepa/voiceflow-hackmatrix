import { useState } from "react";
import { BarChart3 } from "lucide-react";
import { Card, EmptyState, ErrorNote } from "../ui.jsx";
import { useApi } from "../api.js";

const RANGES = [7, 30, 90];

const outcomeColor = {
  Booked: "#2ee6a8",
  "Lead captured": "#7c5cff",
  Answered: "#8e8ea8",
  Escalated: "#ff8a3d",
  Missed: "#ff5c7a",
};

const shortDay = (iso) =>
  new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short" });

/** Bar chart with a shared scale, so two series stay comparable. */
function Bars({ data, valueOf, labelOf, color = "#7c5cff", title, sub }) {
  const peak = Math.max(...data.map(valueOf), 1);
  return (
    <Card>
      <h2 className="text-[15px] font-semibold">{title}</h2>
      {sub && <p className="mt-1 text-xs text-dim">{sub}</p>}
      {data.length === 0 ? (
        <EmptyState title="Nothing in this range yet" />
      ) : (
        <>
          <div className="mt-6 flex h-36 items-end gap-1">
            {data.map((d, i) => (
              <div
                key={i}
                title={`${labelOf(d)}: ${valueOf(d)}`}
                className="flex-1 rounded-t transition hover:opacity-80"
                style={{
                  height: `${Math.max((valueOf(d) / peak) * 100, 2)}%`,
                  background: color,
                }}
              />
            ))}
          </div>
          <div className="mt-2 flex justify-between text-[10px] text-dim">
            <span>{labelOf(data[0])}</span>
            <span>{labelOf(data[data.length - 1])}</span>
          </div>
        </>
      )}
    </Card>
  );
}

export default function Analytics() {
  const [days, setDays] = useState(30);
  const { data, error } = useApi(`/api/analytics?days=${days}`, null);

  const daily = data?.daily ?? [];
  const outcomes = data?.outcomes ?? [];
  const byAgent = data?.byAgent ?? [];
  const whatsapp = data?.whatsapp ?? [];

  const totalCalls = daily.reduce((a, d) => a + d.calls, 0);
  const totalOutcomes = outcomes.reduce((a, o) => a + o.count, 0) || 1;
  const inbound = whatsapp.reduce((a, d) => a + d.inbound, 0);
  const replies = whatsapp.reduce((a, d) => a + d.replies, 0);

  return (
    <div className="mx-auto max-w-6xl p-6 lg:p-8">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Analytics</h1>
          <p className="mt-1 text-sm text-dim">
            How the agent is actually performing, over time.
          </p>
        </div>
        <div className="flex gap-2">
          {RANGES.map((d) => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={`rounded-full px-3.5 py-2 text-xs transition ${
                days === d
                  ? "bg-violet text-white"
                  : "border border-line bg-panel text-dim hover:text-white"
              }`}
            >
              {d} days
            </button>
          ))}
        </div>
      </header>

      {error && (
        <div className="mt-5">
          <ErrorNote error={error} />
        </div>
      )}

      <div className="mt-7 grid gap-4 sm:grid-cols-3">
        {[
          ["Calls", totalCalls],
          ["WhatsApp messages in", inbound],
          ["Auto-replies sent", replies],
        ].map(([label, value]) => (
          <Card key={label}>
            <p className="text-xs text-dim">{label}</p>
            <p className="mt-1 text-3xl font-semibold tracking-tight">
              {value.toLocaleString("en-IN")}
            </p>
          </Card>
        ))}
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Bars
          data={daily}
          valueOf={(d) => d.calls}
          labelOf={(d) => shortDay(d.day)}
          title="Calls per day"
          sub={`Last ${days} days`}
        />
        <Bars
          data={whatsapp}
          valueOf={(d) => d.inbound}
          labelOf={(d) => shortDay(d.day)}
          color="#2ee6a8"
          title="WhatsApp messages per day"
          sub="Incoming from customers"
        />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card>
          <h2 className="text-[15px] font-semibold">Outcomes</h2>
          <p className="mt-1 text-xs text-dim">Where calls actually ended up</p>
          {outcomes.length === 0 ? (
            <EmptyState title="No calls in this range" />
          ) : (
            <div className="mt-6 space-y-4">
              {outcomes.map((o) => (
                <div key={o.outcome}>
                  <div className="flex justify-between text-sm">
                    <span>{o.outcome}</span>
                    <span className="text-dim">
                      {o.count} · {Math.round((o.count / totalOutcomes) * 100)}%
                    </span>
                  </div>
                  <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-panel-2">
                    <div
                      className="h-full rounded-full"
                      style={{
                        width: `${(o.count / totalOutcomes) * 100}%`,
                        background: outcomeColor[o.outcome] ?? "#8e8ea8",
                      }}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card>
          <h2 className="text-[15px] font-semibold">By agent</h2>
          <p className="mt-1 text-xs text-dim">Which persona handled what</p>
          {byAgent.length === 0 ? (
            <EmptyState title="No calls in this range" />
          ) : (
            <div className="mt-5 space-y-1">
              {byAgent.map((a) => (
                <div
                  key={a.agent}
                  className="flex items-center justify-between rounded-xl px-2 py-3 hover:bg-panel-2"
                >
                  <p className="truncate text-sm">{a.agent}</p>
                  <div className="flex shrink-0 items-center gap-4 text-xs text-dim">
                    <span>{a.avgDuration} avg</span>
                    <span className="text-white">{a.calls} calls</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      {totalCalls === 0 && inbound === 0 && !error && (
        <Card className="mt-4">
          <EmptyState
            icon={BarChart3}
            title="No activity yet"
            sub="Charts fill in as calls and messages come through."
          />
        </Card>
      )}
    </div>
  );
}
