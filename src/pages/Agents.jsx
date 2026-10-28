import { useState } from "react";
import { Bot, FileText, Plus, Star, Trash2, Upload, X } from "lucide-react";
import { Card, EmptyState, ErrorNote } from "../ui.jsx";
import { API, useApi, post, put } from "../api.js";

const BLANK = {
  name: "",
  prompt: "",
  facts: "",
  greeting: "Namaste! Main aapki kya madad kar sakti hoon?",
  language: "Hindi",
  is_default: false,
};

const LANGUAGES = ["Hindi", "Hinglish", "English", "Marathi", "Tamil"];

function AgentForm({ agent, onSaved, onCancel }) {
  const [form, setForm] = useState(agent ? { ...agent, is_default: agent.isDefault } : BLANK);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  async function save() {
    setBusy(true);
    setError(null);
    const body = {
      name: form.name,
      prompt: form.prompt,
      facts: form.facts,
      greeting: form.greeting,
      language: form.language,
      is_default: form.is_default,
    };
    try {
      await (agent ? put(`/api/agents/${agent.id}`, body) : post("/api/agents", body));
      onSaved();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="mt-4">
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium">{agent ? `Edit ${agent.name}` : "New agent"}</p>
        <button onClick={onCancel} className="text-dim transition hover:text-white">
          <X className="size-4" />
        </button>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <label className="text-xs text-dim">
          Name
          <input
            value={form.name}
            onChange={set("name")}
            placeholder="Clinic reception"
            className="mt-1.5 w-full rounded-xl border border-line bg-panel-2 px-3.5 py-2.5 text-sm text-white outline-none focus:border-violet/50"
          />
        </label>
        <label className="text-xs text-dim">
          Primary language
          <select
            value={form.language}
            onChange={set("language")}
            className="mt-1.5 w-full rounded-xl border border-line bg-panel-2 px-3.5 py-2.5 text-sm text-white outline-none focus:border-violet/50"
          >
            {LANGUAGES.map((l) => (
              <option key={l}>{l}</option>
            ))}
          </select>
        </label>
      </div>

      <label className="mt-3 block text-xs text-dim">
        Greeting — the first thing a caller hears
        <input
          value={form.greeting}
          onChange={set("greeting")}
          className="mt-1.5 w-full rounded-xl border border-line bg-panel-2 px-3.5 py-2.5 text-sm text-white outline-none focus:border-violet/50"
        />
      </label>

      <label className="mt-3 block text-xs text-dim">
        Instructions — how this agent behaves
        <textarea
          value={form.prompt}
          onChange={set("prompt")}
          rows={5}
          placeholder="You book dental appointments. Be warm and brief. Confirm the patient's name and preferred time."
          className="mt-1.5 w-full resize-none rounded-xl border border-line bg-panel-2 p-3.5 text-sm text-white outline-none focus:border-violet/50"
        />
      </label>

      <label className="mt-3 block text-xs text-dim">
        Business facts — the only prices, timings and items it may quote
        <textarea
          value={form.facts}
          onChange={set("facts")}
          rows={5}
          placeholder={"Study table: ₹4,800\nOffice chair: ₹1,200\nOpen: Mon-Sat, 10am-7pm\nAddress: MG Road, Indore\nDelivery: free within city, 3-4 days"}
          className="mt-1.5 w-full resize-none rounded-xl border border-line bg-panel-2 p-3.5 text-sm text-white outline-none focus:border-violet/50"
        />
        <span className="mt-1 block text-[11px] text-dim/70">
          Leave blank and the agent will refuse to quote any price instead of
          inventing one. Anything here is the only pricing it will state.
        </span>
      </label>

      <label className="mt-3 flex items-center gap-2 text-xs text-dim">
        <input
          type="checkbox"
          checked={form.is_default}
          onChange={(e) => setForm({ ...form, is_default: e.target.checked })}
          className="size-4 accent-violet"
        />
        Use this agent by default for new calls
      </label>

      {error && <p className="mt-3 text-xs text-saffron">{error}</p>}

      <button
        onClick={save}
        disabled={busy || !form.name.trim() || !form.prompt.trim()}
        className="mt-4 rounded-xl bg-violet px-4 py-2.5 text-sm font-medium text-white transition hover:bg-violet-soft disabled:opacity-40"
      >
        {busy ? "Saving…" : "Save agent"}
      </button>
    </Card>
  );
}

function KnowledgeBase() {
  const { data: docs, reload } = useApi("/api/kb", [], 0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function upload(file) {
    if (!file) return;
    setBusy(true);
    setError(null);
    const body = new FormData();
    body.append("file", file);
    try {
      const r = await fetch(`${API}/api/kb`, { method: "POST", body });
      if (!r.ok) throw new Error((await r.json()).detail || "Upload failed");
      reload();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function remove(id) {
    await fetch(`${API}/api/kb/${id}`, { method: "DELETE" });
    reload();
  }

  return (
    <Card className="mt-6">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-[15px] font-semibold">Knowledge base</h2>
          <p className="mt-1 text-xs text-dim">
            Upload a price list, catalogue or FAQ. Every agent answers customer
            questions from these files — and quotes their figures instead of
            guessing.
          </p>
        </div>
        <label className="flex shrink-0 cursor-pointer items-center gap-2 rounded-xl bg-violet px-4 py-2.5 text-sm font-medium text-white transition hover:bg-violet-soft">
          <Upload className="size-4" />
          {busy ? "Uploading…" : "Upload file"}
          <input
            type="file"
            className="hidden"
            accept=".pdf,.txt,.md,.csv,.tsv,.json,.yaml,.yml"
            disabled={busy}
            onChange={(e) => {
              upload(e.target.files[0]);
              e.target.value = "";
            }}
          />
        </label>
      </div>

      {error && (
        <p className="mt-3 rounded-xl border border-rose/30 bg-rose/5 px-4 py-3 text-xs text-rose">
          {error}
        </p>
      )}

      <div className="mt-4 space-y-2">
        {docs.length === 0 && (
          <p className="text-xs text-dim/70">
            No documents yet. PDF, TXT, MD, CSV, JSON or YAML — up to 2 MB.
          </p>
        )}
        {docs.map((d) => (
          <div
            key={d.id}
            className="flex items-center justify-between gap-3 rounded-xl border border-line bg-panel-2 px-3.5 py-2.5"
          >
            <div className="flex min-w-0 items-center gap-2.5">
              <FileText className="size-4 shrink-0 text-violet-soft" />
              <span className="truncate text-sm">{d.filename}</span>
              <span className="shrink-0 rounded-full bg-panel px-2 py-0.5 text-[10px] text-dim ring-1 ring-line">
                {d.chunks} chunk{d.chunks === 1 ? "" : "s"}
              </span>
            </div>
            <button
              onClick={() => remove(d.id)}
              className="shrink-0 rounded-lg border border-line px-2 py-1.5 text-dim transition hover:text-rose"
              aria-label={`Delete ${d.filename}`}
            >
              <Trash2 className="size-3.5" />
            </button>
          </div>
        ))}
      </div>
    </Card>
  );
}

export default function Agents() {
  const { data: agents, error, reload } = useApi("/api/agents", [], 0);
  const [editing, setEditing] = useState(null); // null | "new" | agent

  async function remove(agent) {
    if (!confirm(`Delete "${agent.name}"?`)) return;
    const r = await fetch(`${API}/api/agents/${agent.id}`, { method: "DELETE" });
    if (!r.ok) alert((await r.json()).detail);
    reload();
  }

  return (
    <div className="mx-auto max-w-4xl p-6 lg:p-8">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Agents</h1>
          <p className="mt-1 text-sm text-dim">
            Different personas for different jobs — bookings, sales, support.
          </p>
        </div>
        <button
          onClick={() => setEditing("new")}
          className="flex items-center gap-2 rounded-xl bg-violet px-4 py-2.5 text-sm font-medium text-white transition hover:bg-violet-soft"
        >
          <Plus className="size-4" /> New agent
        </button>
      </header>

      {error && (
        <div className="mt-5">
          <ErrorNote error={error} />
        </div>
      )}

      {editing && (
        <AgentForm
          agent={editing === "new" ? null : editing}
          onCancel={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            reload();
          }}
        />
      )}

      <div className="mt-4 space-y-3">
        {agents.length === 0 && !error && (
          <Card>
            <EmptyState icon={Bot} title="No agents yet" sub="Create one to answer calls." />
          </Card>
        )}
        {agents.map((a) => (
          <Card key={a.id}>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <p className="text-sm font-medium">{a.name}</p>
                  {a.isDefault && (
                    <span className="flex items-center gap-1 rounded-full bg-mint/10 px-2 py-0.5 text-[10px] text-mint ring-1 ring-mint/25">
                      <Star className="size-2.5" /> default
                    </span>
                  )}
                  <span className="rounded-full bg-panel-2 px-2 py-0.5 text-[10px] text-dim ring-1 ring-line">
                    {a.language}
                  </span>
                </div>
                <p className="mt-2 text-xs text-dim">“{a.greeting}”</p>
                <p className="mt-2 line-clamp-2 text-xs text-dim/70">{a.prompt}</p>
              </div>
              <div className="flex shrink-0 gap-2">
                <button
                  onClick={() => setEditing(a)}
                  className="rounded-lg border border-line px-3 py-1.5 text-xs text-dim transition hover:text-white"
                >
                  Edit
                </button>
                <button
                  onClick={() => remove(a)}
                  className="rounded-lg border border-line px-2 py-1.5 text-dim transition hover:text-rose"
                >
                  <Trash2 className="size-3.5" />
                </button>
              </div>
            </div>
          </Card>
        ))}
      </div>

      <KnowledgeBase />
    </div>
  );
}
