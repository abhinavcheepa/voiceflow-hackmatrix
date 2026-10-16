import { useCallback, useEffect, useRef, useState } from "react";
import { Phone, PhoneOff, Loader2, AlertTriangle } from "lucide-react";
import { Card, Waveform } from "../ui.jsx";
import { API, useApi } from "../api.js";

const WS_BASE = `${API.replace(/^http/, "ws")}/api/web-call/ws`;

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
  }, []);

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
        if (agentSpeaking.current) {
          speechMs.current = 0;
          silenceMs.current = 0;
          peakRms.current = 0;
          return;
        }

        analyser.getFloatTimeDomainData(buffer);
        let sum = 0;
        for (const v of buffer) sum += v * v;
        const rms = Math.sqrt(sum / buffer.length);

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
    [flushTurn, discardTurn, resetTurnDetection],
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

    const socket = new WebSocket(agentId ? `${WS_BASE}?agent=${agentId}` : WS_BASE);
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

  return (
    <div className="mx-auto max-w-3xl p-6 lg:p-8">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Web call</h1>
        <p className="mt-1 text-sm text-dim">
          Talk to your agent from the browser — same pipeline a real caller gets.
        </p>
      </header>

      <Card className="mt-7">
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
            {turns.map((t, i) => (
              <div key={i} className={`flex ${t.who === "agent" ? "justify-end" : "justify-start"}`}>
                <div
                  className={`max-w-[80%] rounded-2xl px-3.5 py-2.5 text-sm ${
                    t.who === "agent"
                      ? "rounded-tr-sm bg-violet/20 ring-1 ring-violet/30"
                      : "rounded-tl-sm bg-panel-2 ring-1 ring-line"
                  }`}
                >
                  {t.text}
                  {t.language && (
                    <span className="mt-1 block text-[10px] text-dim">{t.language}</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}

      <p className="mt-4 text-center text-xs text-dim">
        Half-duplex: the agent finishes speaking before it listens again.
        Interrupting mid-sentence needs the streaming media loop.
      </p>
    </div>
  );
}
