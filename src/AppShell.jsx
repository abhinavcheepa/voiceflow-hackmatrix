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
} from "lucide-react";
import { Logo } from "./ui.jsx";
import { useApi } from "./api.js";

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
    ],
  },
  {
    title: "Manage",
    items: [
      { to: "/app/agents", icon: Bot, label: "Agents" },
      { to: "/app/contacts", icon: Users, label: "Contacts" },
      { to: "/app/campaigns", icon: Megaphone, label: "Campaigns" },
      { to: "/app/voice-studio", icon: Mic, label: "Voice Studio" },
    ],
  },
];

const allItems = groups.flatMap((g) => g.items);

/** Replaces what used to be a hardcoded card — this reads the real setup. */
function VoiceProfile() {
  const { data: profile } = useApi("/api/voice/profile", null, 0);
  const { data: health } = useApi("/health", null, 30000);
  const { data: agent } = useApi("/api/agents", [], 0);

  const active = agent.find((a) => a.isDefault) ?? agent[0];
  const ready = profile?.cloned && health?.brain;

  return (
    <div className="mt-auto rounded-xl border border-line bg-panel-2 p-3.5">
      <p className="text-xs text-dim">Voice profile</p>
      <p className="mt-1 truncate text-sm font-medium">
        {profile?.cloned ? profile.name : "Not cloned yet"}
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

export default function AppShell() {
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

        <Link
          to="/"
          className="mt-3 flex items-center gap-2 px-3 py-2 text-xs text-dim transition hover:text-white"
        >
          <ArrowLeft className="size-3.5" /> Back to site
        </Link>
      </aside>

      {/* Mobile nav — scrolls sideways rather than hiding items behind a menu. */}
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

      <main className="min-w-0 flex-1 pb-20 md:pb-0">
        <Outlet />
      </main>
    </div>
  );
}
