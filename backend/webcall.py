"""Browser calling — talk to the agent from the dashboard, no phone needed.

One WebSocket per call. The browser detects when the caller stops speaking,
sends that turn as one audio blob, and this module runs the same pipeline
WhatsApp voice notes use:

    audio in -> transcribe -> LLM -> cloned-voice TTS -> audio back

Turn-based: the caller speaks, then the agent speaks. The browser lets the
caller cut the agent off (barge-in) by stopping playback and telling us, so the
model knows its last line wasn't fully heard. Every call is recorded (audio +
a text record with summary) by recording.py.
"""

import asyncio
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone

import brain
import db
import kb
import recording
import tools
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

# The owner's own assistant ("Jarvis" mode). Same audio loop, different job:
# the person speaking runs the business and gives orders; the agent carries
# them out with tools instead of answering a customer.
ASSISTANT_PROMPT = """You are {owner}'s personal operations assistant for {business}, like
Jarvis for Tony Stark. The person speaking is the OWNER, not a customer.

- Carry out what the owner asks using your tools: make and send bills, check or
  update stock, send WhatsApp messages, start a WhatsApp conversation with a
  number and let an agent handle it, look up bookings, bills, calls and chats.
- Chain the steps yourself. "Bill banao aur <number> par bhej do" means
  create_bill, add_bill_item for each item, finalize_bill, then send_bill.
- If something essential is missing (an item's quantity or rate, the number),
  ask for just that, briefly.
- A phone number comes ONLY from the owner's words, NUMBERS below, CONTACTS
  below, or a tool result. Never write or guess one yourself.
- The owner may name a person instead of a number ("Lavish bhai ko bhejo"):
  pass the name as `phone` — saved contacts are looked up (CONTACTS below shows
  likely matches). When they give a number for a name, pass both (phone +
  name) so it is saved for next time.
- Never ask again for a number or message the owner already gave in this call
  (see NUMBERS below and the conversation).
- To send anything, call the send tool straight away — do NOT ask "bhej doon?"
  or "theek hai?" yourself first. The tool replies "NOT SENT YET" and the owner
  is asked; stop there — their yes sends it. Never claim something was sent
  unless a tool result said so.
- "Allow / stop replying to this number", "auto-reply on/off": use
  auto_reply_settings. NEVER send someone a message to change a setting.
- After a tool does something, tell the owner what was done in one sentence,
  based on its result ("Ho gaya — …"). Don't skip straight to "aur kuch?".
- If the owner corrects a message waiting for their yes (spelling, a word, the
  number), call the same send tool again with the corrected details.
- Every word you write is spoken aloud: no notes or stage directions like
  "(waiting for details)", no ALL-CAPS words. The product is "VoiceFlow AI".
- Speak like a sharp assistant: one or two short sentences, no lists, no
  markdown. Reply in the owner's language (Hindi/Hinglish/English).
"""

ASSISTANT_GREETING = "Ji, boliye, kya karna hai?"

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
    "female": "Maaf kijiye, ek baar phir se boliye, main sun nahi paayi.",
    "male": "Maaf kijiye, ek baar phir se boliye, main sun nahi paaya.",
}


class Session:
    """One browser call. History lives here, not in the WhatsApp tables."""

    def __init__(self, agent_id: int | None = None, caller_key: str | None = None,
                 assistant: bool = False) -> None:
        self.id = f"WEB-{uuid.uuid4().hex[:8].upper()}"
        self.history: list[dict] = []
        # The full call, never trimmed: (seconds-in, speaker, text). `history`
        # is cut to six entries for the token budget, so the transcript, the
        # analysis and the text record must not be built from it.
        self.log: list[tuple[float, str, str]] = []
        self.started = time.time()
        self.turns = 0
        # Whichever agent is selected supplies the persona; falls back to the
        # module defaults if the table is somehow empty.
        self.agent = db.get_agent(agent_id) or {}
        self.agents_seen = [self.agent.get("name") or "Agent"]  # grows on each handoff
        self.language = self.agent.get("language", "Hindi")
        self.recorder = recording.Recorder(self.id)

        # A browser-held id, so the same person calling again is remembered.
        self.caller_key = caller_key if caller_key and CALLER_KEY_RE.match(caller_key) else None
        self.memory = "" if assistant else memory_block(self.caller_key)
        # Owner mode keeps ONE tool context for the whole call: a confirmation
        # asked in one turn and given in the next, or a bill being built over
        # several turns, must survive between them.
        self.assistant = assistant
        self.numbers: list[str] = []  # phone numbers the owner said, kept past the window
        self.vocab = stt_vocab(self.agent, with_contacts=assistant)
        self.tools_ctx = {"channel": "assistant", "owner": True, "agent_id": self.agent.get("id"),
                          "_turn": 0} if assistant else None

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
        if self.assistant:
            base = ASSISTANT_PROMPT.format(owner=OWNER_NAME,
                                           business=self.agent.get("name") or BUSINESS_NAME)
            if self.numbers:
                said = ", ".join(f"{n[:-10]} {n[-10:-5]} {n[-5:]}" for n in self.numbers)
                base += (f"\nNUMBERS the owner has said in this call (latest last): {said}. "
                         "Use these — don't ask for them again.")
            return base
        persona = self.agent.get("prompt", "")
        base = CALL_PROMPT.format(
            business=self.agent.get("name") or BUSINESS_NAME,
            owner=OWNER_NAME,
            persona=f"{persona}\n\n{brain.facts_block(self.agent.get('facts'))}".strip(),
        )
        return f"{base}\n\n{self.memory}" if self.memory else base

    @property
    def greeting(self) -> str:
        # The stored greeting is the owner's text; only first-person verb forms
        # are corrected so it agrees with the voice.
        text = ASSISTANT_GREETING if self.assistant else (self.agent.get("greeting") or GREETING)
        return brain.fix_gender(text, brain.agent_gender())

    def note(self, who: str, text: str) -> None:
        self.log.append((time.time() - self.started, who, text))

    def add(self, role: str, content: str) -> None:
        self.note("Caller" if role == "user" else (self.agent.get("name") or "Agent"), content)
        self.history.append({"role": role, "content": content})
        if self.assistant and role == "user":
            for raw in re.findall(r"\d[\d\s-]{8,}\d", content):
                n = db.normalize_phone(raw)
                if len(n) == 12:
                    self.numbers = [x for x in self.numbers if x != n][-4:] + [n]
        # Six turns, not sixteen. Every turn resends the whole window, and the
        # free tier caps tokens per MINUTE — a call doing a turn every ten
        # seconds hits that ceiling on history alone, and the backoff that
        # follows looks exactly like the model being slow. The owner's
        # assistant gets more: a task (dictate a message, give the number, fix
        # a word, confirm) spans many turns, and with six it forgot the number
        # it had been given and asked again.
        del self.history[:-(20 if self.assistant else 6)]

    def interrupted(self) -> None:
        """The caller talked over the agent's last line. Tell the model, so it
        doesn't assume they heard the rest of it."""
        for m in reversed(self.history):
            if m["role"] == "assistant":
                if not m["content"].endswith(INTERRUPTED):
                    m["content"] += INTERRUPTED
                break
        self.note("—", "caller interrupted the agent")

    def squad(self) -> list[dict]:
        """The other agents this call can be handed to (Vapi's "squads")."""
        me = self.agent.get("id")
        return [
            {"id": a["id"], "name": a["name"], "about": (a.get("prompt") or "")[:160]}
            for a in db.get_agents() if a["id"] != me
        ]

    def switch_agent(self, agent_id: int) -> bool:
        new = db.get_agent(agent_id)
        if not new:
            return False
        self.note("—", f"transferred from {self.agent.get('name') or 'agent'} to {new['name']}")
        self.agent = new
        self.agents_seen.append(new["name"])
        return True

    def open_record(self) -> None:
        db.upsert_call(
            self.id,
            id=self.id,
            phone="Web call",
            status="active",
            agent_id=self.agent.get("id"),
            caller_key=self.caller_key,
            started_at=datetime.now(timezone.utc).isoformat(),
        )

    def transcript(self) -> str:
        return "\n".join(f"{who}: {text}" for _, who, text in self.log if who != "—")

    def close_record(self) -> None:
        """Guaranteed minimal record — sync, no network, always runs."""
        db.upsert_call(
            self.id,
            status="ended",
            duration_sec=round(time.time() - self.started),
            language=self.language,
            intent="Assistant" if self.assistant else "Web call",
            outcome="Answered" if self.turns else "Missed",
            transcript=self.transcript(),
        )


# The owner's answer to a read-back. A yes with digits in it ("haan, par number
# 98123…") is a correction, not a yes — that goes to the model instead.
_YES_RE = re.compile(
    r"^\W*(?:(?:haan|haa|han|ha|hanji|haanji|ji haan|ji|yes|yeah|yep|ok|okay|theek|thik|sahi|"
    r"bilkul|confirm|done|bhej|kar do|karo|send)(?![a-z])|हाँ|हां|हा|जी|ठीक|भेज|कर दो|ओके)",
    re.IGNORECASE,
)
NO_RE = re.compile(r"(?<![a-z])(?:nahi|nahin|na|mat|cancel|ruko|wait|no|stop|galat)(?![a-z])"
                   r"|नहीं|नही|मत|रुको|कैंसल", re.IGNORECASE)


# The agent claiming a send. Checked against what actually went out.
SENT_CLAIM_RE = re.compile(
    r"bhej (?:diya|di|dia)|bhej (?:raha|rahi) (?:hoon|hu|hun)|bhej (?:deta|deti|dunga|dungi)|"
    r"भेज (?:दिया|दी|देता|देती|दूँगा|दूँगी|दूंगा|दूंगी)|भेजा गया|भेज (?:रहा|रही) (?:हूँ|हूं)|"
    r"\bsent\b|has been sent|chala gaya|चला गया",
    re.IGNORECASE,
)
NOT_SENT = ("Abhi kuch bheja nahi gaya hai. Kise aur kya bhejna hai, bata dijiye — bhejne se "
            "pehle main padh ke sunaungi.")
ASK_NUMBER = "Kis number par bhejna hai? Number ya saved naam bata dijiye."
# A reply that already reports a finished action.
DONE_RE = re.compile(r"ho gaya|ho gayi|kar diya|kar di\b|bhej diya|allow|band kar|हो गया|कर दिया|भेज दिया",
                     re.IGNORECASE)
_PHONE_IN_TEXT = re.compile(r"\d[\d\s-]{8,}\d")


def stt_vocab(agent: dict, with_contacts: bool = False) -> str:
    """Words speech-to-text should expect: the product, the business, the
    agents and — for the owner — the people they message by name."""
    names = ["VoiceFlow AI", "WhatsApp", BUSINESS_NAME, OWNER_NAME, agent.get("name") or ""]
    names += [a["name"] for a in db.get_agents()]
    if with_contacts:
        for c in db.get_contacts(limit=80):
            name = re.sub(r"[^\w\s]", " ", c.get("name") or "").strip()
            # Real names only: not a number, not a long business/channel title.
            if (name and name.lower() != "unknown" and not any(ch.isdigit() for ch in name)
                    and len(name.split()) <= 3 and len(name) <= 24):
                names.append(name)
    out, seen = [], set()
    for n in names:
        if n and n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return ", ".join(out)


def invented_number(text: str, session: "Session") -> bool:
    """Does `text` contain a phone number nobody gave — not said by the owner,
    not a saved contact?"""
    for raw in _PHONE_IN_TEXT.findall(text):
        n = db.normalize_phone(raw)
        if len(n) == 12 and n not in session.numbers and not tools.is_contact(n):
            return True
    return False


def is_plain_yes(text: str) -> bool:
    return bool(_YES_RE.match(text)) and not NO_RE.search(text) and not re.search(r"\d", text)


# Browser-generated caller id: a UUID, but accept any short safe token.
CALLER_KEY_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
INTERRUPTED = " [caller interrupted — they may not have heard the end]"


def memory_block(caller_key: str | None) -> str:
    """What the agent remembers about this caller from earlier calls.

    Vapi's "sessions": continuity across calls. Built from our own post-call
    summaries, so it costs no extra model call at the start of a call.
    """
    if not caller_key:
        return ""
    past = db.past_calls_for(caller_key, limit=3)
    if not past:
        return ""
    name = next((p["name"] for p in past if p["name"] and p["name"] != "Unknown"), None)
    lines = [f"- {p['time']}: {p['summary']}" for p in past]
    return (
        "MEMORY — this caller has called before"
        + (f"; their name is {name}" if name else "")
        + ". Most recent first:\n" + "\n".join(lines)
        + "\nUse it naturally (greet them back, follow up on what they wanted). "
          "Do not read this list out."
    )


async def finalize(session: "Session") -> None:
    """Everything after hang-up: analysis, the stitched recording, the text
    record. Each step is best-effort and independent — close_record already
    saved the basics, so a failure here never loses the call."""
    analysis: dict = {}
    if session.turns:
        try:
            analysis = await brain.analyse_call(session.transcript(), session.language)
        except Exception as e:  # noqa: BLE001
            log.warning("analysis failed for %s: %s", session.id, e)
    fields = {k: analysis[k] for k in ("summary", "name", "intent", "outcome") if k in analysis}
    if "success" in analysis:
        fields["success"] = int(analysis["success"])

    # ffmpeg is blocking; keep it off the event loop so other calls carry on.
    if await asyncio.to_thread(session.recorder.finish):
        fields["recording_url"] = f"/api/calls/{session.id}/recording"
    text = recording.render_text(
        {"id": session.id, "agent": " → ".join(session.agents_seen), "started": session.started,
         "duration": time.time() - session.started, "language": session.language,
         "outcome": "Answered" if session.turns else "Missed"},
        session.log, analysis,
    )
    if recording.write_text(session.id, text):
        fields["record_url"] = f"/api/calls/{session.id}/record.txt"
    if fields:
        db.upsert_call(session.id, **fields)

    # Adaptive learning — extract Q&A pairs for future use. Not from the owner's
    # commands: "send bill to 98xxx" is not a customer FAQ.
    if session.turns and not session.assistant:
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
    await ws.send_json({"type": "agent", "text": text, "agent": session.agent.get("name")})
    try:
        audio = await voice.synthesize(text, session.voice_language)
        session.recorder.add("agent", audio, voice.OUTPUT_EXT)
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
        heard = await brain.transcribe(audio, "turn.webm", vocab=session.vocab)
    except Exception as e:  # noqa: BLE001
        await ws.send_json({"type": "error", "text": f"Could not hear that: {e}"})
        return

    if not heard.strip():
        await ws.send_json({"type": "idle"})
        return

    # Only turns that held speech go into the recording — noise blobs that
    # transcribed to nothing would just be dead air in the file.
    session.recorder.add("caller", audio, "webm")
    await respond(ws, session, heard)


async def respond(ws, session: Session, heard: str) -> None:
    """Answer one caller turn — spoken (after transcription) or typed."""
    heard = heard.strip()[:2000]
    if not heard:
        return
    session.turns += 1
    session.language = brain.detect_language(heard)
    session.add("user", heard)
    await ws.send_json({"type": "caller", "text": heard, "language": session.language})

    agent_id = session.agent.get("id")
    if session.assistant:
        tools_ctx = session.tools_ctx
        tools_ctx["_turn"] += 1
        tools_ctx["_heard_numbers"] = list(session.numbers)  # what a send may use
        if tools_ctx.get("_pending"):
            # The owner is answering a read-back. A plain yes runs exactly what
            # was read out — not left to the model, which tended to ask again
            # (or reword the message) instead of sending.
            if is_plain_yes(heard):
                try:
                    done = await tools.approve_pending(tools_ctx)
                except Exception as e:  # noqa: BLE001
                    done = f"Nahi ho paaya: {e}"
                await speak(ws, session, done or "Theek hai.")
                return
            if NO_RE.search(heard) and not re.search(r"\d", heard):
                tools_ctx.pop("_pending", None)  # cancelled; the model just acknowledges
    else:
        tools_ctx = {"channel": "call", "agent_id": agent_id, "squad": session.squad()}
    try:
        # Retrieve using the last couple of user turns, not just this one. A
        # follow-up answer like "Computer Science and Engineering" to the agent's
        # "which department?" carries no "faculty/count" intent on its own, so on
        # its own it retrieved the wrong chunks. Joining the recent user turns
        # keeps the original question's keywords in the retrieval query.
        recent_user = [m["content"] for m in session.history if m["role"] == "user"][-2:]
        retrieval_query = " ".join(recent_user) or heard
        kb_context = kb.context_block(agent_id, retrieval_query)
        prompt = session.prompt
        if session.assistant and (p := tools_ctx.get("_pending")):
            # Still waiting for a yes, and this wasn't one: it may be a fix
            # ("spelling V-O-I-C-E hai"). The model dropped such a fix once.
            prompt += (f"\nWAITING FOR THE OWNER'S YES: {p['ask']} If they are correcting it, "
                       "call the same tool again with the corrected details.")
        if session.assistant and (hints := tools.contact_hints(heard)):
            prompt += "\nCONTACTS the owner may mean (saved): " + ", ".join(
                f"{name} = {phone[-10:-5]} {phone[-5:]}" for name, phone in hints)
        answer = await brain.reply("", heard, history=session.history[:-1],
                                   prompt=prompt, kb_context=kb_context,
                                   tools_ctx=tools_ctx, agent_id=agent_id)
    except Exception as e:  # noqa: BLE001
        # Free LLM endpoints rate-limit under load. Say something rather than
        # leaving the caller in silence — the call stays up either way.
        log.warning("LLM failed on %s: %s", session.id, e)
        await ws.send_json({"type": "error", "text": f"Could not answer: {e}"})
        await speak(ws, session, os.getenv("WEBCALL_FALLBACK") or FALLBACK[brain.agent_gender()])
        return

    # An outward action parked this turn: speak its exact question, whatever
    # the model wrote — it has claimed "sent" over a NOT SENT YET before.
    if session.assistant and (p := tools_ctx.get("_pending")) and p["turn"] == tools_ctx["_turn"]:
        answer = p["ask"]
    elif (session.assistant and SENT_CLAIM_RE.search(answer)
          and tools_ctx.get("_delivered_turn") != tools_ctx["_turn"]):
        # It said "bhej diya" but nothing went out this turn — it once told the
        # owner a message was sent when no tool had even run.
        answer = brain.fix_gender(NOT_SENT, brain.agent_gender())
    elif session.assistant and invented_number(answer, session):
        # A number the owner never said and no contact has — it once offered
        # to send to "98123 45678", copied from an example in its instructions.
        answer = ASK_NUMBER
    elif session.assistant and not DONE_RE.search(answer) and (done := [
            r for t, r in tools_ctx.get("_done", []) if t == tools_ctx["_turn"]]):
        # Something was done but the reply doesn't say so ("aur kuch?").
        answer = f"{done[-1]} {answer}"

    await speak(ws, session, answer)

    # Handoff: the old agent has said "connecting you"; the new one picks up
    # with the same history, so the caller never repeats themselves.
    if (target := tools_ctx.get("_handoff")) and session.switch_agent(target):
        await ws.send_json({"type": "handoff", "agent": session.agent.get("name")})
        await speak(ws, session, session.greeting)
