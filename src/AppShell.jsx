import { useEffect, useState } from "react";
import { NavLink, Outlet, Link } from "react-router-dom";
import {
  LayoutDashboard,
  Phone,
  MessageSquare,
  Mic,
  ArrowLeft,
  Headphones,
  Bot,
  Users,
  Megaphone,
  BarChart3,
  CalendarCheck,
  Package,
  Receipt,
  Brain,
  FlaskConical,
  Key,
  Pause,
  Play,
  Radio,
  CheckCircle2,
  XCircle,
  X,
} from "lucide-react";
import { Logo } from "./ui.jsx";
import { useApi, post, getApiToken, setApiToken } from "./api.js";
import { connectEvents, disconnectEvents } from "./events.js";

const groups = [
  {
    items: [
      { to: "/app", end: true, icon: LayoutDashboard, label: "Dashboard" },
      { to: "/app/analytics", icon: BarChart3, label: "Analytics" },
    ],
  },
  {
    title: "Conversations",
    items: [
      { to: "/app/web-call", icon: Headphones, label: "Web call" },
      { to: "/app/calls", icon: Phone, label: "Calls" },
      { to: "/app/whatsapp", icon: MessageSquare, label: "WhatsApp" },
      { to: "/app/bookings", icon: CalendarCheck, label: "Bookings" },
    ],
  },
  {
    title: "Business",
    items: [
      { to: "/app/stock", icon: Package, label: "Stock" },
      { to: "/app/bills", icon: Receipt, label: "Bills" },
      { to: "/app/learning", icon: Brain, label: "Learning" },
    ],
  },
  {
    title: "Manage",
    items: [
      { to: "/app/agents", icon: Bot, label: "Agents" },
      { to: "/app/contacts", icon: Users, label: "Contacts" },
      { to: "/app/campaigns", icon: Megaphone, label: "Campaigns" },
      { to: "/app/voice-studio", icon: Mic, label: "Voice Studio" },
      { to: "/app/tests", icon: FlaskConical, label: "Tests" },
    ],
  },
];

const allItems = groups.flatMap((g) => g.items);

function VoiceProfile() {
  const { data: profile } = useApi("/api/voice/profile", null, 0);
  const { data: health } = useApi("/health", null, 30000);
  const { data: agent } = useApi("/api/agents", [], 0);

  const active = agent.find((a) => a.isDefault) ?? agent[0];
  const ready = profile?.ready && health?.brain;

  return (
    <div className="mt-auto rounded-xl border border-line bg-panel-2 p-3.5">
      <p className="text-xs text-dim">Voice</p>
      <p className="mt-1 truncate text-sm font-medium">
        {profile?.ready ? profile.name : "Not set up yet"}
      </p>
      {active && <p className="mt-0.5 truncate text-xs text-dim">{active.name}</p>}
      <p
        className={`mt-1.5 flex items-center gap-1.5 text-xs ${ready ? "text-mint" : "text-saffron"}`}
      >
        <span className={`size-1.5 rounded-full ${ready ? "bg-mint" : "bg-saffron"}`} />
        {ready ? "Agent live" : "Setup incomplete"}
      </p>
    </div>
  );
}

function TokenModal({ open, onClose }) {
  const [tokenInput, setTokenInput] = useState(getApiToken());
  const [saved, setSaved] = useState(false);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm">
      <div className="w-full max-w-md rounded-2xl border border-line bg-panel p-6 shadow-2xl">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="grid size-9 place-items-center rounded-xl bg-violet/15 ring-1 ring-violet/40">
              <Key className="size-4 text-violet-soft" />
            </div>
            <h3 className="font-semibold text-white">API Authentication Token</h3>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-dim transition hover:bg-panel-2 hover:text-white"
          >
            <X className="size-4" />
          </button>
        </div>

        <p className="mt-3 text-xs leading-relaxed text-dim">
          When <code className="rounded bg-panel-2 px-1 text-violet-soft">API_TOKEN</code> is set in your backend <code className="text-dim">.env</code>, all API routes and live events require this Bearer token.
        </p>

        <div className="mt-4">
          <label className="text-xs font-medium text-dim">Bearer Token</label>
          <input
            type="password"
            value={tokenInput}
            onChange={(e) => {
              setTokenInput(e.target.value);
              setSaved(false);
            }}
            placeholder="Paste your API_TOKEN here"
            className="mt-1.5 w-full rounded-xl border border-line bg-panel-2 px-3.5 py-2.5 text-sm text-white placeholder-dim/40 outline-none focus:border-violet/50"
          />
        </div>

        <div className="mt-5 flex items-center justify-between gap-3">
          <button
            onClick={() => {
              setApiToken("");
              setTokenInput("");
              setSaved(true);
              setTimeout(() => {
                setSaved(false);
                window.location.reload();
              }, 400);
            }}
            className="rounded-xl border border-line px-3.5 py-2 text-xs text-dim transition hover:bg-panel-2 hover:text-white"
          >
            Clear Token
          </button>
          <div className="flex items-center gap-2">
            <button
              onClick={() => {
                setApiToken(tokenInput.trim());
                setSaved(true);
                setTimeout(() => {
                  setSaved(false);
                  onClose();
                  window.dispatchEvent(new CustomEvent("voiceflow:refresh"));
                }, 400);
              }}
              className="flex items-center gap-1.5 rounded-xl bg-violet px-4 py-2 text-xs font-medium text-white transition hover:bg-violet-soft"
            >
              {saved ? <CheckCircle2 className="size-3.5 text-mint" /> : null}
              {saved ? "Saved!" : "Save Token"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function AppShell() {
  const [showAuthModal, setShowAuthModal] = useState(false);
  const [sseConnected, setSseConnected] = useState(false);
  const [toggling, setToggling] = useState(false);

  const { data: systemStatus, reload: reloadSystem } = useApi(
    "/api/system/status",
    { automation: true, whatsappAutoReply: true },
    15000,
  );

  useEffect(() => {
    connectEvents();

    const onStatus = (e) => setSseConnected(Boolean(e.detail?.connected));
    const onUnauthorized = () => setShowAuthModal(true);

    window.addEventListener("voiceflow:sse-status", onStatus);
    window.addEventListener("voiceflow:unauthorized", onUnauthorized);

    return () => {
      window.removeEventListener("voiceflow:sse-status", onStatus);
      window.removeEventListener("voiceflow:unauthorized", onUnauthorized);
      disconnectEvents();
    };
  }, []);

  async function toggleAutomation() {
    if (toggling) return;
    const current = Boolean(systemStatus?.automation);
    setToggling(true);
    try {
      await post("/api/system/controls", { automation: !current });
      reloadSystem();
    } catch (err) {
      console.warn("Failed to toggle automation", err);
    } finally {
      setToggling(false);
    }
  }

  const automationOn = systemStatus?.automation ?? true;

  return (
    <div className="flex min-h-screen">
      <aside className="hidden w-60 shrink-0 flex-col border-r border-line bg-panel p-4 md:sticky md:top-0 md:flex md:h-screen">
        <Link to="/" className="px-1 py-2">
          <Logo />
        </Link>

        <nav className="mt-5 flex-1 space-y-5 overflow-y-auto">
          {groups.map((group, i) => (
            <div key={i}>
              {group.title && (
                <p className="px-3 pb-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-dim/60">
                  {group.title}
                </p>
              )}
              <div className="space-y-0.5">
                {group.items.map(({ to, end, icon: Icon, label }) => (
                  <NavLink
                    key={to}
                    to={to}
                    end={end}
                    className={({ isActive }) =>
                      `flex items-center gap-3 rounded-xl px-3 py-2 text-sm transition ${
                        isActive
                          ? "bg-violet/15 text-white ring-1 ring-violet/30"
                          : "text-dim hover:bg-panel-2 hover:text-white"
                      }`
                    }
                  >
                    <Icon className="size-[18px]" />
                    {label}
                  </NavLink>
                ))}
              </div>
            </div>
          ))}
        </nav>

        <VoiceProfile />

        <div className="mt-3 flex items-center justify-between border-t border-line/60 pt-3">
          <Link
            to="/"
            className="flex items-center gap-2 text-xs text-dim transition hover:text-white"
          >
            <ArrowLeft className="size-3.5" /> Site
          </Link>
          <button
            onClick={() => setShowAuthModal(true)}
            className="flex items-center gap-1.5 rounded-lg border border-line bg-panel-2 px-2.5 py-1 text-[11px] text-dim transition hover:text-white"
            title="Configure API Token"
          >
            <Key className="size-3" />
            Token
          </button>
        </div>
      </aside>

      {/* Main content wrapper with persistent Top Control Bar */}
      <div className="flex min-w-0 flex-1 flex-col">
        {/* Top Control Bar */}
        <header className="sticky top-0 z-40 flex h-14 items-center justify-between border-b border-line bg-panel/90 px-4 backdrop-blur-md lg:px-8">
          <div className="flex items-center gap-3">
            <Link to="/" className="md:hidden">
              <Logo />
            </Link>
            <div className="hidden items-center gap-2 rounded-full border border-line bg-panel-2/80 px-2.5 py-1 text-xs text-dim md:flex">
              <span
                className={`size-2 rounded-full transition ${
                  sseConnected ? "animate-pulse bg-mint shadow-[0_0_8px_#34d399]" : "bg-dim/40"
                }`}
              />
              <span className="text-[11px] font-medium">
                {sseConnected ? "Live SSE Active" : "SSE Connecting…"}
              </span>
            </div>
          </div>

          <div className="flex items-center gap-2">
            {/* Master Automation Remote Control Switch */}
            <button
              onClick={toggleAutomation}
              disabled={toggling}
              className={`flex items-center gap-2 rounded-xl border px-3 py-1.5 text-xs font-medium transition active:scale-95 ${
                automationOn
                  ? "border-mint/30 bg-mint/10 text-mint hover:bg-mint/15"
                  : "border-saffron/30 bg-saffron/10 text-saffron hover:bg-saffron/15"
              }`}
              title={
                automationOn
                  ? "Click to pause all automated replies, sweeps & background worker"
                  : "Click to resume automated server execution"
              }
            >
              {automationOn ? (
                <>
                  <Pause className="size-3.5 fill-current" />
                  <span>Automation Active</span>
                </>
              ) : (
                <>
                  <Play className="size-3.5 fill-current" />
                  <span>Automation Paused</span>
                </>
              )}
            </button>

            <button
              onClick={() => setShowAuthModal(true)}
              className="grid size-8 place-items-center rounded-xl border border-line bg-panel-2 text-dim transition hover:text-white md:hidden"
              title="API Token"
            >
              <Key className="size-4" />
            </button>
          </div>
        </header>

        {/* Page Content */}
        <main className="min-w-0 flex-1 pb-20 md:pb-0">
          <Outlet />
        </main>
      </div>

      {/* Mobile nav — bottom bar */}
      <div className="fixed inset-x-0 bottom-0 z-50 flex overflow-x-auto border-t border-line bg-panel md:hidden">
        {allItems.map(({ to, end, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              `flex min-w-[4.5rem] shrink-0 flex-col items-center gap-1 py-2.5 text-[10px] ${
                isActive ? "text-violet-soft" : "text-dim"
              }`
            }
          >
            <Icon className="size-[18px]" />
            {label}
          </NavLink>
        ))}
      </div>

      <TokenModal open={showAuthModal} onClose={() => setShowAuthModal(false)} />
    </div>
  );
}
