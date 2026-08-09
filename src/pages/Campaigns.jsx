import { useState } from "react";
import { Megaphone, Plus, Play, Square, X, ChevronDown } from "lucide-react";
import { Card, EmptyState, ErrorNote } from "../ui.jsx";
import { useApi, post } from "../api.js";

const statusStyle = {
  draft: "bg-panel-2 text-dim ring-line",
  running: "bg-mint/10 text-mint ring-mint/25",
  paused: "bg-saffron/10 text-saffron ring-saffron/25",
  done: "bg-violet/15 text-violet-soft ring-violet/30",
};

const targetStyle = {
  pending: "text-dim",
  called: "text-mint",
  failed: "text-rose",
};

function NewCampaign({ agents, onCreated, onCancel }) {
  const [name, setName] = useState("");
  const [numbers, setNumbers] = useState("");
  const [agentId, setAgentId] = useState(agents[0]?.id ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  // Mirrors the backend parser so the count shown matches what gets created.
  const parsed = numbers
    .split("\n")
    .map((l) => l.match(/\+?\d[\d\s-]{7,18}\d/)?.[0].replace(/[\s-]/g, ""))
    .filter(Boolean);
  const unique = new Set(parsed).size;

  async function create() {
    setBusy(true);
    setError(null);
    try {
      await post("/api/campaigns", { name, numbers, agent_id: agentId || null });
      onCreated();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="mt-4">
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium">New campaign</p>
        <button onClick={onCancel} className="text-dim transition hover:text-white">
          <X className="size-4" />
        </button>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <label className="text-xs text-dim">
          Campaign name
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Diwali offer"
            className="mt-1.5 w-full rounded-xl border border-line bg-panel-2 px-3.5 py-2.5 text-sm text-white outline-none focus:border-violet/50"
          />
        </label>
        <label className="text-xs text-dim">
          Agent
          <select
            value={agentId}
            onChange={(e) => setAgentId(e.target.value)}
            className="mt-1.5 w-full rounded-xl border border-line bg-panel-2 px-3.5 py-2.5 text-sm text-white outline-none focus:border-violet/50"
          >
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        </label>
      </div>

      <label className="mt-3 block text-xs text-dim">
        Numbers — one per line. “Name, +91…” also works.
        <textarea
          value={numbers}
          onChange={(e) => setNumbers(e.target.value)}
          rows={7}
          placeholder={"Ramesh, +91 98220 41111\n+919812345678\nNeha Gupta, 9876543210"}
          className="mt-1.5 w-full resize-none rounded-xl border border-line bg-panel-2 p-3.5 font-mono text-sm text-white outline-none focus:border-violet/50"
        />
      </label>
      {numbers && (
        <p className="mt-1.5 text-xs text-dim">
          {unique} number{unique === 1 ? "" : "s"} found
          {parsed.length > unique && ` · ${parsed.length - unique} duplicate dropped`}
        </p>
      )}

      {error && <p className="mt-3 text-xs text-saffron">{error}</p>}

      <button
        onClick={create}
        disabled={busy || !name.trim() || unique === 0}
        className="mt-4 rounded-xl bg-violet px-4 py-2.5 text-sm font-medium text-white transition hover:bg-violet-soft disabled:opacity-40"
      >
        {busy ? "Creating…" : "Create campaign"}
      </button>
    </Card>
  );
}

function Targets({ campaignId }) {
  const { data: targets } = useApi(`/api/campaigns/${campaignId}/targets`, [], 5000);
  return (
    <div className="mt-4 max-h-64 space-y-1 overflow-y-auto border-t border-line pt-3">
      {targets.map((t) => (
        <div key={t.id} className="flex items-center justify-between gap-3 px-1 py-1.5 text-xs">
          <span className="truncate">
            {t.name && <span className="text-white">{t.name} · </span>}
            <span className="font-mono text-dim">{t.phone}</span>
          </span>
          <span className={`shrink-0 ${targetStyle[t.status] ?? "text-dim"}`} title={t.error}>
            {t.status}
          </span>
        </div>
      ))}
    </div>
  );
}

export default function Campaigns() {
  const { data: campaigns, error, reload } = useApi("/api/campaigns", []);
  const { data: agents } = useApi("/api/agents", [], 0);
  const [creating, setCreating] = useState(false);
  const [openId, setOpenId] = useState(null);
  const [note, setNote] = useState(null);

  async function control(id, action) {
    setNote(null);
    try {
      await post(`/api/campaigns/${id}/${action}`, {});
      reload();
    } catch (e) {
      setNote(e.message);
    }
  }

  return (
    <div className="mx-auto max-w-4xl p-6 lg:p-8">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Campaigns</h1>
          <p className="mt-1 text-sm text-dim">
            Give the agent a list. It calls each one and logs the result.
          </p>
        </div>
        <button
          onClick={() => setCreating(true)}
          className="flex items-center gap-2 rounded-xl bg-violet px-4 py-2.5 text-sm font-medium text-white transition hover:bg-violet-soft"
        >
          <Plus className="size-4" /> New campaign
        </button>
      </header>

      {error && (
        <div className="mt-5">
          <ErrorNote error={error} />
        </div>
      )}
      {note && (
        <p className="mt-5 rounded-xl border border-saffron/30 bg-saffron/5 px-4 py-3 text-xs text-saffron">
          {note}
        </p>
      )}

      {creating && (
        <NewCampaign
          agents={agents}
          onCancel={() => setCreating(false)}
          onCreated={() => {
            setCreating(false);
            reload();
          }}
        />
      )}

      <div className="mt-4 space-y-3">
        {campaigns.length === 0 && !error && (
          <Card>
            <EmptyState
              icon={Megaphone}
              title="No campaigns yet"
              sub="Paste a list of numbers and the agent works through it."
            />
          </Card>
        )}

        {campaigns.map((c) => (
          <Card key={c.id}>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <p className="text-sm font-medium">{c.name}</p>
                  <span
                    className={`rounded-full px-2 py-0.5 text-[10px] ring-1 ${statusStyle[c.status] ?? statusStyle.draft}`}
                  >
                    {c.status}
                  </span>
                </div>
                <p className="mt-1 text-xs text-dim">
                  {c.agentName} · {c.called}/{c.total} called
                  {c.failed > 0 && ` · ${c.failed} failed`} · {c.created}
                </p>
              </div>

              <div className="flex shrink-0 items-center gap-2">
                {c.status === "running" ? (
                  <button
                    onClick={() => control(c.id, "stop")}
                    className="flex items-center gap-1.5 rounded-lg border border-line px-3 py-1.5 text-xs text-saffron transition hover:bg-panel-2"
                  >
                    <Square className="size-3" /> Stop
                  </button>
                ) : (
                  c.status !== "done" && (
                    <button
                      onClick={() => control(c.id, "start")}
                      className="flex items-center gap-1.5 rounded-lg bg-mint px-3 py-1.5 text-xs font-medium text-ink transition hover:brightness-110"
                    >
                      <Play className="size-3" /> Start
                    </button>
                  )
                )}
                <button
                  onClick={() => setOpenId(openId === c.id ? null : c.id)}
                  className="rounded-lg border border-line p-1.5 text-dim transition hover:text-white"
                >
                  <ChevronDown
                    className={`size-3.5 transition ${openId === c.id ? "rotate-180" : ""}`}
                  />
                </button>
              </div>
            </div>

            {c.total > 0 && (
              <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-panel-2">
                <div
                  className="h-full rounded-full bg-mint transition-all"
                  style={{ width: `${((c.called + c.failed) / c.total) * 100}%` }}
                />
              </div>
            )}

            {openId === c.id && <Targets campaignId={c.id} />}
          </Card>
        ))}
      </div>
    </div>
  );
}
