import { Brain, Check, X, Trash2, Sparkles, MessageSquare, Bot } from "lucide-react";
import { Card, EmptyState } from "../ui.jsx";
import { API, useApi, put } from "../api.js";

const SOURCE_STYLE = {
  conversation: { icon: MessageSquare, label: "From call", color: "text-violet-soft" },
  manual: { icon: Bot, label: "Manual", color: "text-mint" },
  ai_generated: { icon: Sparkles, label: "AI generated", color: "text-saffron" },
};

const STATUS_STYLE = {
  pending: "bg-saffron/10 text-saffron ring-saffron/25",
  approved: "bg-mint/10 text-mint ring-mint/25",
  rejected: "bg-rose/10 text-rose ring-rose/25",
};

export default function Learning() {
  const { data: items, reload } = useApi("/api/learned", [], 8000);

  const pending = items.filter((i) => i.status === "pending");
  const approved = items.filter((i) => i.status === "approved");
  const rejected = items.filter((i) => i.status === "rejected");

  async function setStatus(id, status) {
    await put(`/api/learned/${id}`, { status });
    reload();
  }

  async function remove(id) {
    await fetch(`${API}/api/learned/${id}`, { method: "DELETE" });
    reload();
  }

  return (
    <div className="mx-auto max-w-5xl p-6 lg:p-8">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">
          <span className="inline-flex items-center gap-2.5">
            <span className="grid size-9 place-items-center rounded-xl bg-violet/15 ring-1 ring-violet/40">
              <Brain className="size-5 text-violet-soft" />
            </span>
            Adaptive Learning
          </span>
        </h1>
        <p className="mt-2 text-sm text-dim">
          The agent learns from every conversation. Approve what it learned to make it smarter,
          or reject incorrect learnings. Only approved answers influence future replies.
        </p>
      </header>

      {/* Stats */}
      <div className="mt-5 grid grid-cols-3 gap-3">
        <Card className="text-center">
          <p className="text-2xl font-semibold text-saffron">{pending.length}</p>
          <p className="mt-1 text-xs text-dim">Pending Review</p>
        </Card>
        <Card className="text-center">
          <p className="text-2xl font-semibold text-mint">{approved.length}</p>
          <p className="mt-1 text-xs text-dim">Approved</p>
        </Card>
        <Card className="text-center">
          <p className="text-2xl font-semibold text-dim">{rejected.length}</p>
          <p className="mt-1 text-xs text-dim">Rejected</p>
        </Card>
      </div>

      {/* Pending section */}
      {pending.length > 0 && (
        <div className="mt-6">
          <h2 className="text-sm font-semibold text-saffron flex items-center gap-2">
            <Sparkles className="size-4" /> Needs Your Review ({pending.length})
          </h2>
          <div className="mt-3 space-y-3">
            {pending.map((item) => (
              <LearningCard key={item.id} item={item} onApprove={() => setStatus(item.id, "approved")}
                onReject={() => setStatus(item.id, "rejected")} onDelete={() => remove(item.id)} />
            ))}
          </div>
        </div>
      )}

      {/* All items table */}
      <Card className="mt-6 overflow-hidden p-0">
        <div className="px-5 py-3.5 border-b border-line">
          <p className="text-sm font-medium">All Learned Answers</p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead>
              <tr className="border-b border-line text-xs text-dim">
                {["Question", "Answer", "Source", "Confidence", "Used", "Status", ""].map((h) => (
                  <th key={h} className="px-5 py-3 font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {items.map((item) => {
                const src = SOURCE_STYLE[item.source] || SOURCE_STYLE.conversation;
                const SrcIcon = src.icon;
                return (
                  <tr key={item.id} className="border-b border-line/50 last:border-0 hover:bg-panel-2">
                    <td className="px-5 py-3.5 max-w-[200px]">
                      <p className="truncate font-medium">{item.question}</p>
                    </td>
                    <td className="px-5 py-3.5 max-w-[200px]">
                      <p className="truncate text-dim">{item.answer}</p>
                    </td>
                    <td className="px-5 py-3.5">
                      <span className={`inline-flex items-center gap-1 text-xs ${src.color}`}>
                        <SrcIcon className="size-3" /> {src.label}
                      </span>
                    </td>
                    <td className="px-5 py-3.5">
                      <div className="flex items-center gap-2">
                        <div className="h-1.5 w-16 rounded-full bg-panel-2">
                          <div
                            className="h-full rounded-full bg-violet-soft"
                            style={{ width: `${item.confidence * 100}%` }}
                          />
                        </div>
                        <span className="text-xs text-dim">{(item.confidence * 100).toFixed(0)}%</span>
                      </div>
                    </td>
                    <td className="px-5 py-3.5 text-dim">{item.useCount}×</td>
                    <td className="px-5 py-3.5">
                      <span className={`inline-block rounded-full px-2 py-0.5 text-xs ring-1 ${STATUS_STYLE[item.status] || STATUS_STYLE.pending}`}>
                        {item.status}
                      </span>
                    </td>
                    <td className="px-5 py-3.5 text-right">
                      <div className="flex justify-end gap-1">
                        {item.status === "pending" && (
                          <>
                            <button onClick={() => setStatus(item.id, "approved")}
                              className="rounded-lg border border-line px-2 py-1 text-dim hover:text-mint" title="Approve">
                              <Check className="size-3.5" />
                            </button>
                            <button onClick={() => setStatus(item.id, "rejected")}
                              className="rounded-lg border border-line px-2 py-1 text-dim hover:text-rose" title="Reject">
                              <X className="size-3.5" />
                            </button>
                          </>
                        )}
                        <button onClick={() => remove(item.id)}
                          className="rounded-lg border border-line px-2 py-1 text-dim hover:text-rose" title="Delete">
                          <Trash2 className="size-3.5" />
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
              {items.length === 0 && (
                <tr>
                  <td colSpan={7}>
                    <EmptyState
                      icon={Brain}
                      title="No learned answers yet"
                      sub="After each call, the agent will extract Q&A pairs for you to review here."
                    />
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

function LearningCard({ item, onApprove, onReject, onDelete }) {
  const src = SOURCE_STYLE[item.source] || SOURCE_STYLE.conversation;
  const SrcIcon = src.icon;

  return (
    <div className="rounded-xl border border-saffron/20 bg-panel p-4 transition hover:border-saffron/40">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0">
          <p className="text-xs text-dim flex items-center gap-1.5 mb-2">
            <SrcIcon className={`size-3 ${src.color}`} /> {src.label} ·
            Confidence: {(item.confidence * 100).toFixed(0)}% · {item.time}
          </p>
          <p className="text-sm font-medium">❓ {item.question}</p>
          <p className="mt-1.5 text-sm text-dim">💬 {item.answer}</p>
        </div>
        <div className="flex gap-1.5 shrink-0">
          <button onClick={onApprove}
            className="flex items-center gap-1.5 rounded-lg bg-mint/10 px-3 py-1.5 text-xs text-mint ring-1 ring-mint/25 transition hover:bg-mint/20">
            <Check className="size-3.5" /> Approve
          </button>
          <button onClick={onReject}
            className="flex items-center gap-1.5 rounded-lg bg-rose/10 px-3 py-1.5 text-xs text-rose ring-1 ring-rose/25 transition hover:bg-rose/20">
            <X className="size-3.5" /> Reject
          </button>
          <button onClick={onDelete}
            className="rounded-lg border border-line px-2 py-1.5 text-dim hover:text-rose">
            <Trash2 className="size-3.5" />
          </button>
        </div>
      </div>
    </div>
  );
}
