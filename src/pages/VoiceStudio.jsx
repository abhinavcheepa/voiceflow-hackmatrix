import { useState } from "react";
import { Mic, Square, Check, Play } from "lucide-react";
import { Card, Waveform } from "../ui.jsx";

const samples = [
  { label: "Greeting", text: "Namaste! VoiceFlow clinic mein aapka swagat hai.", seconds: 4 },
  { label: "Booking", text: "Aapka appointment kal subah 11 baje confirm kar diya hai.", seconds: 6 },
  { label: "Follow-up", text: "Kya main aapki aur kisi cheez mein madad kar sakta hoon?", seconds: 5 },
];

export default function VoiceStudio() {
  const [recording, setRecording] = useState(false);
  const [tone, setTone] = useState("Warm & polite");
  const [greeting, setGreeting] = useState(
    "Namaste! VoiceFlow clinic mein aapka swagat hai. Main aapki kaise madad kar sakta hoon?",
  );

  return (
    <div className="mx-auto max-w-5xl p-6 lg:p-8">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Voice Studio</h1>
        <p className="mt-1 text-sm text-dim">
          Record once. Every call and WhatsApp voice note afterwards sounds like you.
        </p>
      </header>

      <div className="mt-7 grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <div className="flex items-center justify-between">
            <h2 className="text-[15px] font-semibold">Your voice profile</h2>
            <span className="flex items-center gap-1.5 rounded-full bg-mint/10 px-3 py-1.5 text-xs text-mint ring-1 ring-mint/25">
              <Check className="size-3" /> Trained
            </span>
          </div>

          <div className="mt-8 flex flex-col items-center">
            <button
              onClick={() => setRecording((r) => !r)}
              className={`grid size-20 place-items-center rounded-full transition ${
                recording
                  ? "bg-saffron text-ink"
                  : "bg-violet text-white ring-8 ring-violet/15 hover:bg-violet-soft"
              }`}
            >
              {recording ? <Square className="size-7" /> : <Mic className="size-8" />}
            </button>
            <p className="mt-4 text-sm text-dim">
              {recording ? "Recording… speak naturally for 60 seconds" : "Record a new 60s sample to retrain"}
            </p>
            <Waveform bars={40} active={recording} className="mt-6 h-14" />
          </div>

          <div className="mt-8 grid gap-3 sm:grid-cols-3">
            {[
              ["Similarity", "96%"],
              ["Accent match", "Indian · Hindi"],
              ["Sample length", "1m 04s"],
            ].map(([k, v]) => (
              <div key={k} className="rounded-xl border border-line bg-panel-2 p-4">
                <p className="text-xs text-dim">{k}</p>
                <p className="mt-1 text-sm font-medium">{v}</p>
              </div>
            ))}
          </div>
        </Card>

        <Card>
          <h2 className="text-[15px] font-semibold">Persona</h2>
          <p className="mt-1 text-xs text-dim">How the agent talks and writes as you</p>

          <p className="mt-6 text-xs text-dim">Tone</p>
          <div className="mt-2 space-y-2">
            {["Warm & polite", "Crisp & professional", "Friendly & casual"].map((t) => (
              <button
                key={t}
                onClick={() => setTone(t)}
                className={`w-full rounded-xl px-3.5 py-2.5 text-left text-sm transition ${
                  tone === t
                    ? "bg-violet/15 text-white ring-1 ring-violet/30"
                    : "border border-line bg-panel-2 text-dim hover:text-white"
                }`}
              >
                {t}
              </button>
            ))}
          </div>

          <p className="mt-6 text-xs text-dim">Opening line</p>
          <textarea
            value={greeting}
            onChange={(e) => setGreeting(e.target.value)}
            rows={4}
            className="mt-2 w-full resize-none rounded-xl border border-line bg-panel-2 p-3.5 text-sm outline-none focus:border-violet/50"
          />

          <button className="mt-4 w-full rounded-xl bg-violet py-2.5 text-sm font-medium text-white transition hover:bg-violet-soft">
            Save persona
          </button>
        </Card>
      </div>

      <Card className="mt-4">
        <h2 className="text-[15px] font-semibold">Preview clips</h2>
        <p className="mt-1 text-xs text-dim">Generated in your cloned voice</p>
        <div className="mt-5 space-y-2">
          {samples.map((s) => (
            <div
              key={s.label}
              className="flex items-center gap-4 rounded-xl border border-line bg-panel-2 p-3.5"
            >
              <button className="grid size-9 shrink-0 place-items-center rounded-full bg-violet/15 text-violet-soft ring-1 ring-violet/30 transition hover:bg-violet/25">
                <Play className="size-4" />
              </button>
              <div className="min-w-0 flex-1">
                <p className="text-xs text-dim">{s.label}</p>
                <p className="truncate text-sm">{s.text}</p>
              </div>
              <Waveform bars={12} active={false} className="hidden h-4 sm:flex" />
              <span className="shrink-0 text-xs text-dim">0:0{s.seconds}</span>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
