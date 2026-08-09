import { useState } from "react";
import { Users, Search, Phone, MessageSquare, X } from "lucide-react";
import { Card, EmptyState, ErrorNote } from "../ui.jsx";
import { useApi, put } from "../api.js";

const STATUSES = ["New", "Contacted", "Qualified", "Customer", "Lost"];

const statusStyle = {
  New: "bg-panel-2 text-dim ring-line",
  Contacted: "bg-violet/15 text-violet-soft ring-violet/30",
  Qualified: "bg-saffron/10 text-saffron ring-saffron/25",
  Customer: "bg-mint/10 text-mint ring-mint/25",
  Lost: "bg-rose/10 text-rose ring-rose/25",
};

function Detail({ id, onClose, onChanged }) {
  const { data: contact, reload } = useApi(`/api/contacts/${id}`, null, 0);
  const [saving, setSaving] = useState(false);

  if (!contact) return null;

  async function setStatus(status) {
    setSaving(true);
    await put(`/api/contacts/${id}`, { status });
    await reload();
    onChanged();
    setSaving(false);
  }

  return (
    <Card className="mt-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-sm font-medium">{contact.name}</p>
          <p className="mt-0.5 text-xs text-dim">{contact.phone}</p>
        </div>
        <button onClick={onClose} className="text-dim transition hover:text-white">
          <X className="size-4" />
        </button>
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        {STATUSES.map((s) => (
          <button
            key={s}
            onClick={() => setStatus(s)}
            disabled={saving}
            className={`rounded-full px-3 py-1.5 text-xs ring-1 transition ${
              contact.status === s
                ? statusStyle[s]
                : "border-line bg-panel-2 text-dim ring-line hover:text-white"
            }`}
          >
            {s}
          </button>
        ))}
      </div>

      <p className="mt-6 text-xs text-dim">
        Everything this customer did — calls and WhatsApp in one thread
      </p>
      <div className="mt-3 space-y-2">
        {contact.timeline.length === 0 && (
          <p className="py-6 text-center text-xs text-dim">Nothing yet.</p>
        )}
        {contact.timeline.map((t, i) => (
          <div
            key={i}
            className="flex items-start gap-3 rounded-xl border border-line bg-panel-2 p-3"
          >
            <span className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-lg bg-panel ring-1 ring-line">
              {t.kind === "call" ? (
                <Phone className="size-3.5 text-violet-soft" />
              ) : (
                <MessageSquare className="size-3.5 text-mint" />
              )}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex items-baseline justify-between gap-2">
                <p className="truncate text-sm">{t.title}</p>
                <span className="shrink-0 text-[10px] text-dim">{t.when}</span>
              </div>
              <p className="mt-0.5 truncate text-xs text-dim">{t.detail}</p>
            </div>
            <span className="shrink-0 self-center rounded-full bg-panel px-2 py-0.5 text-[10px] text-dim ring-1 ring-line">
              {t.outcome}
            </span>
          </div>
        ))}
      </div>
    </Card>
  );
}

export default function Contacts() {
  const { data: contacts, error, reload } = useApi("/api/contacts", []);
  const [q, setQ] = useState("");
  const [openId, setOpenId] = useState(null);

  const needle = q.trim().toLowerCase();
  const rows = contacts.filter(
    (c) => !needle || `${c.name} ${c.phone} ${c.status}`.toLowerCase().includes(needle),
  );

  return (
    <div className="mx-auto max-w-5xl p-6 lg:p-8">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Contacts</h1>
        <p className="mt-1 text-sm text-dim">
          Built automatically from every call and message — nothing to enter by hand.
        </p>
      </header>

      {error && (
        <div className="mt-5">
          <ErrorNote error={error} />
        </div>
      )}

      <label className="mt-7 flex items-center gap-2.5 rounded-xl border border-line bg-panel px-3.5 py-2.5 focus-within:border-violet/50 sm:max-w-xs">
        <Search className="size-4 shrink-0 text-dim" />
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search name, number, status…"
          className="w-full bg-transparent text-sm outline-none placeholder:text-dim"
        />
      </label>

      {openId && (
        <Detail id={openId} onClose={() => setOpenId(null)} onChanged={reload} />
      )}

      <Card className="mt-4 overflow-x-auto p-0">
        <table className="w-full min-w-[640px] text-sm whitespace-nowrap">
          <thead>
            <tr className="border-b border-line text-left text-xs text-dim">
              {["Contact", "Status", "Calls", "Messages", "Last seen"].map((h) => (
                <th key={h} className="px-5 py-3.5 font-medium">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => (
              <tr
                key={c.id}
                onClick={() => setOpenId(c.id)}
                className="cursor-pointer border-b border-line/50 last:border-0 hover:bg-panel-2"
              >
                <td className="px-5 py-4">
                  <p className="font-medium">{c.name}</p>
                  <p className="text-xs text-dim">{c.phone}</p>
                </td>
                <td className="px-5 py-4">
                  <span
                    className={`rounded-full px-2.5 py-1 text-xs ring-1 ${statusStyle[c.status] ?? statusStyle.New}`}
                  >
                    {c.status}
                  </span>
                </td>
                <td className="px-5 py-4 text-dim">{c.calls}</td>
                <td className="px-5 py-4 text-dim">{c.messages}</td>
                <td className="px-5 py-4 text-dim">{c.lastSeen}</td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={5}>
                  <EmptyState
                    icon={Users}
                    title={contacts.length === 0 ? "No contacts yet" : "No contacts match"}
                    sub={
                      contacts.length === 0
                        ? "A contact is created the first time someone calls or messages."
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
