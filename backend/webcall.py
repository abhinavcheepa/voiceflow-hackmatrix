"""Browser calling — talk to the agent from the dashboard, no phone needed.

One WebSocket per call. The browser detects when the caller stops speaking,
sends that turn as one audio blob, and this module runs the same pipeline
WhatsApp voice notes use:

    audio in -> transcribe -> LLM -> cloned-voice TTS -> audio back

Half-duplex by design: the caller speaks, then the agent speaks. Barge-in needs
the streaming media loop (Phases 6-11) and is deliberately not faked here.
"""

import logging
import os
import time
import uuid
from datetime import datetime, timezone

import brain
import db
import voice

log = logging.getLogger("voiceflow.webcall")

BUSINESS_NAME = os.getenv("BUSINESS_NAME", "our business")
OWNER_NAME = os.getenv("OWNER_NAME", "the owner")

# Spoken conversation needs a different brief from WhatsApp: no formatting, no
# lists, and short enough that the caller isn't left waiting.
CALL_PROMPT = f"""You are answering a phone call for {BUSINESS_NAME}, on behalf of {OWNER_NAME}.

{{persona}}

- Reply in the SAME language the caller used. Never switch.
- This is speech, not text. One or two short sentences. No bullet points, no
  emoji, no markdown, no numbered lists.
- Answer questions about timings, pricing and availability, and take bookings.
- Never invent prices, stock or appointment slots. If you don't know, say you'll
  check and confirm.
- If the caller says goodbye, close warmly in one short line.
"""

GREETING = os.getenv(
    "WEBCALL_GREETING",
    f"Namaste! {BUSINESS_NAME} me aapka swagat hai. Main aapki kya madad kar sakti hoon?",
)

# Anything shorter is a click, a cough, or the mic opening — not a turn.
MIN_AUDIO_BYTES = 2000


class Session:
    """One browser call. History lives here, not in the WhatsApp tables."""

    def __init__(self, agent_id: int | None = None) -> None:
        self.id = f"WEB-{uuid.uuid4().hex[:8].upper()}"
        self.history: list[dict] = []
        self.started = time.time()
        self.turns = 0
        # Whichever agent is selected supplies the persona; falls back to the
        # module defaults if the table is somehow empty.
        self.agent = db.get_agent(agent_id) or {}
        self.language = self.agent.get("language", "Hindi")

    @property
    def prompt(self) -> str:
        return CALL_PROMPT.format(persona=self.agent.get("prompt", ""))

    @property
    def greeting(self) -> str:
        return self.agent.get("greeting") or GREETING

    def add(self, role: str, content: str) -> None:
        self.history.append({"role": role, "content": content})
        # Keep the prompt bounded on long calls; the last 16 turns is plenty
        # of context and keeps latency flat.
        del self.history[:-16]

    def open_record(self) -> None:
        db.upsert_call(
            self.id,
            id=self.id,
            phone="Web call",
            status="active",
            agent_id=self.agent.get("id"),
            started_at=datetime.now(timezone.utc).isoformat(),
        )

    def close_record(self) -> None:
        transcript = "\n".join(
            f"{'Caller' if m['role'] == 'user' else 'Agent'}: {m['content']}"
            for m in self.history
        )
        db.upsert_call(
            self.id,
            status="ended",
            duration_sec=round(time.time() - self.started),
            language=self.language,
            intent="Web call",
            outcome="Answered" if self.turns else "Missed",
            transcript=transcript,
        )


async def speak(ws, session: Session, text: str) -> None:
    """Send one agent line: the words first, then the audio."""
    session.add("assistant", text)
    await ws.send_json({"type": "agent", "text": text})
    try:
        audio = await voice.synthesize(text, session.language)
        await ws.send_bytes(audio)
    except Exception as e:  # noqa: BLE001 - a call without audio still beats a dropped call
        log.warning("TTS failed on %s: %s", session.id, e)
        await ws.send_json({"type": "warning", "text": f"Voice unavailable: {e}"})


async def handle_turn(ws, session: Session, audio: bytes) -> None:
    """One caller turn, start to finish."""
    if len(audio) < MIN_AUDIO_BYTES:
        await ws.send_json({"type": "idle"})
        return

    await ws.send_json({"type": "thinking"})

    try:
        heard = await brain.transcribe(audio, "turn.webm")
    except Exception as e:  # noqa: BLE001
        await ws.send_json({"type": "error", "text": f"Could not hear that: {e}"})
        return

    if not heard.strip():
        await ws.send_json({"type": "idle"})
        return

    session.turns += 1
    session.language = brain.detect_language(heard)
    session.add("user", heard)
    await ws.send_json({"type": "caller", "text": heard, "language": session.language})

    try:
        answer = await brain.reply("", heard, history=session.history[:-1], prompt=session.prompt)
    except Exception as e:  # noqa: BLE001
        await ws.send_json({"type": "error", "text": f"Could not answer: {e}"})
        return

    await speak(ws, session, answer)
