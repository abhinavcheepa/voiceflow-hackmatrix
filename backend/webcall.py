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
import kb
import voice

log = logging.getLogger("voiceflow.webcall")

BUSINESS_NAME = os.getenv("BUSINESS_NAME", "our business")
OWNER_NAME = os.getenv("OWNER_NAME", "the owner")

# Spoken conversation needs a different brief from WhatsApp: no formatting, no
# lists, and short enough that the caller isn't left waiting.
CALL_PROMPT = """You are answering a phone call for {business}, on behalf of {owner}.

{persona}

- Reply in the SAME language the caller used. Never switch.
- This is speech, not text. One or two short sentences. No bullet points, no
  emoji, no markdown, no numbered lists.
- Only state facts given above. If a fact was not given to you, you do not
  know it. Never guess.

You have NO access to a calendar, booking system or stock list. You cannot see
what is free. So:
- Never say a specific time, date or item IS available or IS booked.
- When someone asks for a time, take down their name and the time they want,
  and tell them it will be confirmed shortly. Do not confirm it yourself.
- Opening hours are not availability. "We are open until 7" is fine;
  "7 o'clock is free" is not.

CAPABILITIES — you can do these using your tools:

1. BILLING: When the caller says "bill banao", "invoice banao", "bill create karo",
   or anything about making a bill:
   - Use create_bill first (ask customer name)
   - Then for each item, use add_bill_item (ask name, quantity, rate)
   - Keep adding items until they say "bas", "done", "ho gaya"
   - Then use finalize_bill to complete
   - Read out the total to the customer

2. STOCK MANAGEMENT: When the caller says "stock entry karo", "maal aaya hai",
   "stock update karo", or asks about stock:
   - For checking: use check_stock with the item name
   - For adding/removing: use add_stock_entry with item, quantity, and type (in/out)

3. BOOKINGS: When someone wants to book, use book_appointment.

4. ESCALATION: When you cannot help, use escalate_to_human.

5. LOOKUP: For any question about the business — prices, people, timings,
   policies, stock, a past order — use search_knowledge first if the REFERENCE
   below does not already answer it. Never guess.

- If the caller says goodbye, close warmly in one short line.
"""

GREETING = os.getenv(
    "WEBCALL_GREETING",
    f"Namaste! {BUSINESS_NAME} me aapka swagat hai. Main aapki kya madad kar sakti hoon?",
)

# Anything shorter is a click, a cough, or the mic opening — not a turn.
MIN_AUDIO_BYTES = 2000

# Spoken when the LLM fails, so the caller hears something instead of silence.
# Gendered to match the voice — "paayi" from a male voice is the same mismatch
# the prompt rule fixes for replies.
FALLBACK = {
    "female": "Maaf kijiye, ek baar phir se boliye — main sun nahi paayi.",
    "male": "Maaf kijiye, ek baar phir se boliye — main sun nahi paaya.",
}


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

        # The voice is fixed for the whole call, on purpose. `language` follows
        # whatever the caller last spoke, and TTS used to follow it too — so a
        # Hindi question then an English one made the agent answer in a
        # different person's voice, mid-conversation. A receptionist does not
        # swap bodies when you switch language; the reply wording changes, the
        # speaker does not.
        self.voice_language = self.language

    @property
    def prompt(self) -> str:
        # The agent's own name wins over BUSINESS_NAME from .env — otherwise
        # every agent introduces itself as whatever the env file says, which is
        # how "VoiceFlow Demo" ended up answering as a dental clinic.
        persona = self.agent.get("prompt", "")
        return CALL_PROMPT.format(
            business=self.agent.get("name") or BUSINESS_NAME,
            owner=OWNER_NAME,
            persona=f"{persona}\n\n{brain.facts_block(self.agent.get('facts'))}".strip(),
        )

    @property
    def greeting(self) -> str:
        # The stored greeting is the owner's text; only first-person verb forms
        # are corrected so it agrees with the voice.
        return brain.fix_gender(self.agent.get("greeting") or GREETING, brain.agent_gender())

    def add(self, role: str, content: str) -> None:
        self.history.append({"role": role, "content": content})
        # Six turns, not sixteen. Every turn resends the whole window, and the
        # free tier caps tokens per MINUTE — a call doing a turn every ten
        # seconds hits that ceiling on history alone, and the backoff that
        # follows looks exactly like the model being slow.
        del self.history[:-6]

    def open_record(self) -> None:
        db.upsert_call(
            self.id,
            id=self.id,
            phone="Web call",
            status="active",
            agent_id=self.agent.get("id"),
            started_at=datetime.now(timezone.utc).isoformat(),
        )

    def transcript(self) -> str:
        return "\n".join(
            f"{'Caller' if m['role'] == 'user' else 'Agent'}: {m['content']}"
            for m in self.history
        )

    def close_record(self) -> None:
        """Guaranteed minimal record — sync, no network, always runs."""
        db.upsert_call(
            self.id,
            status="ended",
            duration_sec=round(time.time() - self.started),
            language=self.language,
            intent="Web call",
            outcome="Answered" if self.turns else "Missed",
            transcript=self.transcript(),
        )


async def analyse_and_store(session: "Session") -> None:
    """Enrich a finished call with our own analysis (summary, real intent/outcome,
    caller name, success). Best-effort: close_record already saved the basics, so
    a failure here just leaves the call without a summary, never unrecorded.

    Also extracts Q&A pairs for adaptive learning — the agent gets smarter with
    each conversation."""
    if not session.turns:
        return
    result = await brain.analyse_call(session.transcript(), session.language)
    if not result:
        return
    fields = {k: result[k] for k in ("summary", "name", "intent", "outcome") if k in result}
    if "success" in result:
        fields["success"] = int(result["success"])
    if fields:
        db.upsert_call(session.id, **fields)

    # Adaptive learning — extract Q&A pairs for future use.
    try:
        import adaptive
        await adaptive.extract_learnings(
            session.transcript(),
            agent_id=session.agent.get("id"),
            language=session.language,
        )
    except Exception as e:  # noqa: BLE001
        log.debug("learning extraction skipped for %s: %s", session.id, e)


async def speak(ws, session: Session, text: str) -> None:
    """Send one agent line: the words first, then the audio."""
    session.add("assistant", text)
    await ws.send_json({"type": "agent", "text": text})
    try:
        audio = await voice.synthesize(text, session.voice_language)
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
        agent_id = session.agent.get("id")
        # Retrieve using the last couple of user turns, not just this one. A
        # follow-up answer like "Computer Science and Engineering" to the agent's
        # "which department?" carries no "faculty/count" intent on its own, so on
        # its own it retrieved the wrong chunks. Joining the recent user turns
        # keeps the original question's keywords in the retrieval query.
        recent_user = [m["content"] for m in session.history if m["role"] == "user"][-2:]
        retrieval_query = " ".join(recent_user) or heard
        kb_context = kb.context_block(agent_id, retrieval_query)
        answer = await brain.reply("", heard, history=session.history[:-1],
                                   prompt=session.prompt, kb_context=kb_context,
                                   tools_ctx={"channel": "call", "agent_id": agent_id},
                                   agent_id=agent_id)
    except Exception as e:  # noqa: BLE001
        # Free LLM endpoints rate-limit under load. Say something rather than
        # leaving the caller in silence — the call stays up either way.
        log.warning("LLM failed on %s: %s", session.id, e)
        await ws.send_json({"type": "error", "text": f"Could not answer: {e}"})
        await speak(ws, session, os.getenv("WEBCALL_FALLBACK") or FALLBACK[brain.agent_gender()])
        return

    await speak(ws, session, answer)
