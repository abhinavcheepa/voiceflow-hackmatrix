import { NavLink, Outlet, Link } from "react-router-dom";
import { LayoutDashboard, Phone, MessageSquare, Mic, ArrowLeft } from "lucide-react";
import { Logo } from "./ui.jsx";

const nav = [
  { to: "/app", end: true, icon: LayoutDashboard, label: "Dashboard" },
  { to: "/app/calls", icon: Phone, label: "Calls" },
  { to: "/app/whatsapp", icon: MessageSquare, label: "WhatsApp" },
  { to: "/app/voice-studio", icon: Mic, label: "Voice Studio" },
];

export default function AppShell() {
  return (
    <div className="flex min-h-screen">
      <aside className="hidden w-60 shrink-0 flex-col border-r border-line bg-panel p-4 md:sticky md:top-0 md:flex md:h-screen">
        <Link to="/" className="px-1 py-2">
          <Logo />
        </Link>

        <nav className="mt-6 space-y-1">
          {nav.map(({ to, end, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition ${
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
        </nav>

        <div className="mt-auto rounded-xl border border-line bg-panel-2 p-3.5">
          <p className="text-xs text-dim">Voice profile</p>
          <p className="mt-1 text-sm font-medium">Abhinav — Hindi/English</p>
          <p className="mt-1.5 flex items-center gap-1.5 text-xs text-mint">
            <span className="size-1.5 rounded-full bg-mint" /> Agent live
          </p>
        </div>

        <Link
          to="/"
          className="mt-3 flex items-center gap-2 px-3 py-2 text-xs text-dim transition hover:text-white"
        >
          <ArrowLeft className="size-3.5" /> Back to site
        </Link>
      </aside>

      {/* Mobile nav — the sidebar is hidden below md. */}
      <div className="fixed inset-x-0 bottom-0 z-50 flex border-t border-line bg-panel md:hidden">
        {nav.map(({ to, end, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              `flex flex-1 flex-col items-center gap-1 py-2.5 text-[10px] ${
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
