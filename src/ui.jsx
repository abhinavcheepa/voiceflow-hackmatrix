import { AudioLines } from "lucide-react";

export function Logo({ className = "" }) {
  return (
    <span className={`flex items-center gap-2.5 ${className}`}>
      <span className="grid size-9 place-items-center rounded-xl bg-violet/15 ring-1 ring-violet/40">
        <AudioLines className="size-5 text-violet-soft" strokeWidth={2.2} />
      </span>
      <span className="text-[17px] font-semibold tracking-tight">
        Voice<span className="text-violet-soft">Flow</span> AI
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
 * Animated voice waveform. `bars` sets the count; each bar gets a staggered
 * delay and a stable pseudo-random height so it reads as speech, not a sine.
 */
export function Waveform({ bars = 28, active = true, className = "" }) {
  return (
    <div className={`flex items-center gap-[3px] ${className}`}>
      {Array.from({ length: bars }, (_, i) => (
        <span
          key={i}
          className={`w-[3px] rounded-full bg-violet-soft ${active ? "wave-bar" : "opacity-30"}`}
          style={{
            height: `${18 + ((i * 37) % 30)}px`,
            animationDelay: `${(i % 9) * 0.09}s`,
          }}
        />
      ))}
    </div>
  );
}
