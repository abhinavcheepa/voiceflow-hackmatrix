import { CalendarCheck, Trash2, AlertTriangle, Phone, MessageSquare } from "lucide-react";
import { Card, EmptyState } from "../ui.jsx";
import { del, useApi } from "../api.js";

const CHANNEL = {
  call: { icon: Phone, label: "Web call" },
  whatsapp: { icon: MessageSquare, label: "WhatsApp" },
};

export default function Bookings() {
  const { data: rows, reload } = useApi("/api/bookings", [], 8000);

  async function remove(id) {
    await del(`/api/bookings/${id}`);
    reload();
  }

  return (
    <div className="mx-auto max-w-5xl p-6 lg:p-8">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Bookings</h1>
        <p className="mt-1 text-sm text-dim">
          What the agent actually recorded during conversations — not promises, real
          entries. Bookings and escalations land here the moment the agent takes them.
        </p>
      </header>

      <Card className="mt-6 overflow-hidden p-0">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead>
              <tr className="border-b border-line text-xs text-dim">
                {["Item", "Customer", "When", "Channel", "Taken", ""].map((h) => (
                  <th key={h} className="px-5 py-3.5 font-medium">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((b) => {
                const ch = CHANNEL[b.channel] ?? CHANNEL.call;
                const ChIcon = ch.icon;
                return (
                  <tr key={b.id} className="border-b border-line/50 last:border-0 hover:bg-panel-2">
                    <td className="px-5 py-4">
                      <div className="flex items-center gap-2.5">
                        {b.escalated ? (
                          <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-saffron/10 ring-1 ring-saffron/25">
                            <AlertTriangle className="size-4 text-saffron" />
                          </span>
                        ) : (
                          <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-violet/10 ring-1 ring-violet/25">
                            <CalendarCheck className="size-4 text-violet-soft" />
                          </span>
                        )}
                        <div>
                          <p className="font-medium">{b.item}</p>
                          {b.notes && <p className="text-xs text-dim">{b.notes}</p>}
                        </div>
                      </div>
                    </td>
                    <td className="px-5 py-4">
                      <p>{b.name}</p>
                      {b.phone !== "—" && <p className="text-xs text-dim">{b.phone}</p>}
                    </td>
                    <td className="px-5 py-4 text-dim">{b.when}</td>
                    <td className="px-5 py-4">
                      <span className="inline-flex items-center gap-1.5 text-xs text-dim">
                        <ChIcon className="size-3.5" /> {ch.label}
                      </span>
                    </td>
                    <td className="px-5 py-4 text-dim">{b.time}</td>
                    <td className="px-5 py-4 text-right">
                      <button
                        onClick={() => remove(b.id)}
                        className="rounded-lg border border-line px-2 py-1.5 text-dim transition hover:text-rose"
                        aria-label="Delete booking"
                      >
                        <Trash2 className="size-3.5" />
                      </button>
                    </td>
                  </tr>
                );
              })}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={6}>
                    <EmptyState
                      icon={CalendarCheck}
                      title="No bookings yet"
                      sub="When a caller books or orders, the agent records it here automatically."
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
