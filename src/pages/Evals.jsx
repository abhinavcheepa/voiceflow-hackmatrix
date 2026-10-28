import { useState } from "react";
import { FlaskConical, Play, Trash2, Loader2, Plus, X, Check } from "lucide-react";
import { Card, EmptyState, ErrorNote } from "../ui.jsx";
import { API, useApi, post } from "../api.js";

const field =
  "mt-1.5 w-full rounded-xl border border-line bg-panel-2 px-3.5 py-2.5 text-sm text-white outline-none focus:border-violet/50";

function NewTest({ agents, onCreated, onClose }) {
  const [form, setForm] = useState({
    name: "",
    agent_id: "",
    mode: "script",
    turns: "",
    persona: "",
    max_turns: 4,
    rubric: "",
  });
  const [error, setError] = useState(null);
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  async function save() {
    setError(null);
    try {
      await post("/api/evals", {
        ...form,
        agent_id: form.agent_id ? Number(form.agent_id) : null,
        max_turns: Number(form.max_turns),
      });
      onCreated();
    } catch (e) {
      setError(e.message);
    }
  }

  return (
    <Card className="mt-6">
      <div className="flex items-center justify-between">
        <h2 className="text-[15px] font-semibold">New test</h2>
        <button onClick={onClose} aria-label="Close" className="text-dim hover:text-white">
          <X className="size-4" />
        </button>
      </div>
      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <label className="text-xs text-dim">
          Name
          <input value={form.name} onChange={set("name")} placeholder="Books a study table" className={field} />
        </label>
        <label className="text-xs text-dim">
          Agent
          <select value={form.agent_id} onChange={set("agent_id")} className={field}>
            <option value="">Default agent</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>{a.name}</option>
            ))}
          </select>
        </label>
      </div>

      <div className="mt-4 flex gap-2">
        {[
          ["script", "Scripted caller"],
          ["simulate", "AI caller (simulation)"],
        ].map(([v, label]) => (
          <button
            key={v}
            onClick={() => setForm((f) => ({ ...f, mode: v }))}
            className={`rounded-full px-3.5 py-2 text-xs transition ${
              form.mode === v ? "bg-violet text-white" : "border border-line bg-panel text-dim hover:text-white"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {form.mode === "script" ? (
        <label className="mt-4 block text-xs text-dim">
          What the caller says — one line per turn
          <textarea
            value={form.turns}
            onChange={set("turns")}
            rows={4}
            placeholder={"Mujhe study table chahiye\nKal shaam 5 baje\nMera naam Ravi hai"}
            className={field}
          />
        </label>
      ) : (
        <div className="mt-4 grid gap-4 sm:grid-cols-[1fr_8rem]">
          <label className="text-xs text-dim">
            Who the AI caller is and what they want
            <textarea
              value={form.persona}
              onChange={set("persona")}
              rows={3}
              placeholder="Ravi, speaks Hinglish, wants a study table delivered tomorrow, a bit impatient about price."
              className={field}
            />
          </label>
          <label className="text-xs text-dim">
            Max turns
            <input type="number" min={1} max={8} value={form.max_turns} onChange={set("max_turns")} className={field} />
          </label>
        </div>
      )}

      <label className="mt-4 block text-xs text-dim">
        Passes when…
        <textarea
          value={form.rubric}
          onChange={set("rubric")}
          rows={2}
          placeholder="The agent books the study table (book_appointment) and never invents a price."
          className={field}
        />
      </label>

      {error && <p className="mt-3 text-xs text-saffron">{error}</p>}
      <button
        onClick={save}
        className="mt-4 rounded-xl bg-violet px-4 py-2.5 text-sm font-medium text-white transition hover:bg-violet-soft"
      >
        Save test
      </button>
    </Card>
  );
}

function Verdict({ run }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] ring-1 ${
        run.passed ? "bg-mint/10 text-mint ring-mint/25" : "bg-rose/10 text-rose ring-rose/25"
      }`}
    >
      {run.passed ? <Check className="size-3" /> : <X className="size-3" />}
      {run.passed ? "Pass" : "Fail"}
    </span>
  );
}

export default function Evals() {
  const { data: cases, error, reload } = useApi("/api/evals", [], 0);
  const { data: agents } = useApi("/api/agents", [], 0);
  const [adding, setAdding] = useState(false);
  const [running, setRunning] = useState({});
  const [runError, setRunError] = useState(null);
  const [shown, setShown] = useState(null);

  async function run(id) {
    setRunning((r) => ({ ...r, [id]: true }));
    setRunError(null);
    try {
      await post(`/api/evals/${id}/run`, {});
      setShown(id);
      reload();
    } catch (e) {
      setRunError(e.message);
    } finally {
      setRunning((r) => ({ ...r, [id]: false }));
    }
  }

  async function runAll() {
    for (const c of cases) await run(c.id); // one at a time: the model budget is per minute
  }

  async function remove(id) {
    await fetch(`${API}/api/evals/${id}`, { method: "DELETE" });
    reload();
  }

  const agentName = (id) => agents.find((a) => a.id === id)?.name ?? "Default agent";
  const busy = Object.values(running).some(Boolean);

  return (
    <div className="mx-auto max-w-5xl p-6 lg:p-8">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Tests</h1>
          <p className="mt-1 max-w-2xl text-sm text-dim">
            Test conversations run against your agent before real customers do. Tools run dry —
            a test never creates a real booking or bill.
          </p>
        </div>
        <div className="flex gap-2">
          {cases.length > 0 && (
            <button
              onClick={runAll}
              disabled={busy}
              className="flex items-center gap-2 rounded-xl border border-line px-4 py-2.5 text-sm transition hover:border-violet/50 disabled:opacity-40"
            >
              <Play className="size-4" /> Run all
            </button>
          )}
          <button
            onClick={() => setAdding(true)}
            className="flex items-center gap-2 rounded-xl bg-violet px-4 py-2.5 text-sm font-medium text-white transition hover:bg-violet-soft"
          >
            <Plus className="size-4" /> New test
          </button>
        </div>
      </header>

      {error && <div className="mt-5"><ErrorNote error={error} /></div>}
      {runError && <p className="mt-4 text-xs text-saffron">{runError}</p>}

      {adding && (
        <NewTest
          agents={agents}
          onClose={() => setAdding(false)}
          onCreated={() => {
            setAdding(false);
            reload();
          }}
        />
      )}

      <div className="mt-6 space-y-3">
        {cases.map((c) => (
          <Card key={c.id}>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="font-medium">{c.name}</p>
                <p className="mt-0.5 text-xs text-dim">
                  {agentName(c.agentId)} · {c.mode === "script" ? "Scripted" : `AI caller, up to ${c.maxTurns} turns`}
                </p>
                <p className="mt-2 text-xs text-dim">Passes when: {c.rubric}</p>
              </div>
              <div className="flex items-center gap-2">
                {c.lastRun && <Verdict run={c.lastRun} />}
                <button
                  onClick={() => run(c.id)}
                  disabled={busy}
                  className="flex items-center gap-1.5 rounded-xl bg-violet px-3 py-2 text-xs font-medium text-white transition hover:bg-violet-soft disabled:opacity-40"
                >
                  {running[c.id] ? <Loader2 className="size-3.5 animate-spin" /> : <Play className="size-3.5" />}
                  {running[c.id] ? "Running…" : "Run"}
                </button>
                <button onClick={() => remove(c.id)} aria-label="Delete test" className="p-2 text-dim hover:text-rose">
                  <Trash2 className="size-4" />
                </button>
              </div>
            </div>

            {c.lastRun && (
              <div className="mt-3 border-t border-line pt-3">
                <button
                  onClick={() => setShown(shown === c.id ? null : c.id)}
                  className="text-xs text-violet-soft hover:underline"
                >
                  {shown === c.id ? "Hide" : "Show"} last run ({c.lastRun.time})
                </button>
                {shown === c.id && (
                  <div className="mt-2 space-y-2 text-xs">
                    <p><span className="text-dim">Judge:</span> {c.lastRun.reason}</p>
                    {c.lastRun.tools.length > 0 && (
                      <p className="text-dim">
                        Tools: {c.lastRun.tools.map((t) => t.name).join(", ")}
                      </p>
                    )}
                    <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-xl bg-panel-2 p-3 font-sans text-[12px] leading-relaxed">
                      {c.lastRun.transcript}
                    </pre>
                  </div>
                )}
              </div>
            )}
          </Card>
        ))}
        {cases.length === 0 && !adding && (
          <EmptyState
            icon={FlaskConical}
            title="No tests yet"
            sub="Write what a caller says (or describe an AI caller), say what a pass looks like, and run it."
          />
        )}
      </div>
    </div>
  );
}
