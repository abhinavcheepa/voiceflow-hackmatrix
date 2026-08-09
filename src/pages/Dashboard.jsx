import { Phone, Clock, TrendingUp, MessageSquare, PhoneIncoming } from "lucide-react";
import { Card, EmptyState, ErrorNote } from "../ui.jsx";
import { useApi } from "../api.js";

const ZERO_STATS = {
  totalCalls: 0,
  avgDuration: "0m 00s",
  successRate: 0,
  whatsappResponseRate: 0,
};

const tilesFor = (s) => [
  { icon: Phone, label: "Total Calls", value: s.totalCalls.toLocaleString("en-IN") },
  { icon: Clock, label: "Avg. Call Duration", value: s.avgDuration },
  { icon: TrendingUp, label: "Success Rate", value: `${s.successRate}%` },
  { icon: MessageSquare, label: "WhatsApp Response Rate", value: `${s.whatsappResponseRate}%` },
];

const outcomeStyles = {
  Booked: "bg-mint/10 text-mint ring-mint/25",
  "Lead captured": "bg-violet/15 text-violet-soft ring-violet/30",
  Answered: "bg-panel-2 text-dim ring-line",
  Escalated: "bg-saffron/10 text-saffron ring-saffron/25",
  Missed: "bg-rose/10 text-rose ring-rose/25",
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

function CallVolume({ callsByHour }) {
  const peak = Math.max(...callsByHour.map((h) => h.calls));
  const peakHour = callsByHour.find((h) => h.calls === peak)?.hour;

  return (
    <Card className="lg:col-span-2">
      <div className="flex items-baseline justify-between">
        <h2 className="text-[15px] font-semibold">Calls answered today</h2>
        {callsByHour.length > 0 && (
          <span className="text-xs text-dim">
            peak {peak} @ {peakHour}:00
          </span>
        )}
      </div>
      {callsByHour.length === 0 ? (
        <EmptyState title="No calls yet" sub="The chart fills in as calls come through." />
      ) : (
        <>
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
        </>
      )}
    </Card>
  );
}

function LanguageMix({ languages }) {
  return (
    <Card>
      <h2 className="text-[15px] font-semibold">Languages handled</h2>
      <p className="mt-1 text-xs text-dim">Detected automatically, answered in kind</p>
      {languages.length === 0 ? (
        <EmptyState title="Nothing detected yet" />
      ) : (
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
      )}
    </Card>
  );
}

/** One chip per integration, so a missing key is visible instead of mysterious. */
function SetupStatus({ health }) {
  if (!health) return null;
  const parts = [
    ["Calls", health.vapi],
    ["WhatsApp", health.whatsapp],
    ["Voice clone", health.voiceCloned],
    ["Replies", health.brain],
  ];
  if (parts.every(([, on]) => on)) return null;

  return (
    <Card className="mt-4 border-saffron/25 bg-saffron/5">
      <p className="text-[13px] font-medium text-saffron">Setup incomplete</p>
      <p className="mt-1 text-xs text-dim">
        Add the missing keys to <code className="font-mono">backend/.env</code> — see the README.
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        {parts.map(([label, on]) => (
          <span
            key={label}
            className={`rounded-full px-2.5 py-1 text-xs ring-1 ${
              on ? "bg-mint/10 text-mint ring-mint/25" : "bg-panel-2 text-dim ring-line"
            }`}
          >
            {on ? "✓" : "○"} {label}
          </span>
        ))}
      </div>
    </Card>
  );
}

export default function Dashboard() {
  const { data: stats, error } = useApi("/api/stats", ZERO_STATS);
  const { data: callsByHour } = useApi("/api/calls-by-hour", []);
  const { data: languages } = useApi("/api/languages", []);
  const { data: calls } = useApi("/api/calls?limit=5", []);
  const { data: health } = useApi("/health", null, 30000);

  return (
    <div className="mx-auto max-w-6xl p-6 lg:p-8">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
        <p className="mt-1 text-sm text-dim">
          Every call and WhatsApp conversation your agent handled — live.
        </p>
      </header>

      {error && (
        <div className="mt-5">
          <ErrorNote error={error} />
        </div>
      )}

      <div className="mt-7 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {tilesFor(stats).map((t) => (
          <Card key={t.label}>
            <t.icon className="size-[18px] text-violet-soft" />
            <p className="mt-4 text-xs text-dim">{t.label}</p>
            <p className="mt-1 text-3xl font-semibold tracking-tight">{t.value}</p>
          </Card>
        ))}
      </div>

      <SetupStatus health={health} />

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <CallVolume callsByHour={callsByHour} />
        <LanguageMix languages={languages} />
      </div>

      <Card className="mt-4">
        <h2 className="text-[15px] font-semibold">Recent calls</h2>
        {calls.length === 0 ? (
          <EmptyState
            icon={PhoneIncoming}
            title="No calls yet"
            sub="Calls appear here the moment your agent answers one."
          />
        ) : (
          <div className="mt-4 space-y-1">
            {calls.map((c) => (
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
        )}
      </Card>
    </div>
  );
}
