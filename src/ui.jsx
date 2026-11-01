import { useId } from "react";

export function LogoMark({ className = "size-7" }) {
  const gradId = useId();
  return (
    <svg
      viewBox="0 0 500 400"
      fill="none"
      className={className}
      xmlns="http://www.w3.org/2000/svg"
    >
      <defs>
        <linearGradient
          id={gradId}
          x1="20"
          y1="200"
          x2="475"
          y2="200"
          gradientUnits="userSpaceOnUse"
        >
          <stop offset="0%" stopColor="#3a58cf" />
          <stop offset="38%" stopColor="#40e5e1" />
          <stop offset="68%" stopColor="#3990df" />
          <stop offset="100%" stopColor="#7055dd" />
        </linearGradient>
      </defs>

      {/* Waveform flowing into bubble wall */}
      <path
        d="M 22 192 L 60 192 C 80 192 92 120 108 120 C 122 120 138 260 153 260 C 168 260 186 52 201 52 C 216 52 234 300 249 300 C 264 300 279 120 293 120 C 307 120 323 250 338 250 C 353 250 365 192 380 192 L 428 192 C 448 192 462 185 466 168"
        stroke={`url(#${gradId})`}
        strokeWidth="24"
        strokeLinecap="round"
        strokeLinejoin="round"
      />

      {/* Bubble upper arc */}
      <path
        d="M 240 52 C 265 38 296 28 335 28 C 408 28 466 85 466 168"
        stroke={`url(#${gradId})`}
        strokeWidth="24"
        strokeLinecap="round"
        strokeLinejoin="round"
      />

      {/* Bubble lower side with tail (separated by small gap on right side) */}
      <path
        d="M 470 218 C 466 250 456 280 448 300 L 462 366 L 434 316 C 395 348 320 348 236 328"
        stroke={`url(#${gradId})`}
        strokeWidth="24"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function Logo({ className = "" }) {
  return (
    <span className={`inline-flex items-center gap-2 whitespace-nowrap text-[15px] sm:text-[17px] ${className}`}>
      <span className="grid size-8 sm:size-9 place-items-center rounded-xl bg-panel-2 ring-1 ring-line brand-glow shrink-0">
        <LogoMark className="size-5 sm:size-6" />
      </span>
      <span className="uppercase tracking-tight leading-none">
        <span className="font-bold text-white">VoiceFlow</span>{" "}
        <span className="font-light text-dim">AI</span>
      </span>
    </span>
  );
}

export function Card({ className = "", children }) {
  return (
    <div className={`rounded-2xl border border-line bg-panel p-5 ${className}`}>{children}</div>
  );
}

/** Section heading used across the landing page. */
export function SectionTitle({ eyebrow, title, sub }) {
  return (
    <div className="mx-auto max-w-3xl text-center">
      <p className="text-xs font-semibold uppercase tracking-[0.2em] text-violet-soft">{eyebrow}</p>
      <h2 className="mt-3 text-3xl font-semibold tracking-tight sm:text-4xl">{title}</h2>
      {sub && <p className="mt-4 text-[15px] leading-relaxed text-dim">{sub}</p>}
    </div>
  );
}

/**
 * Shown wherever there's genuinely no data yet — before the first real call or
 * message arrives. Never stands in for a loading state.
 */
export function EmptyState({ icon: Icon, title, sub, action }) {
  return (
    <div className="grid place-items-center px-6 py-16 text-center">
      {Icon && (
        <span className="grid size-12 place-items-center rounded-2xl bg-panel-2 ring-1 ring-line">
          <Icon className="size-5 text-dim" />
        </span>
      )}
      <p className="mt-4 text-sm font-medium">{title}</p>
      {sub && <p className="mt-1.5 max-w-sm text-xs leading-relaxed text-dim">{sub}</p>}
      {action}
    </div>
  );
}

/** Backend unreachable or erroring — distinct from "no data yet". */
export function ErrorNote({ error }) {
  return (
    <div className="rounded-xl border border-saffron/30 bg-saffron/5 px-4 py-3 text-xs text-saffron">
      Backend not reachable — {error}. Start it with{" "}
      <code className="font-mono">uvicorn main:app --port 8000</code> in <code>backend/</code>.
    </div>
  );
}

const PALETTE_STOPS = [
  { r: 0x3a, g: 0x58, b: 0xcf }, // royal #3a58cf
  { r: 0x40, g: 0xe5, b: 0xe1 }, // aqua #40e5e1
  { r: 0x39, g: 0x90, b: 0xdf }, // azure #3990df
  { r: 0x70, g: 0x55, b: 0xdd }, // violet #7055dd
];

function getWaveColor(index, total) {
  if (total <= 1) return "#40e5e1";
  const t = index / (total - 1);
  const scaled = t * (PALETTE_STOPS.length - 1);
  const i0 = Math.floor(scaled);
  const i1 = Math.min(i0 + 1, PALETTE_STOPS.length - 1);
  const frac = scaled - i0;
  const c0 = PALETTE_STOPS[i0];
  const c1 = PALETTE_STOPS[i1];
  const r = Math.round(c0.r + (c1.r - c0.r) * frac);
  const g = Math.round(c0.g + (c1.g - c0.g) * frac);
  const b = Math.round(c0.b + (c1.b - c0.b) * frac);
  return `rgb(${r}, ${g}, ${b})`;
}

/**
 * Animated voice waveform. `bars` sets the count; each bar gets a staggered
 * delay and sweeps the brand gradient left to right (royal > aqua > azure > violet).
 */
export function Waveform({ bars = 28, active = true, className = "" }) {
  return (
    <div className={`flex items-center gap-[3px] ${className}`}>
      {Array.from({ length: bars }, (_, i) => (
        <span
          key={i}
          className={`w-[3px] rounded-full ${active ? "wave-bar" : "opacity-30"}`}
          style={{
            backgroundColor: getWaveColor(i, bars),
            height: `${18 + ((i * 37) % 30)}px`,
            animationDelay: `${(i % 9) * 0.09}s`,
          }}
        />
      ))}
    </div>
  );
}
