"""Transcription and reply generation via Groq.

Groq serves both Whisper (speech-to-text) and the chat model, so one key
covers WhatsApp voice notes and WhatsApp text replies. Phone calls don't come
through here — Vapi runs that loop itself.
"""

import asyncio
import io
import logging
import os
import re

import httpx

import db

log = logging.getLogger("voiceflow.brain")

GROQ_URL = "https://api.groq.com/openai/v1"
GROQ_KEY = os.getenv("GROQ_API_KEY", "")

# Reply generation. Any OpenAI-compatible provider works — set CHAT_BASE_URL to
# https://integrate.api.nvidia.com/v1 with an nvapi- key to use NVIDIA instead.
# Defaults to Groq so one key covers both jobs.
# `or` not a getenv default: a key present in .env but left blank returns "",
# and getenv's default never fires. Blank must mean "use ours".
CHAT_BASE_URL = os.getenv("CHAT_BASE_URL") or GROQ_URL
CHAT_API_KEY = os.getenv("CHAT_API_KEY") or GROQ_KEY
CHAT_MODEL = os.getenv("CHAT_MODEL") or "llama-3.3-70b-versatile"

# Transcription. Two backends, both real:
#   "groq"  — hosted Whisper, fastest, needs GROQ_API_KEY
#   "local" — faster-whisper on CPU, no key and no bill, ~3-4s for a short note
#   "auto"  — groq when its key is present, otherwise local
# NVIDIA is not an option here: its speech models ship as self-hosted NIM
# containers, so there is no endpoint to point a key at.
STT_PROVIDER = (os.getenv("STT_PROVIDER") or "auto").lower()
STT_MODEL = os.getenv("GROQ_STT_MODEL") or "whisper-large-v3"
LOCAL_STT_MODEL = os.getenv("LOCAL_STT_MODEL") or "small"

BUSINESS_NAME = os.getenv("BUSINESS_NAME", "our business")
OWNER_NAME = os.getenv("OWNER_NAME", "the owner")

STYLE_KEY = "writing_style"

# Default writing style. The owner can overwrite it from Voice Studio, which is
# how "replies sound like you wrote them" actually happens.
DEFAULT_STYLE = (
    "Short, warm, direct. Greets by name when known. Uses everyday words, not "
    "formal ones. Never more than three sentences."
)


def configured() -> bool:
    return bool(CHAT_API_KEY)


def _chat_headers() -> dict:
    if not CHAT_API_KEY:
        raise RuntimeError("No chat key set — add GROQ_API_KEY (or CHAT_API_KEY) to backend/.env")
    return {"Authorization": f"Bearer {CHAT_API_KEY}"}


def _stt_headers() -> dict:
    if not GROQ_KEY:
        raise RuntimeError("GROQ_API_KEY is not set — needed to transcribe voice notes")
    return {"Authorization": f"Bearer {GROQ_KEY}"}


def system_prompt(customer: str | None = None) -> str:
    """The brief for WhatsApp replies.

    Carries the selected agent's own instructions — its timings, prices and
    rules — because without them the model has no facts and cheerfully invents
    slots and rates.
    """
    style = db.get_setting(STYLE_KEY) or DEFAULT_STYLE
    agent = db.get_agent() or {}
    business = agent.get("name") or BUSINESS_NAME
    persona = agent.get("prompt", "").strip()

    # Only claim to know the name when we actually do. Telling the model to
    # "greet by name" with no name makes it invent one.
    who = (
        f"The customer's name is {customer}. Use it naturally."
        if customer
        else "You do NOT know the customer's name. Never guess or make one up."
    )

    return f"""You are replying to WhatsApp messages on behalf of {OWNER_NAME}, who runs {business}.

{persona}

Language: reply in the SAME language and script the customer used. If they wrote
Hinglish in Latin script, reply in Hinglish in Latin script. Never switch.

Writing style to imitate: {style}

{who}

Rules:
- You are answering as the business, not as an AI. Never mention being an AI.
- Only state facts given above. If a fact was not given to you, you do not know
  it. Never guess.

You have NO access to a calendar, booking system or stock list. You cannot see
what is free. So:
- Never say a specific time, date or item IS available or IS booked.
- When someone asks for a time, take their name and the time they want, and say
  it will be confirmed shortly. Do not confirm it yourself.
- Opening hours are not availability.

- No emoji unless the customer used one first.
"""


# Whisper was trained on subtitle data, so when it is handed silence or room
# noise it emits stock phrases from that data rather than nothing. These are
# the ones that actually show up; they are never real customer speech in a
# one-line turn, so drop them instead of replying to them.
HALLUCINATIONS = {
    "you", "thank you", "thank you.", "thanks", "thanks.", "bye", "bye.",
    "thank you for watching", "thanks for watching", "thanks for watching!",
    "please subscribe", "subscribe to my channel", "the end", "okay", "ok",
    "उपशीर्षक", "शुक्रिया", "धन्यवाद", "अगला वीडियो",
}

# A run of the same words is the other tell — "the episode of the episode of".
REPEAT_RE = re.compile(r"\b(\w+(?:\s+\w+){0,3})\b(?:\s+\1\b){2,}", re.IGNORECASE)

# Above this, Whisper itself believes the audio held no speech.
NO_SPEECH_LIMIT = float(os.getenv("STT_NO_SPEECH_LIMIT") or 0.6)


def _clean(text: str) -> str:
    """Empty string for anything that looks like a hallucination, not speech."""
    stripped = text.strip()
    if not stripped:
        return ""
    key = stripped.lower().strip(" .!?,")
    if key in HALLUCINATIONS:
        log.debug("dropped hallucination: %r", stripped)
        return ""
    if REPEAT_RE.search(stripped):
        log.debug("dropped looping transcript: %r", stripped[:60])
        return ""
    # A single short token carries no intent and is almost always noise.
    if len(stripped) < 3:
        return ""
    return stripped


def stt_backend() -> str:
    """Which transcription backend will actually run."""
    if STT_PROVIDER in ("groq", "local"):
        return STT_PROVIDER
    return "groq" if GROQ_KEY else "local"


async def transcribe(audio: bytes, filename: str = "note.ogg") -> str:
    """Speech-to-text. Language is auto-detected, so nothing to configure."""
    if stt_backend() == "local":
        return await asyncio.to_thread(_transcribe_local, audio)
    return await _transcribe_groq(audio, filename)


async def _transcribe_groq(audio: bytes, filename: str) -> str:
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            f"{GROQ_URL}/audio/transcriptions",
            headers=_stt_headers(),
            files={"file": (filename, audio, "application/octet-stream")},
            # verbose_json carries no_speech_prob per segment, which is the only
            # reliable signal that Whisper was handed silence.
            data={"model": STT_MODEL, "response_format": "verbose_json"},
        )
        r.raise_for_status()
        body = r.json()

    segments = body.get("segments") or []
    if segments and all(s.get("no_speech_prob", 0) > NO_SPEECH_LIMIT for s in segments):
        log.debug("dropped: every segment looks like silence")
        return ""
    return _clean(body.get("text", ""))


_local_model = None


def _load_local():
    """Load faster-whisper once, on first use.

    Not at import: the model is a few hundred MB and downloads on first run,
    which would otherwise stall server startup.
    """
    global _local_model
    if _local_model is None:
        from faster_whisper import WhisperModel

        _local_model = WhisperModel(LOCAL_STT_MODEL, device="cpu", compute_type="int8")
    return _local_model


def _transcribe_local(audio: bytes) -> str:
    """Blocking — always call via asyncio.to_thread so the event loop keeps running."""
    # beam_size=1 is greedy decoding: noticeably faster on CPU, and voice notes
    # are short and clear enough that the accuracy difference doesn't show.
    segments, _ = _load_local().transcribe(io.BytesIO(audio), beam_size=1)
    kept = [s for s in segments if getattr(s, "no_speech_prob", 0) <= NO_SPEECH_LIMIT]
    return _clean(" ".join(s.text for s in kept))


async def reply(phone: str, message: str, history: list[dict] | None = None,
                prompt: str | None = None, customer: str | None = None) -> str:
    """Generate one reply.

    `history` overrides the stored thread — web calls keep their turns in memory
    rather than in the WhatsApp tables.
    """
    messages = [
        {"role": "system", "content": prompt or system_prompt(customer)},
        *(db.get_history(phone) if history is None else history),
        {"role": "user", "content": message},
    ]
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{CHAT_BASE_URL}/chat/completions",
            headers={**_chat_headers(), "Content-Type": "application/json"},
            json={"model": CHAT_MODEL, "messages": messages, "temperature": 0.6,
                  "max_tokens": 300},
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()


# Script detection by Unicode block — good enough to label a conversation on the
# dashboard. It never affects the reply: the LLM mirrors the customer's language
# from the prompt, so a mislabel costs a wrong chip, nothing more.
# ponytail: heuristic; swap for a real detector (fastText/lingua) if it drifts.
TAMIL = (0x0B80, 0x0BFF)
DEVANAGARI = (0x0900, 0x097F)

# Devanagari is shared by Hindi and Marathi, so Marathi needs its own markers.
# Each of these is Marathi-only — the Hindi equivalent is a different word
# (उद्या/कल, सकाळी/सुबह, मला/मुझे, नाही/नहीं, काय/क्या, आहे has no Hindi form).
MARATHI_MARKERS = (
    "आहे", "आहात", "आहेत", "काय", "तुम्ही", "मला", "नाही", "शकतो", "शकते",
    "शकता", "उद्या", "सकाळी", "संध्याकाळी", "पाहिजे", "कसे", "कुठे", "होईल",
)


def _in_block(text: str, block: tuple[int, int]) -> bool:
    lo, hi = block
    return any(lo <= ord(ch) <= hi for ch in text)


def detect_language(text: str) -> str:
    if _in_block(text, TAMIL):
        return "Tamil"
    if _in_block(text, DEVANAGARI):
        return "Marathi" if any(w in text for w in MARATHI_MARKERS) else "Hindi"
    # Latin script: Hinglish if it carries common Hindi words, else English.
    words = set(re.findall(r"[a-z]+", text.lower()))
    return "Hinglish" if words & HINGLISH_MARKERS else "English"


# Common Hindi words as they're typed in Latin script. Only whole-word matches
# count, so "kar" never fires on "market". Add to this list, don't switch to
# prefix matching — that misfires on English words constantly.
HINGLISH_MARKERS = {
    "hai", "hain", "haan", "nahi", "nahin", "kya", "kyu", "kyun", "aap", "aapka",
    "aapko", "ji", "kar", "karo", "karta", "karti", "karke", "karna", "kripya",
    "namaste", "namaskar", "dhanyavaad", "shukriya", "accha", "acha", "theek",
    "thik", "bhi", "abhi", "aaj", "kal", "subah", "shaam", "raat", "mein", "mai",
    "hum", "hoon", "hu", "raha", "rahi", "rahe", "sakta", "sakti", "sakte",
    "chahiye", "milega", "mil", "batao", "bataye", "batata", "kitna", "kitne",
    "kitni", "paisa", "rupaye", "wala", "wali", "bhej", "bhejo", "dijiye", "kijiye",
    "hoga", "hogi", "tha", "thi", "se", "ka", "ki", "ke", "ko",
}
# Deliberately absent: "the", "main", "par" — all ordinary English words that
# would label every English message as Hinglish.
