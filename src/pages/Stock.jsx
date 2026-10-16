import { useState } from "react";
import {
  Package, Plus, Trash2, AlertTriangle, ArrowDownCircle,
  ArrowUpCircle, RefreshCw, Search, X,
} from "lucide-react";
import { Card, EmptyState } from "../ui.jsx";
import { API, useApi, post } from "../api.js";

export default function Stock() {
  const { data: items, reload } = useApi("/api/stock", [], 8000);
  const [showAdd, setShowAdd] = useState(false);
  const [detail, setDetail] = useState(null);
  const [search, setSearch] = useState("");

  const filtered = search
    ? items.filter((i) => i.name.toLowerCase().includes(search.toLowerCase()))
    : items;

  const lowCount = items.filter((i) => i.lowStock).length;

  async function remove(id) {
    await fetch(`${API}/api/stock/${id}`, { method: "DELETE" });
    reload();
  }

  return (
    <div className="mx-auto max-w-6xl p-6 lg:p-8">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Stock Management</h1>
          <p className="mt-1 text-sm text-dim">
            Track inventory, get low-stock alerts, and let the agent manage stock via voice.
          </p>
        </div>
        <div className="flex gap-2">
          {lowCount > 0 && (
            <span className="flex items-center gap-1.5 rounded-lg bg-saffron/10 px-3 py-2 text-xs text-saffron ring-1 ring-saffron/25">
              <AlertTriangle className="size-3.5" /> {lowCount} low stock
            </span>
          )}
          <button
            onClick={() => setShowAdd(true)}
            className="flex items-center gap-2 rounded-xl bg-violet px-4 py-2 text-sm font-medium text-white transition hover:bg-violet/80"
          >
            <Plus className="size-4" /> Add Item
          </button>
        </div>
      </header>

      {/* Search bar */}
      <div className="mt-4 flex items-center gap-2 rounded-xl border border-line bg-panel-2 px-3 py-2">
        <Search className="size-4 text-dim" />
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search stock items..."
          className="flex-1 bg-transparent text-sm outline-none placeholder:text-dim/50"
        />
        {search && (
          <button onClick={() => setSearch("")} className="text-dim hover:text-white">
            <X className="size-4" />
          </button>
        )}
      </div>

      {/* Stock table */}
      <Card className="mt-4 overflow-hidden p-0">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[700px] text-left text-sm">
            <thead>
              <tr className="border-b border-line text-xs text-dim">
                {["Item", "Category", "Qty", "Rate", "GST%", "Min Stock", "Status", "Updated", ""].map((h) => (
                  <th key={h} className="px-5 py-3.5 font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map((item) => (
                <tr
                  key={item.id}
                  className="border-b border-line/50 last:border-0 hover:bg-panel-2 cursor-pointer"
                  onClick={() => setDetail(item)}
                >
                  <td className="px-5 py-4">
                    <div className="flex items-center gap-2.5">
                      <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-violet/10 ring-1 ring-violet/25">
                        <Package className="size-4 text-violet-soft" />
                      </span>
                      <span className="font-medium">{item.name}</span>
                    </div>
                  </td>
                  <td className="px-5 py-4 text-dim">{item.category || "—"}</td>
                  <td className="px-5 py-4 font-medium">
                    {item.quantity} {item.unit}
                  </td>
                  <td className="px-5 py-4">₹{item.rate}</td>
                  <td className="px-5 py-4 text-dim">{item.gstPercent}%</td>
                  <td className="px-5 py-4 text-dim">{item.minStock}</td>
                  <td className="px-5 py-4">
                    {item.lowStock ? (
                      <span className="inline-flex items-center gap-1 rounded-full bg-saffron/10 px-2 py-0.5 text-xs text-saffron ring-1 ring-saffron/25">
                        <AlertTriangle className="size-3" /> Low
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 rounded-full bg-mint/10 px-2 py-0.5 text-xs text-mint ring-1 ring-mint/25">
                        ✓ OK
                      </span>
                    )}
                  </td>
                  <td className="px-5 py-4 text-dim text-xs">{item.updated}</td>
                  <td className="px-5 py-4 text-right" onClick={(e) => e.stopPropagation()}>
                    <button
                      onClick={() => remove(item.id)}
                      className="rounded-lg border border-line px-2 py-1.5 text-dim transition hover:text-rose"
                      aria-label="Delete"
                    >
                      <Trash2 className="size-3.5" />
                    </button>
                  </td>
                </tr>
              ))}
              {filtered.length === 0 && (
                <tr>
                  <td colSpan={9}>
                    <EmptyState
                      icon={Package}
                      title="No stock items"
                      sub="Add items manually or let the agent create them via voice commands."
                    />
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>

      {/* Add item modal */}
      {showAdd && <AddStockModal onClose={() => { setShowAdd(false); reload(); }} />}

      {/* Detail / entries modal */}
      {detail && <StockDetailModal item={detail} onClose={() => { setDetail(null); reload(); }} />}
    </div>
  );
}

function AddStockModal({ onClose }) {
  const [form, setForm] = useState({
    name: "", category: "", quantity: 0, unit: "pcs",
    rate: 0, gst_percent: 0, min_stock: 0,
  });
  const [saving, setSaving] = useState(false);

  async function save() {
    if (!form.name.trim()) return;
    setSaving(true);
    await post("/api/stock", form);
    onClose();
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4" onClick={onClose}>
      <div
        className="w-full max-w-lg rounded-2xl border border-line bg-panel p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-lg font-semibold">Add Stock Item</h2>
        <div className="mt-4 grid gap-3">
          <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })}
            placeholder="Item name *" className="rounded-lg border border-line bg-panel-2 px-3 py-2 text-sm outline-none focus:ring-1 focus:ring-violet" />
          <input value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}
            placeholder="Category" className="rounded-lg border border-line bg-panel-2 px-3 py-2 text-sm outline-none focus:ring-1 focus:ring-violet" />
          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="text-xs text-dim">Quantity</label>
              <input type="number" value={form.quantity} onChange={(e) => setForm({ ...form, quantity: +e.target.value })}
                className="mt-1 w-full rounded-lg border border-line bg-panel-2 px-3 py-2 text-sm outline-none" />
            </div>
            <div>
              <label className="text-xs text-dim">Unit</label>
              <input value={form.unit} onChange={(e) => setForm({ ...form, unit: e.target.value })}
                className="mt-1 w-full rounded-lg border border-line bg-panel-2 px-3 py-2 text-sm outline-none" />
            </div>
            <div>
              <label className="text-xs text-dim">Rate (₹)</label>
              <input type="number" value={form.rate} onChange={(e) => setForm({ ...form, rate: +e.target.value })}
                className="mt-1 w-full rounded-lg border border-line bg-panel-2 px-3 py-2 text-sm outline-none" />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs text-dim">GST %</label>
              <input type="number" value={form.gst_percent} onChange={(e) => setForm({ ...form, gst_percent: +e.target.value })}
                className="mt-1 w-full rounded-lg border border-line bg-panel-2 px-3 py-2 text-sm outline-none" />
            </div>
            <div>
              <label className="text-xs text-dim">Min Stock (alert)</label>
              <input type="number" value={form.min_stock} onChange={(e) => setForm({ ...form, min_stock: +e.target.value })}
                className="mt-1 w-full rounded-lg border border-line bg-panel-2 px-3 py-2 text-sm outline-none" />
            </div>
          </div>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose} className="rounded-lg border border-line px-4 py-2 text-sm text-dim hover:text-white">Cancel</button>
          <button onClick={save} disabled={saving}
            className="rounded-xl bg-violet px-4 py-2 text-sm font-medium text-white transition hover:bg-violet/80 disabled:opacity-50">
            {saving ? "Saving..." : "Add Item"}
          </button>
        </div>
      </div>
    </div>
  );
}

function StockDetailModal({ item, onClose }) {
  const { data: entries, reload } = useApi(`/api/stock/${item.id}/entries`, [], 5000);
  const [entryForm, setEntryForm] = useState({ entry_type: "in", quantity: 0, notes: "" });
  const [saving, setSaving] = useState(false);

  async function addEntry() {
    if (entryForm.quantity <= 0) return;
    setSaving(true);
    await post(`/api/stock/${item.id}/entries`, entryForm);
    setEntryForm({ entry_type: "in", quantity: 0, notes: "" });
    setSaving(false);
    reload();
  }

  const typeIcons = {
    in: <ArrowDownCircle className="size-4 text-mint" />,
    out: <ArrowUpCircle className="size-4 text-saffron" />,
    adjust: <RefreshCw className="size-4 text-violet-soft" />,
  };

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4" onClick={onClose}>
      <div
        className="w-full max-w-lg max-h-[80vh] overflow-y-auto rounded-2xl border border-line bg-panel p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between">
          <div>
            <h2 className="text-lg font-semibold">{item.name}</h2>
            <p className="text-sm text-dim">
              {item.quantity} {item.unit} · ₹{item.rate} · GST {item.gstPercent}%
            </p>
          </div>
          <button onClick={onClose} className="text-dim hover:text-white"><X className="size-5" /></button>
        </div>

        {/* Quick entry form */}
        <div className="mt-4 rounded-xl border border-line bg-panel-2 p-3">
          <p className="text-xs font-medium text-dim mb-2">Quick Entry</p>
          <div className="flex gap-2">
            <select value={entryForm.entry_type}
              onChange={(e) => setEntryForm({ ...entryForm, entry_type: e.target.value })}
              className="rounded-lg border border-line bg-panel px-2 py-1.5 text-sm outline-none">
              <option value="in">📥 In</option>
              <option value="out">📤 Out</option>
              <option value="adjust">🔄 Adjust</option>
            </select>
            <input type="number" placeholder="Qty" value={entryForm.quantity || ""}
              onChange={(e) => setEntryForm({ ...entryForm, quantity: +e.target.value })}
              className="w-20 rounded-lg border border-line bg-panel px-2 py-1.5 text-sm outline-none" />
            <input placeholder="Notes" value={entryForm.notes}
              onChange={(e) => setEntryForm({ ...entryForm, notes: e.target.value })}
              className="flex-1 rounded-lg border border-line bg-panel px-2 py-1.5 text-sm outline-none" />
            <button onClick={addEntry} disabled={saving}
              className="rounded-lg bg-violet px-3 py-1.5 text-sm text-white hover:bg-violet/80 disabled:opacity-50">
              Add
            </button>
          </div>
        </div>

        {/* Entries log */}
        <div className="mt-4 space-y-2">
          <p className="text-xs font-medium text-dim">History</p>
          {entries.map((e) => (
            <div key={e.id} className="flex items-center gap-3 rounded-lg border border-line/50 bg-panel-2 px-3 py-2 text-sm">
              {typeIcons[e.type] || typeIcons.adjust}
              <span className="font-medium">{e.type === "in" ? "+" : e.type === "out" ? "-" : "="}{e.quantity}</span>
              <span className="flex-1 text-dim text-xs">{e.notes}</span>
              <span className="text-xs text-dim">{e.time}</span>
            </div>
          ))}
          {entries.length === 0 && (
            <p className="py-6 text-center text-xs text-dim">No entries yet</p>
          )}
        </div>
      </div>
    </div>
  );
}
