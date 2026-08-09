import { Phone, Clock, TrendingUp, MessageSquare } from "lucide-react";
import { Card } from "../ui.jsx";
import { stats, callsByHour, languages, calls } from "../data.js";

const tiles = [
  { icon: Phone, label: "Total Calls", value: stats.totalCalls.toLocaleString("en-IN"), delta: "+12.4% vs last week" },
  { icon: Clock, label: "Avg. Call Duration", value: stats.avgDuration, delta: "−8s vs last week" },
  { icon: TrendingUp, label: "Success Rate", value: `${stats.successRate}%`, delta: "+1.8 pts" },
  {
    icon: MessageSquare,
    label: "WhatsApp Response Rate",
    value: `${stats.whatsappResponseRate}%`,
    delta: "avg reply in 3s",
  },
];

const outcomeStyles = {
  Booked: "bg-mint/10 text-mint ring-mint/25",
  "Lead captured": "bg-violet/15 text-violet-soft ring-violet/30",
  Answered: "bg-panel-2 text-dim ring-line",
  Escalated: "bg-saffron/10 text-saffron ring-saffron/25",
};

export function OutcomeBadge({ outcome }) {
  return (
    <span
      className={`rounded-full px-2.5 py-1 text-xs whitespace-nowrap ring-1 ${outcomeStyles[outcome] ?? outcomeStyles.Answered}`}
    >
      {outcome}
    </span>
  );
}

function CallVolume() {
  const peak = Math.max(...callsByHour.map((h) => h.calls));
  return (
    <Card className="lg:col-span-2">
      <div className="flex items-baseline justify-between">
        <h2 className="text-[15px] font-semibold">Calls answered today</h2>
        <span className="text-xs text-dim">peak {peak} @ 17:00</span>
      </div>
      {/* Bar heights are percentages, so the track needs a definite height. */}
      <div className="mt-6 flex h-40 items-end gap-2">
        {callsByHour.map((h) => (
          <div
            key={h.hour}
            title={`${h.calls} calls at ${h.hour}:00`}
            className="flex-1 rounded-t-md bg-gradient-to-t from-violet/30 to-violet transition hover:to-violet-soft"
            style={{ height: `${(h.calls / peak) * 100}%` }}
          />
        ))}
      </div>
      <div className="mt-2 flex gap-2">
        {callsByHour.map((h) => (
          <span key={h.hour} className="flex-1 text-center text-[10px] text-dim">
            {h.hour}
          </span>
        ))}
      </div>
    </Card>
  );
}

function LanguageMix() {
  return (
    <Card>
      <h2 className="text-[15px] font-semibold">Languages handled</h2>
      <p className="mt-1 text-xs text-dim">Detected by Faster-Whisper, answered in kind</p>
      <div className="mt-6 space-y-4">
        {languages.map((l) => (
          <div key={l.name}>
            <div className="flex justify-between text-sm">
              <span>{l.name}</span>
              <span className="text-dim">{l.share}%</span>
            </div>
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-panel-2">
              <div
                className="h-full rounded-full"
                style={{ width: `${l.share}%`, background: l.color }}
              />
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}

export default function Dashboard() {
  return (
    <div className="mx-auto max-w-6xl p-6 lg:p-8">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
        <p className="mt-1 text-sm text-dim">
          Every call and WhatsApp conversation your agent handled — live.
        </p>
      </header>

      <div className="mt-7 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {tiles.map((t) => (
          <Card key={t.label}>
            <t.icon className="size-[18px] text-violet-soft" />
            <p className="mt-4 text-xs text-dim">{t.label}</p>
            <p className="mt-1 text-3xl font-semibold tracking-tight">{t.value}</p>
            <p className="mt-2 text-xs text-mint">{t.delta}</p>
          </Card>
        ))}
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <CallVolume />
        <LanguageMix />
      </div>

      <Card className="mt-4">
        <h2 className="text-[15px] font-semibold">Recent calls</h2>
        <div className="mt-4 space-y-1">
          {calls.slice(0, 5).map((c) => (
            <div
              key={c.id}
              className="flex items-center justify-between gap-4 rounded-xl px-2 py-3 transition hover:bg-panel-2"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-medium">{c.name}</p>
                <p className="truncate text-xs text-dim">
                  {c.intent} · {c.language}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-4">
                <span className="hidden text-xs text-dim sm:block">{c.duration}</span>
                <OutcomeBadge outcome={c.outcome} />
                <span className="hidden w-16 text-right text-xs text-dim md:block">{c.time}</span>
              </div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
