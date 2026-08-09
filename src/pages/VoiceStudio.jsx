import { useEffect, useRef, useState } from "react";
import { Mic, Square, Check, Play, Trash2, Loader2 } from "lucide-react";
import { Card, Waveform } from "../ui.jsx";
import { API, useApi, put } from "../api.js";

// Read these aloud to give the cloner a range of tones. ElevenLabs wants
// roughly 30+ seconds of clean speech; three passes of these is plenty.
const PROMPTS = [
  "Namaste! VoiceFlow clinic mein aapka swagat hai. Main aapki kaise madad kar sakta hoon?",
  "Aapka appointment kal subah gyarah baje confirm kar diya hai.",
  "Sorry, abhi wo item stock mein nahi hai — main aapko kal update kar dunga.",
];

function useRecorder() {
  const [recording, setRecording] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [clips, setClips] = useState([]);
  const recorder = useRef(null);
  const chunks = useRef([]);
  // Wall-clock start, because `seconds` inside onstop would be a stale closure.
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

  const remove = (i) => setClips((c) => c.filter((_, j) => j !== i));

  return { recording, seconds, clips, start, stop, remove, total: clips.reduce((a, c) => a + c.seconds, 0) };
}

function Recorder({ onCloned }) {
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
      onCloned(body);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="mt-8 flex flex-col items-center">
        <button
          onClick={recording ? stop : start}
          className={`grid size-20 place-items-center rounded-full transition ${
            recording
              ? "bg-saffron text-ink"
              : "bg-violet text-white ring-8 ring-violet/15 hover:bg-violet-soft"
          }`}
        >
          {recording ? <Square className="size-7" /> : <Mic className="size-8" />}
        </button>
        <p className="mt-4 text-sm text-dim">
          {recording
            ? `Recording… ${seconds}s — speak naturally`
            : "Tap to record. Read the lines below."}
        </p>
        <Waveform bars={40} active={recording} className="mt-6 h-14" />
      </div>

      <div className="mt-6 space-y-2">
        {PROMPTS.map((p, i) => (
          <p key={i} className="rounded-xl border border-line bg-panel-2 px-3.5 py-2.5 text-sm text-dim">
            {p}
          </p>
        ))}
      </div>

      {clips.length > 0 && (
        <div className="mt-6 space-y-2">
          {clips.map((c, i) => (
            <div key={i} className="flex items-center gap-3 rounded-xl border border-line bg-panel-2 p-3">
              <audio controls src={c.url} className="h-8 flex-1" />
              <button onClick={() => remove(i)} className="text-dim transition hover:text-rose">
                <Trash2 className="size-4" />
              </button>
            </div>
          ))}

          <div className="flex flex-wrap items-center gap-3 pt-2">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Voice name"
              className="flex-1 rounded-xl border border-line bg-panel-2 px-3.5 py-2.5 text-sm outline-none focus:border-violet/50"
            />
            <button
              onClick={upload}
              disabled={busy || total < 25}
              title={total < 25 ? "Record at least 25 seconds" : undefined}
              className="flex items-center gap-2 rounded-xl bg-violet px-4 py-2.5 text-sm font-medium text-white transition hover:bg-violet-soft disabled:opacity-40"
            >
              {busy && <Loader2 className="size-4 animate-spin" />}
              {busy ? "Cloning…" : `Clone my voice (${total}s)`}
            </button>
          </div>
          {total < 25 && (
            <p className="text-xs text-dim">Record at least 25 seconds for a usable clone.</p>
          )}
        </div>
      )}

      {error && (
        <p className="mt-4 rounded-xl border border-saffron/30 bg-saffron/5 px-4 py-3 text-xs text-saffron">
          {error}
        </p>
      )}
    </>
  );
}

function Preview() {
  const [text, setText] = useState(PROMPTS[0]);
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
      <h2 className="text-[15px] font-semibold">Preview</h2>
      <p className="mt-1 text-xs text-dim">Type anything and hear it in your cloned voice</p>
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

  const cloned = profile?.cloned;

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
            <span
              className={`flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs ring-1 ${
                cloned
                  ? "bg-mint/10 text-mint ring-mint/25"
                  : "bg-panel-2 text-dim ring-line"
              }`}
            >
              {cloned ? <Check className="size-3" /> : <Mic className="size-3" />}
              {cloned ? `Cloned · ${profile.name}` : "Not cloned yet"}
            </span>
          </div>

          {!profile?.configured && (
            <p className="mt-4 rounded-xl border border-saffron/30 bg-saffron/5 px-4 py-3 text-xs text-saffron">
              Add <code className="font-mono">ELEVENLABS_API_KEY</code> to{" "}
              <code className="font-mono">backend/.env</code> to enable cloning.
            </p>
          )}

          <Recorder onCloned={reload} />
        </Card>

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
    </div>
  );
}
