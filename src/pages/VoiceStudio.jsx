import { useEffect, useRef, useState } from "react";
import { Mic, Square, Check, Play, Trash2, Loader2, Lock, ChevronDown, Download, Volume2, RefreshCw } from "lucide-react";
import { Card, Waveform } from "../ui.jsx";
import { API, getApiToken, useApi, post, put } from "../api.js";

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
      const token = getApiToken();
      const r = await fetch(`${API}/api/voice/preview`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
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

/** Cloning runs locally (OpenVoice) on top of Edge — free, but an optional install. */
function CloneYourVoice({ profile, onCloned }) {
  const [open, setOpen] = useState(false);
  const { recording, seconds, clips, start, stop, remove, total } = useRecorder();
  const [name, setName] = useState("My voice");
  // Hindi verbs follow the speaker's gender ("sakti/sakta hoon"), so replies
  // must know who the cloned voice belongs to.
  const [gender, setGender] = useState("female");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function upload() {
    setBusy(true);
    setError(null);
    const form = new FormData();
    form.append("name", name);
    form.append("gender", gender);
    clips.forEach((c, i) => form.append("samples", c.blob, `sample-${i}.webm`));
    try {
      const token = getApiToken();
      const r = await fetch(`${API}/api/voice/clone`, {
        method: "POST",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: form,
      });
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
            Record yourself and the agent speaks in your voice — runs on this machine, free.
          </p>
          {profile?.cloned && (
            <p className="mt-1 text-xs text-mint">Active: {profile.name}</p>
          )}
        </div>
        <ChevronDown className={`size-4 shrink-0 text-dim transition ${open ? "rotate-180" : ""}`} />
      </button>

      {open && (
        <>
          {profile?.cloneMissing && (
            <p className="mt-4 rounded-xl border border-saffron/30 bg-saffron/5 px-4 py-3 text-xs text-saffron">
              Cloning isn't installed yet: {profile.cloneMissing}
            </p>
          )}
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
                <select
                  value={gender}
                  onChange={(e) => setGender(e.target.value)}
                  aria-label="Voice gender"
                  className="rounded-xl border border-line bg-panel-2 px-3 py-2.5 text-sm outline-none focus:border-violet/50"
                >
                  <option value="female">Female</option>
                  <option value="male">Male</option>
                </select>
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
      const token = getApiToken();
      const r = await fetch(`${API}/api/voice/preview`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
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

/** Speaking speed and pitch. Applies to calls and WhatsApp voice notes alike. */
function SpeakingVoice({ profile, onSaved }) {
  const [rate, setRate] = useState(15);
  const [pitch, setPitch] = useState(0);

  useEffect(() => {
    if (!profile) return;
    setRate(parseInt(profile.rate ?? "+15%", 10) || 0);
    setPitch(parseInt(profile.pitch ?? "+0Hz", 10) || 0);
  }, [profile?.rate, profile?.pitch]);

  // Save when the slider is released, not on every pixel of the drag — a PUT
  // per frame would hammer the backend for no benefit.
  async function save() {
    await put("/api/voice/tuning", {
      rate: `${rate >= 0 ? "+" : ""}${rate}%`,
      pitch: `${pitch >= 0 ? "+" : ""}${pitch}Hz`,
    });
    onSaved?.();
  }

  return (
    <Card>
      <h2 className="text-[15px] font-semibold">Speaking voice</h2>
      <p className="mt-1 text-xs text-dim">
        Edge ships one female and one male voice per Indian language, so pitch is what
        makes two agents sound like different people.
      </p>

      <label className="mt-4 block text-xs text-dim">
        <span className="flex justify-between">
          Speed <span className="font-mono text-white">{rate > 0 ? "+" : ""}{rate}%</span>
        </span>
        <input
          type="range" min={-30} max={50} step={5} value={rate}
          onChange={(e) => setRate(Number(e.target.value))}
          onPointerUp={save}
          onKeyUp={save}
          className="mt-2 w-full accent-violet"
        />
      </label>

      <label className="mt-4 block text-xs text-dim">
        <span className="flex justify-between">
          Pitch <span className="font-mono text-white">{pitch > 0 ? "+" : ""}{pitch}Hz</span>
        </span>
        <input
          type="range" min={-40} max={40} step={5} value={pitch}
          onChange={(e) => setPitch(Number(e.target.value))}
          onPointerUp={save}
          onKeyUp={save}
          className="mt-2 w-full accent-violet"
        />
      </label>

      <p className="mt-3 text-[11px] text-dim">
        Use “Hear it” below to check a change before a real call.
      </p>
    </Card>
  );
}


function SavedClonedAudios() {
  const { data: audios, reload, loading } = useApi("/api/voice/cloned-audios", [], 0);
  const [playingId, setPlayingId] = useState(null);
  const audioRef = useRef(null);

  function play(item) {
    if (playingId === item.id) {
      audioRef.current?.pause();
      setPlayingId(null);
      return;
    }
    audioRef.current?.pause();
    const url = `${API}/api/voice/cloned-audios/play/${encodeURIComponent(item.filename)}`;
    audioRef.current = new Audio(url);
    audioRef.current.onended = () => setPlayingId(null);
    audioRef.current.play();
    setPlayingId(item.id);
  }

  async function remove(id) {
    if (!confirm("Are you sure you want to delete this cloned audio?")) return;
    try {
      const token = getApiToken();
      await fetch(`${API}/api/voice/cloned-audios/${id}`, {
        method: "DELETE",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      reload();
    } catch (e) {
      alert("Failed to delete audio");
    }
  }

  return (
    <Card className="mt-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-[15px] font-semibold flex items-center gap-2">
            <Volume2 className="size-4 text-violet" /> Saved Cloned Audio Recordings
          </h2>
          <p className="mt-1 text-xs text-dim">
            All audio generated by the cloned voice pipeline (previews, calls, voice notes) are saved persistently.
          </p>
        </div>
        <button
          onClick={reload}
          className="flex items-center gap-1.5 rounded-lg border border-line bg-panel-2 px-3 py-1.5 text-xs text-dim transition hover:text-white"
        >
          <RefreshCw className={`size-3.5 ${loading ? "animate-spin" : ""}`} /> Refresh
        </button>
      </div>

      {(!audios || audios.length === 0) ? (
        <div className="mt-4 rounded-xl border border-line/60 bg-panel-2/40 p-6 text-center text-xs text-dim">
          No cloned audios generated yet. Test a clone in the Preview or Voice Note to see saved recordings here.
        </div>
      ) : (
        <div className="mt-4 divide-y divide-line/60 overflow-hidden rounded-xl border border-line bg-panel-2">
          {audios.map((item) => (
            <div key={item.id} className="flex flex-col sm:flex-row sm:items-center justify-between p-3 gap-2 hover:bg-panel-1/30 transition">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-xs font-medium text-white">Clone: {item.clone_id}</span>
                  <span className="text-[11px] text-dim">· {new Date(item.created_at).toLocaleString()}</span>
                  {item.duration_sec ? (
                    <span className="rounded bg-violet/20 px-1.5 py-0.5 text-[10px] text-violet font-mono">{item.duration_sec}s</span>
                  ) : null}
                </div>
                {item.text && (
                  <p className="mt-1 truncate text-xs text-dim italic">"{item.text}"</p>
                )}
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <button
                  onClick={() => play(item)}
                  className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition ${
                    playingId === item.id
                      ? "bg-violet text-white"
                      : "border border-line bg-panel-1 text-dim hover:text-white"
                  }`}
                >
                  {playingId === item.id ? <Square className="size-3.5 fill-current" /> : <Play className="size-3.5 fill-current" />}
                  {playingId === item.id ? "Pause" : "Play"}
                </button>
                <a
                  href={`${API}/api/voice/cloned-audios/play/${encodeURIComponent(item.filename)}`}
                  download={item.filename}
                  className="flex items-center gap-1.5 rounded-lg border border-line bg-panel-1 px-3 py-1.5 text-xs text-dim hover:text-white transition"
                  title="Download MP3"
                >
                  <Download className="size-3.5" />
                </a>
                <button
                  onClick={() => remove(item.id)}
                  className="rounded-lg border border-line bg-panel-1 p-1.5 text-dim hover:text-saffron transition"
                  title="Delete"
                >
                  <Trash2 className="size-3.5" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
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

      {profile && !profile.configured && (
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

        <div className="space-y-4">
          <SpeakingVoice profile={profile} onSaved={reload} />

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
      </div>

      <Preview />
      <CloneYourVoice profile={profile} onCloned={reload} />
      <SavedClonedAudios />
    </div>
  );
}
