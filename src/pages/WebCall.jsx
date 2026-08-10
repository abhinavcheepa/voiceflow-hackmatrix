import { useCallback, useEffect, useRef, useState } from "react";
import { Phone, PhoneOff, Loader2, AlertTriangle } from "lucide-react";
import { Card, Waveform } from "../ui.jsx";
import { API, useApi } from "../api.js";

const WS_BASE = `${API.replace(/^http/, "ws")}/api/web-call/ws`;

// Turn detection. The browser decides when the caller has finished speaking so
// the backend never has to guess — it just receives one blob per turn.
//
// These thresholds matter more than they look. Send near-silence to Whisper and
// it does not return nothing — it returns stock subtitle phrases it was trained
// on ("Thank you.", "you", "I'm going to go to the next episode…"), and the
// agent then answers that nonsense. The gate below is what stops that.
const SILENCE_RMS = 0.02; // below this counts as silence
const SPEECH_RMS = 0.045; // a real voice peaks well above room noise
const SILENCE_MS = 1100; // how long silence must hold to end a turn
const MIN_SPEECH_MS = 700; // ignore coughs, clicks and door slams

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
  const dropNext = useRef(false);
  const raf = useRef(null);

  const cleanup = useCallback(() => {
    cancelAnimationFrame(raf.current);
    recorder.current?.state === "recording" && recorder.current.stop();
    stream.current?.getTracks().forEach((t) => t.stop());
    audioCtx.current?.close().catch(() => {});
    ws.current?.close();
    recorder.current = stream.current = audioCtx.current = ws.current = null;
  }, []);

  useEffect(() => cleanup, [cleanup]);

  /** Send whatever has been recorded as one turn, then start a fresh recorder. */
  const flushTurn = useCallback(() => {
    const mr = recorder.current;
    if (!mr || mr.state !== "recording") return;
    mr.stop(); // onstop ships the blob and restarts recording
  }, []);

  /** Same, but throw the audio away — it was noise, not a turn. */
  const discardTurn = useCallback(() => {
    const mr = recorder.current;
    if (!mr || mr.state !== "recording") return;
    dropNext.current = true;
    mr.stop();
  }, []);

  /** Watch the mic level and call flushTurn once the caller goes quiet. */
  const watchLevel = useCallback(
    (analyser, buffer) => {
      const tick = () => {
        analyser.getFloatTimeDomainData(buffer);
        let sum = 0;
        for (const v of buffer) sum += v * v;
        const rms = Math.sqrt(sum / buffer.length);

        // ~16ms a frame at 60fps; good enough for turn detection.
        peakRms.current = Math.max(peakRms.current, rms);

        if (rms > SILENCE_RMS) {
          speechMs.current += 16;
          silenceMs.current = 0;
        } else if (speechMs.current > MIN_SPEECH_MS) {
          silenceMs.current += 16;
          if (silenceMs.current > SILENCE_MS) {
            // Sustained sound alone isn't speech — a fan clears SILENCE_RMS all
            // day. Require a peak only a voice reaches before sending anything.
            const wasSpeech = peakRms.current >= SPEECH_RMS;
            speechMs.current = 0;
            silenceMs.current = 0;
            peakRms.current = 0;
            if (wasSpeech) flushTurn();
            else discardTurn();
          }
        }
        raf.current = requestAnimationFrame(tick);
      };
      raf.current = requestAnimationFrame(tick);
    },
    [flushTurn, discardTurn],
  );

  function startRecorder() {
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
        const url = URL.createObjectURL(new Blob([e.data], { type: "audio/mpeg" }));
        player.current = new Audio(url);
        setStatus("speaking");
        // Don't record while the agent talks, or it transcribes its own voice.
        recorder.current?.state === "recording" && recorder.current.stop();
        player.current.onended = () => {
          URL.revokeObjectURL(url);
          if (ws.current?.readyState === WebSocket.OPEN) {
            setStatus("listening");
            startRecorder();
          }
        };
        player.current.play().catch(() => setStatus("listening"));
        return;
      }

      const msg = JSON.parse(e.data);
      if (msg.type === "connected") setCallId(msg.callId);
      else if (msg.type === "caller") setTurns((t) => [...t, { who: "caller", ...msg }]);
      else if (msg.type === "agent") setTurns((t) => [...t, { who: "agent", ...msg }]);
      else if (msg.type === "thinking") setStatus("thinking");
      else if (msg.type === "idle") setStatus("listening");
      else if (msg.type === "warning" || msg.type === "error") {
        setError(msg.text);
        // A failed turn sends no audio, so nothing else would hand the mic
        // back — without this the call sits on "Thinking…" forever.
        if (ws.current?.readyState === WebSocket.OPEN) {
          setStatus("listening");
          if (recorder.current?.state !== "recording") startRecorder();
        }
      }
    };

    socket.onopen = () => {
      setStatus("greeting");
      watchLevel(analyser, new Float32Array(analyser.fftSize));
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
