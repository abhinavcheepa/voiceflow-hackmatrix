import { useEffect, useRef, useState } from "react";
import { Mic, Square, Check, Play, Trash2, Loader2, Lock, ChevronDown } from "lucide-react";
import { Card, Waveform } from "../ui.jsx";
import { API, useApi, post, put } from "../api.js";

const PROMPTS = [
  "Namaste! VoiceFlow clinic mein aapka swagat hai. Main aapki kaise madad kar sakta hoon?",
  "Aapka appointment kal subah gyarah baje confirm kar diya hai.",
  "Sorry, abhi wo item stock mein nahi hai — main aapko kal update kar dunga.",
];

const LANG_LABEL = { hi: "Hindi", ta: "Tamil", te: "Telugu", en: "English" };

/** Pick a ready-made voice. Free on every plan — this is the default path. */
function VoicePicker({ profile, onPicked }) {
  const { data: voices, error } = useApi("/api/voice/library", [], 0);
  const [lang, setLang] = useState("hi");
  const [busy, setBusy] = useState(null);
  const [playing, setPlaying] = useState(null);
  const audio = useRef(null);

  const languages = [...new Set(voices.map((v) => v.language))];
  const shown = voices.filter((v) => v.language === lang);

  async function preview(v) {
    setPlaying(v.id);
    try {
      // Audition only — previewing must NOT change which voice the agent uses.
      // Listening to six voices should not leave you on the last one you played.
      const r = await fetch(`${API}/api/voice/preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: PROMPTS[0], voice_id: v.id, language: v.language }),
      });
      if (!r.ok) throw new Error((await r.json()).detail);
      audio.current?.pause();
      audio.current = new Audio(URL.createObjectURL(await r.blob()));
      audio.current.play();
    } catch (e) {
      alert(e.message);
    } finally {
      setPlaying(null);
    }
  }

  async function choose(v) {
    setBusy(v.id);
    await post("/api/voice/select", { voice_id: v.id, name: v.name });
    onPicked();
    setBusy(null);
  }

  return (
    <Card>
      <h2 className="text-[15px] font-semibold">Choose a voice</h2>
      <p className="mt-1 text-xs text-dim">
        This is the voice every call and WhatsApp voice note will use.
      </p>

      {error && <p className="mt-4 text-xs text-saffron">{error}</p>}

      <div className="mt-4 flex flex-wrap gap-2">
        {languages.map((l) => (
          <button
            key={l}
            onClick={() => setLang(l)}
            className={`rounded-full px-3.5 py-1.5 text-xs transition ${
              lang === l
                ? "bg-violet text-white"
                : "border border-line bg-panel-2 text-dim hover:text-white"
            }`}
          >
            {LANG_LABEL[l] ?? l}
          </button>
        ))}
      </div>

      <div className="mt-4 max-h-80 space-y-2 overflow-y-auto pr-1">
        {shown.map((v) => {
          const active = profile?.voiceId === v.id;
          return (
            <div
              key={v.id}
              className={`flex items-center gap-3 rounded-xl border p-3 transition ${
                active ? "border-violet/40 bg-violet/10" : "border-line bg-panel-2"
              }`}
            >
              <button
                onClick={() => preview(v)}
                disabled={playing === v.id}
                title="Hear this voice"
                className="grid size-9 shrink-0 place-items-center rounded-full bg-violet/15 text-violet-soft ring-1 ring-violet/30 transition hover:bg-violet/25 disabled:opacity-50"
              >
                {playing === v.id ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Play className="size-4" />
                )}
              </button>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm">{v.name}</p>
                {v.description && (
                  <p className="truncate text-xs text-dim">{v.description}</p>
                )}
              </div>
              {active ? (
                <span className="flex shrink-0 items-center gap-1 rounded-full bg-mint/10 px-2.5 py-1 text-xs text-mint ring-1 ring-mint/25">
                  <Check className="size-3" /> in use
                </span>
              ) : (
                <button
                  onClick={() => choose(v)}
                  disabled={busy === v.id}
                  className="shrink-0 rounded-lg border border-line px-3 py-1.5 text-xs text-dim transition hover:text-white"
                >
                  {busy === v.id ? "…" : "Use"}
                </button>
              )}
            </div>
          );
        })}
        {shown.length === 0 && !error && (
          <p className="py-8 text-center text-xs text-dim">Loading voices…</p>
        )}
      </div>
    </Card>
  );
}

function useRecorder() {
  const [recording, setRecording] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [clips, setClips] = useState([]);
  const recorder = useRef(null);
  const chunks = useRef([]);
  const startedAt = useRef(0);

  useEffect(() => {
    if (!recording) return;
    const t = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, [recording]);

  async function start() {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    chunks.current = [];
    const mr = new MediaRecorder(stream);
    mr.ondataavailable = (e) => e.data.size && chunks.current.push(e.data);
    mr.onstop = () => {
      const blob = new Blob(chunks.current, { type: mr.mimeType });
      const elapsed = Math.round((Date.now() - startedAt.current) / 1000);
      setClips((c) => [...c, { blob, url: URL.createObjectURL(blob), seconds: elapsed }]);
      stream.getTracks().forEach((t) => t.stop());
    };
    mr.start();
    recorder.current = mr;
    startedAt.current = Date.now();
    setSeconds(0);
    setRecording(true);
  }

  function stop() {
    recorder.current?.stop();
    setRecording(false);
  }

  return {
    recording,
    seconds,
    clips,
    start,
    stop,
    remove: (i) => setClips((c) => c.filter((_, j) => j !== i)),
    total: clips.reduce((a, c) => a + c.seconds, 0),
  };
}

/** Cloning needs a paid Cartesia plan, so it's the secondary path, folded away. */
function CloneYourVoice({ onCloned }) {
  const [open, setOpen] = useState(false);
  const { recording, seconds, clips, start, stop, remove, total } = useRecorder();
  const [name, setName] = useState("My voice");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function upload() {
    setBusy(true);
    setError(null);
    const form = new FormData();
    form.append("name", name);
    clips.forEach((c, i) => form.append("samples", c.blob, `sample-${i}.webm`));
    try {
      const r = await fetch(`${API}/api/voice/clone`, { method: "POST", body: form });
      const body = await r.json();
      if (!r.ok) throw new Error(body.detail ?? r.statusText);
      onCloned();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="mt-4">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center justify-between text-left"
      >
        <div>
          <h2 className="flex items-center gap-2 text-[15px] font-semibold">
            <Lock className="size-3.5 text-dim" /> Clone your own voice
          </h2>
          <p className="mt-1 text-xs text-dim">
            Record yourself and the agent speaks in your voice — needs a paid Cartesia plan.
          </p>
        </div>
        <ChevronDown className={`size-4 shrink-0 text-dim transition ${open ? "rotate-180" : ""}`} />
      </button>

      {open && (
        <>
          <div className="mt-6 flex flex-col items-center">
            <button
              onClick={recording ? stop : start}
              className={`grid size-16 place-items-center rounded-full transition ${
                recording ? "bg-saffron text-ink" : "bg-violet text-white hover:bg-violet-soft"
              }`}
            >
              {recording ? <Square className="size-6" /> : <Mic className="size-6" />}
            </button>
            <p className="mt-3 text-xs text-dim">
              {recording ? `Recording… ${seconds}s` : "Tap and read the lines below"}
            </p>
            <Waveform bars={30} active={recording} className="mt-4 h-10" />
          </div>

          <div className="mt-5 space-y-2">
            {PROMPTS.map((p, i) => (
              <p
                key={i}
                className="rounded-xl border border-line bg-panel-2 px-3.5 py-2.5 text-sm text-dim"
              >
                {p}
              </p>
            ))}
          </div>

          {clips.length > 0 && (
            <div className="mt-5 space-y-2">
              {clips.map((c, i) => (
                <div
                  key={i}
                  className="flex items-center gap-3 rounded-xl border border-line bg-panel-2 p-3"
                >
                  <audio controls src={c.url} className="h-8 flex-1" />
                  <button onClick={() => remove(i)} className="text-dim transition hover:text-rose">
                    <Trash2 className="size-4" />
                  </button>
                </div>
              ))}
              <div className="flex flex-wrap items-center gap-3 pt-1">
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="flex-1 rounded-xl border border-line bg-panel-2 px-3.5 py-2.5 text-sm outline-none focus:border-violet/50"
                />
                <button
                  onClick={upload}
                  disabled={busy || total < 25}
                  className="flex items-center gap-2 rounded-xl bg-violet px-4 py-2.5 text-sm font-medium text-white transition hover:bg-violet-soft disabled:opacity-40"
                >
                  {busy && <Loader2 className="size-4 animate-spin" />}
                  {busy ? "Cloning…" : `Clone (${total}s)`}
                </button>
              </div>
              {total < 25 && <p className="text-xs text-dim">Record at least 25 seconds.</p>}
            </div>
          )}

          {error && (
            <p className="mt-4 rounded-xl border border-saffron/30 bg-saffron/5 px-4 py-3 text-xs text-saffron">
              {error}
            </p>
          )}
        </>
      )}
    </Card>
  );
}

function Preview() {
  const [text, setText] = useState(PROMPTS[1]);
  const [url, setUrl] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function speak() {
    setBusy(true);
    setError(null);
    try {
      const r = await fetch(`${API}/api/voice/preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!r.ok) throw new Error((await r.json()).detail ?? r.statusText);
      setUrl(URL.createObjectURL(await r.blob()));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="mt-4">
      <h2 className="text-[15px] font-semibold">Try it</h2>
      <p className="mt-1 text-xs text-dim">Type anything and hear it in the selected voice</p>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={3}
        className="mt-4 w-full resize-none rounded-xl border border-line bg-panel-2 p-3.5 text-sm outline-none focus:border-violet/50"
      />
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button
          onClick={speak}
          disabled={busy}
          className="flex items-center gap-2 rounded-xl bg-violet px-4 py-2.5 text-sm font-medium text-white transition hover:bg-violet-soft disabled:opacity-40"
        >
          {busy ? <Loader2 className="size-4 animate-spin" /> : <Play className="size-4" />}
          {busy ? "Generating…" : "Speak it"}
        </button>
        {url && <audio controls autoPlay src={url} className="h-9 flex-1" />}
      </div>
      {error && <p className="mt-3 text-xs text-saffron">{error}</p>}
    </Card>
  );
}

export default function VoiceStudio() {
  const { data: profile, reload } = useApi("/api/voice/profile", null, 0);
  const [style, setStyle] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (profile?.style) setStyle(profile.style);
  }, [profile?.style]);

  async function saveStyle() {
    await put("/api/voice/style", { style });
    setSaved(true);
    setTimeout(() => setSaved(false), 1800);
  }

  return (
    <div className="mx-auto max-w-5xl p-6 lg:p-8">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Voice Studio</h1>
        <p className="mt-1 text-sm text-dim">
          Pick how your agent sounds and how it writes.
        </p>
      </header>

      {!profile?.configured && (
        <p className="mt-5 rounded-xl border border-saffron/30 bg-saffron/5 px-4 py-3 text-xs text-saffron">
          Add <code className="font-mono">CARTESIA_API_KEY</code> to{" "}
          <code className="font-mono">backend/.env</code> to enable voices.
        </p>
      )}

      {profile?.ready && (
        <p className="mt-5 flex items-center gap-2 rounded-xl border border-mint/25 bg-mint/5 px-4 py-3 text-xs text-mint">
          <Check className="size-3.5 shrink-0" />
          Using <span className="font-medium">{profile.name}</span>
          {profile.source === "library" && " · ready-made voice"}
        </p>
      )}

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <VoicePicker profile={profile} onPicked={reload} />
        </div>

        <Card>
          <h2 className="text-[15px] font-semibold">Writing style</h2>
          <p className="mt-1 text-xs text-dim">
            How WhatsApp text replies should sound. Describe it the way you'd brief a new
            employee.
          </p>
          <textarea
            value={style}
            onChange={(e) => setStyle(e.target.value)}
            rows={9}
            className="mt-4 w-full resize-none rounded-xl border border-line bg-panel-2 p-3.5 text-sm outline-none focus:border-violet/50"
          />
          <button
            onClick={saveStyle}
            className="mt-4 w-full rounded-xl bg-violet py-2.5 text-sm font-medium text-white transition hover:bg-violet-soft"
          >
            {saved ? "Saved ✓" : "Save style"}
          </button>
        </Card>
      </div>

      <Preview />
      <CloneYourVoice onCloned={reload} />
    </div>
  );
}
