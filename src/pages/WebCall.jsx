import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Phone, PhoneOff, Loader2, AlertTriangle } from "lucide-react";
import { Card, Waveform } from "../ui.jsx";
import { API, useApi } from "../api.js";

// Same origin by default (Vite proxies it), which is what works from a phone.
const WS_BASE = `${(API || window.location.origin).replace(/^http/, "ws")}/api/web-call/ws`;

// Turn detection. The browser decides when the caller has finished speaking so
// the backend never has to guess — it just receives one blob per turn.
//
// The thresholds are measured, not hardcoded. Fixed values were tried and were
// wrong on the first quiet laptop mic they met: real speech never cleared the
// bar, every turn was discarded, and the call simply never responded. Mic gain
// varies enormously between machines, so calibrate against this room instead.
const CALIBRATE_FRAMES = 45;  // ambient frames to measure before trusting a floor
const SILENCE_OVER_FLOOR = 2.0;  // speech starts at 2x the noise floor
const SPEECH_OVER_FLOOR = 3.5;   // a real voice peaks well past that
const FLOOR_MIN = 0.004;    // a silent room still needs a non-zero bar
const FLOOR_MAX = 0.030;    // a noisy room must not set an impossible one

const SILENCE_MS = 800;    // how long silence must hold to end a turn (sharper listening)
const MIN_SPEECH_MS = 300;  // ignore coughs, clicks and door slams
const MAX_TURN_MS = 12_000; // never wait longer than this for a silence

// A turn normally completes in 4-6s. Past this the backend is not coming back,
// so stop waiting for it and let the caller speak again.
const TURN_TIMEOUT_MS = 20_000;

// Barge-in. While the agent talks the mic also hears the agent (whatever echo
// cancellation leaves). So each clip's first moments teach us how loud that
// echo is, and only sound clearly above it, held for a moment, counts as the
// caller cutting in.
const ECHO_LEARN_MS = 400;
const BARGE_OVER_ECHO = 2.5;
const BARGE_MS = 300;

/** localStorage that never throws (private mode, blocked storage). */
const store = {
  get: (k) => {
    try {
      return localStorage.getItem(k) ?? "";
    } catch {
      return "";
    }
  },
  set: (k, v) => {
    try {
      localStorage.setItem(k, v);
    } catch {
      /* the value just won't be remembered */
    }
  },
};

/** Stable per-browser id, so the agent remembers this caller next time. */
function callerKey() {
  try {
    let k = localStorage.getItem("vf_caller");
    if (!k) {
      k = crypto.randomUUID();
      localStorage.setItem("vf_caller", k);
    }
    return k;
  } catch {
    return null; // private mode / blocked storage: the call works, just without memory
  }
}

const STATUS_TEXT = {
  idle: "Ready",
  connecting: "Connecting…",
  greeting: "Agent speaking…",
  listening: "Listening — speak now",
  thinking: "Thinking…",
  speaking: "Agent speaking…",
  ended: "Call ended",
};

export default function WebCall() {
  const [status, setStatus] = useState("idle");
  const [turns, setTurns] = useState([]);
  const [error, setError] = useState(null);
  const [callId, setCallId] = useState(null);
  const [agentId, setAgentId] = useState("");
  const [level, setLevel] = useState(null);
  const [allowBarge, setAllowBarge] = useState(true);
  // "customer": try your agent as a caller would. "assistant": you are the
  // owner giving orders (bills, WhatsApp, stock) — Jarvis mode.
  const [mode, setMode] = useState(() => store.get("vf_call_mode") || "customer");
  const [pin, setPin] = useState(() => store.get("vf_owner_pin"));
  const [typed, setTyped] = useState("");
  const assistant = mode === "assistant";
  const bargeOn = useRef(true);
  useEffect(() => {
    bargeOn.current = allowBarge;
  }, [allowBarge]);

  const { data: agents } = useApi("/api/agents", [], 0);

  // Default to whichever agent is marked default, but let it be changed
  // before dialling — otherwise every call goes to the generic one.
  useEffect(() => {
    if (!agentId && agents.length) {
      setAgentId(String((agents.find((a) => a.isDefault) ?? agents[0]).id));
    }
  }, [agents, agentId]);

  const agent = agents.find((a) => String(a.id) === agentId);

  const ws = useRef(null);
  const stream = useRef(null);
  const recorder = useRef(null);
  const audioCtx = useRef(null);
  const player = useRef(null);
  const chunks = useRef([]);
  const speechMs = useRef(0);
  const silenceMs = useRef(0);
  const peakRms = useRef(0);
  const noiseFloor = useRef(null);
  const dropNext = useRef(false);
  const agentSpeaking = useRef(false);
  const turnInFlight = useRef(false);
  const turnAt = useRef(0);
  const raf = useRef(null);
  const pingTimer = useRef(null);
  const playStartedAt = useRef(0);
  const echoPeak = useRef(0);
  const bargeMs = useRef(0);

  const cleanup = useCallback(() => {
    cancelAnimationFrame(raf.current);
    clearInterval(pingTimer.current);
    // Discard, don't send — hanging up must not fire one last turn.
    dropNext.current = true;
    recorder.current?.state === "recording" && recorder.current.stop();
    const p = player.current;
    if (p) {
      p.onended = p.onerror = null;
      p.pause();
    }
    stream.current?.getTracks().forEach((t) => t.stop());
    audioCtx.current?.close().catch(() => {});
    ws.current?.close();
    player.current = recorder.current = stream.current = audioCtx.current = ws.current = null;
    agentSpeaking.current = false;
    turnInFlight.current = false;
    noiseFloor.current = null;
    setLevel(null);
  }, []);

  useEffect(() => cleanup, [cleanup]);

  /** Send whatever has been recorded as one turn, then start a fresh recorder. */
  const flushTurn = useCallback(() => {
    const mr = recorder.current;
    if (!mr || mr.state !== "recording") return;
    // One turn at a time. Sending a second while the agent is still working on
    // the first produced two replies that then talked over each other.
    //
    // The timeout is not optional. A turn that dies silently — socket drops, a
    // backend exception that sends nothing back — leaves this flag stuck true
    // and every later turn is discarded, so the call goes deaf for good.
    if (turnInFlight.current && Date.now() - turnAt.current < TURN_TIMEOUT_MS) {
      return discardTurn();
    }
    turnInFlight.current = true;
    turnAt.current = Date.now();
    mr.stop(); // onstop ships the blob and restarts recording
  }, []);

  /** Same, but throw the audio away — it was noise, not a turn. */
  const discardTurn = useCallback(() => {
    const mr = recorder.current;
    if (!mr || mr.state !== "recording") return;
    dropNext.current = true;
    mr.stop();
  }, []);

  /** Clear the level counters so a new turn starts from zero. */
  const resetTurnDetection = useCallback(() => {
    speechMs.current = 0;
    silenceMs.current = 0;
    peakRms.current = 0;
  }, []);

  /** Stop whatever the agent is currently saying. */
  const stopPlayback = useCallback(() => {
    const p = player.current;
    player.current = null;
    if (!p) return;
    p.onended = null;
    p.onerror = null;
    p.pause();
    URL.revokeObjectURL(p.src);
  }, []);

  /** The caller talked over the agent: cut the agent off and listen. */
  const bargeIn = useCallback(() => {
    stopPlayback();
    agentSpeaking.current = false;
    turnInFlight.current = false;
    if (ws.current?.readyState === WebSocket.OPEN) {
      ws.current.send(JSON.stringify({ type: "barge" }));
    }
    // What the mic held so far is mostly the agent's own voice — drop it and
    // record the caller fresh. Their speech is already under way, so count it.
    discardTurn();
    resetTurnDetection();
    speechMs.current = BARGE_MS;
    setStatus("listening");
  }, [stopPlayback, discardTurn, resetTurnDetection]);

  /** Watch the mic level and call flushTurn once the caller goes quiet. */
  const watchLevel = useCallback(
    (analyser, buffer) => {
      let ambient = [];
      let lastPaint = 0;

      const tick = () => {
        raf.current = requestAnimationFrame(tick);

        // Deaf while the agent talks. Without this the loop measures the
        // agent's own voice coming back through the speakers, decides the
        // caller spoke, and sends the agent its own words as a question.
        // Barge-in is the one exception: listen for a voice clearly louder
        // than the agent's own echo, measured fresh for every clip.
        analyser.getFloatTimeDomainData(buffer);
        let sum = 0;
        for (const v of buffer) sum += v * v;
        const rms = Math.sqrt(sum / buffer.length);

        if (agentSpeaking.current) {
          speechMs.current = 0;
          silenceMs.current = 0;
          peakRms.current = 0;
          if (!bargeOn.current || noiseFloor.current === null) return;
          if (performance.now() - playStartedAt.current < ECHO_LEARN_MS) {
            echoPeak.current = Math.max(echoPeak.current, rms);
            return;
          }
          const bar = Math.max(
            echoPeak.current * BARGE_OVER_ECHO,
            noiseFloor.current * SPEECH_OVER_FLOOR * 1.5,
          );
          bargeMs.current = rms > bar ? bargeMs.current + 16 : 0;
          if (bargeMs.current >= BARGE_MS) {
            bargeMs.current = 0;
            bargeIn();
          }
          return;
        }

        // Learn how loud this room actually is. Sampling is counted in frames,
        // not wall-clock: the greeting arrives during this window and the loop
        // is deaf while it plays, so a clock-based window could close having
        // collected almost nothing and set the floor from noise.
        if (noiseFloor.current === null) {
          if (ambient.length < CALIBRATE_FRAMES) {
            ambient.push(rms);
            return;
          }
          const sorted = [...ambient].sort((a, b) => a - b);
          const median = sorted[Math.floor(sorted.length / 2)];
          noiseFloor.current = Math.min(Math.max(median, FLOOR_MIN), FLOOR_MAX);
          ambient = [];
        }

        const floor = noiseFloor.current;
        const silenceBar = floor * SILENCE_OVER_FLOOR;
        const speechBar = floor * SPEECH_OVER_FLOOR;

        // Feed the on-screen meter ~10x a second, not 60 — repainting every
        // frame makes React the bottleneck for no visible gain.
        if (performance.now() - lastPaint > 100) {
          lastPaint = performance.now();
          setLevel({ rms, speechBar });
        }

        // ~16ms a frame at 60fps; good enough for turn detection.
        peakRms.current = Math.max(peakRms.current, rms);

        // A room noisier than the measured floor never drops below the silence
        // bar, so the "caller stopped talking" test never fires and the call
        // stays deaf for its whole length — 31 seconds with zero turns is what
        // that looked like. Past this much continuous sound, send what we have
        // rather than waiting for a silence that is not coming.
        if (speechMs.current > MAX_TURN_MS) {
          const wasSpeech = peakRms.current >= speechBar;
          resetTurnDetection();
          if (wasSpeech) flushTurn();
          else {
            // Not a voice — just a loud room. Re-measure instead of fighting it.
            noiseFloor.current = null;
            ambient = [];
            discardTurn();
          }
          return;
        }

        if (rms > silenceBar) {
          speechMs.current += 16;
          silenceMs.current = 0;
        } else if (speechMs.current > MIN_SPEECH_MS) {
          silenceMs.current += 16;
          if (silenceMs.current > SILENCE_MS) {
            // Sustained sound alone isn't speech — a fan clears the silence bar
            // all day. Require a peak only a voice reaches before sending.
            const wasSpeech = peakRms.current >= speechBar;
            speechMs.current = 0;
            silenceMs.current = 0;
            peakRms.current = 0;
            if (wasSpeech) flushTurn();
            else discardTurn();
          }
        }
      };
      raf.current = requestAnimationFrame(tick);
    },
    [flushTurn, discardTurn, resetTurnDetection, bargeIn],
  );

  function startRecorder() {
    // Never run two recorders on one stream. Both push into the same chunk
    // buffer, so the turn ships doubled, interleaved audio that transcribes
    // to nonsense — and the agent answers the nonsense.
    if (recorder.current?.state === "recording") return;
    const mr = new MediaRecorder(stream.current, { mimeType: "audio/webm" });
    chunks.current = [];
    mr.ondataavailable = (e) => e.data.size && chunks.current.push(e.data);
    mr.onstop = () => {
      const blob = new Blob(chunks.current, { type: "audio/webm" });
      chunks.current = [];
      const drop = dropNext.current;
      dropNext.current = false;
      if (!drop && ws.current?.readyState === WebSocket.OPEN) {
        blob.arrayBuffer().then((buf) => ws.current?.send(buf));
      }
      // Keep the mic hot for the next turn unless the call is over.
      if (stream.current && ws.current?.readyState === WebSocket.OPEN) startRecorder();
    };
    mr.start();
    recorder.current = mr;
  }

  async function start() {
    setError(null);
    setTurns([]);
    setStatus("connecting");

    try {
      stream.current = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
    } catch {
      setError("Microphone access denied. Allow it in the browser and try again.");
      setStatus("idle");
      return;
    }

    audioCtx.current = new AudioContext();
    const source = audioCtx.current.createMediaStreamSource(stream.current);
    const analyser = audioCtx.current.createAnalyser();
    analyser.fftSize = 1024;
    source.connect(analyser);

    const params = new URLSearchParams();
    if (assistant) params.set("mode", "assistant");
    else if (agentId) params.set("agent", agentId);
    const key = callerKey();
    if (key) params.set("caller", key);
    const socket = new WebSocket(`${WS_BASE}?${params}`);
    socket.binaryType = "arraybuffer";
    ws.current = socket;

    socket.onmessage = (e) => {
      if (e.data instanceof ArrayBuffer) {
        // Replacing player.current only drops the reference — the old clip
        // keeps playing, which is how two replies ended up talking over each
        // other. Stop it properly first.
        stopPlayback();

        agentSpeaking.current = true;
        setStatus("speaking");

        // Throw away whatever the mic caught, don't send it. Plain stop() runs
        // onstop, which ships the blob — that buffer is the tail of the agent's
        // own previous sentence, and sending it is how it "heard" itself.
        discardTurn();

        const url = URL.createObjectURL(new Blob([e.data], { type: "audio/mpeg" }));
        const audio = new Audio(url);
        player.current = audio;
        playStartedAt.current = performance.now();
        echoPeak.current = 0;
        bargeMs.current = 0;

        const finished = () => {
          URL.revokeObjectURL(url);
          if (player.current !== audio) return; // superseded by a newer clip
          player.current = null;
          agentSpeaking.current = false;
          turnInFlight.current = false;
          resetTurnDetection();
          if (ws.current?.readyState === WebSocket.OPEN) {
            setStatus("listening");
            startRecorder();
          }
        };
        audio.onended = finished;
        audio.onerror = finished;
        audio.play().catch(finished);
        return;
      }

      const msg = JSON.parse(e.data);
      if (msg.type === "pong") return; // ignore pong
      if (msg.type === "connected") setCallId(msg.callId);
      else if (msg.type === "caller") setTurns((t) => [...t, { who: "caller", ...msg }]);
      else if (msg.type === "agent") setTurns((t) => [...t, { who: "agent", ...msg }]);
      else if (msg.type === "handoff")
        setTurns((t) => [...t, { who: "event", text: `Transferred to ${msg.agent}` }]);
      else if (msg.type === "thinking") setStatus("thinking");
      else if (msg.type === "idle") {
        // Nothing worth answering — release the turn so the caller can retry.
        turnInFlight.current = false;
        resetTurnDetection();
        setStatus("listening");
      } else if (msg.type === "warning" || msg.type === "error") {
        setError(msg.text);
        // A failed turn sends no audio, so nothing else would hand the mic
        // back — without this the call sits on "Thinking…" forever.
        turnInFlight.current = false;
        agentSpeaking.current = false;
        resetTurnDetection();
        if (ws.current?.readyState === WebSocket.OPEN) {
          setStatus("listening");
          if (recorder.current?.state !== "recording") startRecorder();
        }
      }
    };

    socket.onopen = () => {
      // The PIN goes in the first message, not the URL, so it never lands in
      // a proxy or server log. The backend only asks for it off this machine.
      if (assistant && pin) socket.send(JSON.stringify({ type: "auth", pin }));
      setStatus("greeting");
      noiseFloor.current = null;
      // Open the mic now. It used to wait for the greeting audio to finish,
      // which meant a greeting that never arrived — TTS quota, a slow model —
      // left the call with no recorder at all, permanently unable to hear.
      startRecorder();
      watchLevel(analyser, new Float32Array(analyser.fftSize));
      pingTimer.current = setInterval(() => {
        if (ws.current?.readyState === WebSocket.OPEN) {
          ws.current.send(JSON.stringify({ type: "ping" }));
        }
      }, 10000);
    };
    socket.onerror = () => setError("Could not reach the backend. Is it running on port 8000?");
    socket.onclose = () => setStatus((s) => (s === "idle" ? s : "ended"));
  }

  function hangUp() {
    player.current?.pause();
    cleanup();
    setStatus("ended");
  }

  const live = !["idle", "ended"].includes(status);

  /** Typed instead of spoken — handy on a phone in a noisy place. */
  function sendTyped(e) {
    e.preventDefault();
    const text = typed.trim();
    if (!text || ws.current?.readyState !== WebSocket.OPEN) return;
    stopPlayback();
    agentSpeaking.current = false;
    turnInFlight.current = true;
    turnAt.current = Date.now();
    discardTurn();
    ws.current.send(JSON.stringify({ type: "say", text }));
    setTyped("");
    setStatus("thinking");
  }

  function chooseMode(m) {
    setMode(m);
    store.set("vf_call_mode", m);
  }

  return (
    <div className="mx-auto max-w-3xl p-6 lg:p-8">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Web call</h1>
        <p className="mt-1 text-sm text-dim">
          {assistant
            ? "Your assistant: tell it what to do — make and send a bill, message a number, start a WhatsApp chat with an agent, check stock."
            : "Talk to your agent from the browser — same pipeline a real caller gets."}
        </p>
      </header>

      <Card className="mt-7">
        <div className="mb-4 flex gap-2">
          {[
            ["customer", "Customer call"],
            ["assistant", "My assistant"],
          ].map(([m, label]) => (
            <button
              key={m}
              onClick={() => chooseMode(m)}
              disabled={live}
              className={`rounded-full px-3.5 py-2 text-xs transition disabled:opacity-50 ${
                mode === m ? "bg-violet text-white" : "border border-line bg-panel text-dim hover:text-white"
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {assistant ? (
          <label className="block text-xs text-dim">
            Owner PIN <span className="text-dim/70">(only asked when you use this from your phone)</span>
            <input
              type="password"
              inputMode="numeric"
              autoComplete="off"
              value={pin}
              onChange={(e) => {
                setPin(e.target.value);
                store.set("vf_owner_pin", e.target.value);
              }}
              disabled={live}
              placeholder="ASSISTANT_PIN from backend/.env"
              className="mt-1.5 w-full rounded-xl border border-line bg-panel-2 px-3.5 py-2.5 text-sm text-white outline-none focus:border-violet/50 disabled:opacity-50"
            />
          </label>
        ) : (
        <>
        {/* Which persona answers. Without this every call went to whichever
            agent happened to be default. */}
        <label className="block text-xs text-dim">
          Agent
          <select
            value={agentId}
            onChange={(e) => setAgentId(e.target.value)}
            disabled={live}
            className="mt-1.5 w-full rounded-xl border border-line bg-panel-2 px-3.5 py-2.5 text-sm text-white outline-none focus:border-violet/50 disabled:opacity-50"
          >
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
                {a.isDefault ? " (default)" : ""}
              </option>
            ))}
          </select>
        </label>
        {agent && (
          <p className="mt-2 text-xs text-dim">
            Answers with: “{agent.greeting}”
          </p>
        )}
        </>
        )}
        <label className="mt-3 flex items-center gap-2 text-xs text-dim">
          <input
            type="checkbox"
            checked={allowBarge}
            onChange={(e) => setAllowBarge(e.target.checked)}
            className="accent-violet"
          />
          Let me interrupt the agent (barge-in) — turn off if speakers make it cut itself off
        </label>

        <div className="flex flex-col items-center py-4">
          <button
            onClick={live ? hangUp : start}
            className={`grid size-20 place-items-center rounded-full transition ${
              live
                ? "bg-rose text-white"
                : "bg-mint text-ink ring-8 ring-mint/15 hover:brightness-110"
            }`}
          >
            {status === "connecting" ? (
              <Loader2 className="size-7 animate-spin" />
            ) : live ? (
              <PhoneOff className="size-7" />
            ) : (
              <Phone className="size-8" />
            )}
          </button>

          <p className="mt-4 text-sm font-medium">{STATUS_TEXT[status]}</p>
          {callId && <p className="mt-1 font-mono text-xs text-dim">{callId}</p>}

          <Waveform
            bars={36}
            active={status === "listening" || status === "speaking"}
            className="mt-6 h-12"
          />

          {/* Mic level against the threshold the agent is actually using. When
              a call "doesn't respond", this says whether the mic is too quiet
              or the backend is at fault — instead of leaving it to guesswork. */}
          {live && level && (
            <div className="mt-5 w-full max-w-sm">
              <div className="relative h-2 overflow-hidden rounded-full bg-panel-2">
                <div
                  className={`h-full rounded-full transition-[width] duration-100 ${
                    level.rms >= level.speechBar ? "bg-mint" : "bg-line"
                  }`}
                  style={{
                    width: `${Math.min(100, (level.rms / (level.speechBar * 2)) * 100)}%`,
                  }}
                />
                <div className="absolute inset-y-0 left-1/2 w-px bg-white/40" />
              </div>
              <p className="mt-1.5 text-center text-[11px] text-dim">
                Mic level — speak past the line to be heard
              </p>
            </div>
          )}
        </div>

        {live && (
          <form onSubmit={sendTyped} className="mb-4 flex gap-2">
            <input
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder={assistant ? "Or type: bill banao, 2 cement bag 350 ke, Ravi ko bhej do" : "Or type a message…"}
              aria-label="Type a message"
              className="min-w-0 flex-1 rounded-xl border border-line bg-panel-2 px-3.5 py-2.5 text-sm outline-none focus:border-violet/50"
            />
            <button
              type="submit"
              disabled={!typed.trim()}
              className="rounded-xl bg-violet px-4 py-2.5 text-sm font-medium text-white transition hover:bg-violet-soft disabled:opacity-40"
            >
              Send
            </button>
          </form>
        )}

        {error && (
          <p className="flex items-start gap-2 rounded-xl border border-saffron/30 bg-saffron/5 px-4 py-3 text-xs text-saffron">
            <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
            {error}
          </p>
        )}
      </Card>

      {turns.length > 0 && (
        <Card className="mt-4">
          <h2 className="text-[15px] font-semibold">Transcript</h2>
          <div className="mt-4 space-y-3">
            {turns.map((t, i) =>
              t.who === "event" ? (
                <p key={i} className="text-center text-[11px] text-dim">— {t.text} —</p>
              ) : (
              <div key={i} className={`flex ${t.who === "agent" ? "justify-end" : "justify-start"}`}>
                <div
                  className={`max-w-[80%] rounded-2xl px-3.5 py-2.5 text-sm ${
                    t.who === "agent"
                      ? "rounded-tr-sm bg-violet/20 ring-1 ring-violet/30"
                      : "rounded-tl-sm bg-panel-2 ring-1 ring-line"
                  }`}
                >
                  {t.text}
                  {(t.language || (t.who === "agent" && t.agent)) && (
                    <span className="mt-1 block text-[10px] text-dim">{t.language ?? t.agent}</span>
                  )}
                </div>
              </div>
              ),
            )}
          </div>
        </Card>
      )}

      {status === "ended" && callId && (
        <p className="mt-4 text-center text-xs text-dim">
          This call is being saved — recording, summary and full transcript appear on the{" "}
          <Link to="/app/calls" className="text-violet-soft underline">Calls</Link> page in a few seconds.
        </p>
      )}

      <p className="mt-4 text-center text-xs text-dim">
        Every web call is recorded and saved with a text summary. Speak over the agent to
        interrupt it; it remembers you the next time you call from this browser.
      </p>
    </div>
  );
}
