"""Transcription and reply generation via Groq.

Groq serves both Whisper (speech-to-text) and the chat model, so one key
covers WhatsApp voice notes and WhatsApp text replies. Phone calls don't come
through here — Vapi runs that loop itself.
"""

import os

import httpx

import db

BASE_URL = "https://api.groq.com/openai/v1"
API_KEY = os.getenv("GROQ_API_KEY", "")
CHAT_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
STT_MODEL = os.getenv("GROQ_STT_MODEL", "whisper-large-v3")

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
    return bool(API_KEY)


def _headers() -> dict:
    if not configured():
        raise RuntimeError("GROQ_API_KEY is not set — add it to backend/.env")
    return {"Authorization": f"Bearer {API_KEY}"}


def system_prompt() -> str:
    style = db.get_setting(STYLE_KEY) or DEFAULT_STYLE
    return f"""You are replying to WhatsApp messages on behalf of {OWNER_NAME}, who runs {BUSINESS_NAME}.

Language: reply in the SAME language and script the customer used. If they wrote
Hinglish in Latin script, reply in Hinglish in Latin script. Never switch.

Writing style to imitate: {style}

Rules:
- You are answering as the business, not as an AI. Never mention being an AI.
- Answer questions about timings, pricing, availability, and take bookings.
- Never invent prices, stock, or appointment slots you were not told about.
  If you don't know, say you'll confirm and get back to them.
- No emoji unless the customer used one first.
"""


async def transcribe(audio: bytes, filename: str = "note.ogg") -> str:
    """Speech-to-text. Language is auto-detected, so nothing to configure."""
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            f"{BASE_URL}/audio/transcriptions",
            headers=_headers(),
            files={"file": (filename, audio, "application/octet-stream")},
            data={"model": STT_MODEL, "response_format": "json"},
        )
        r.raise_for_status()
        return r.json().get("text", "").strip()


async def reply(phone: str, message: str) -> str:
    """Generate the reply for one incoming WhatsApp message."""
    messages = [
        {"role": "system", "content": system_prompt()},
        *db.get_history(phone),
        {"role": "user", "content": message},
    ]
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{BASE_URL}/chat/completions",
            headers={**_headers(), "Content-Type": "application/json"},
            json={"model": CHAT_MODEL, "messages": messages, "temperature": 0.6,
                  "max_tokens": 300},
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()


# Rough script detection — good enough to label a conversation on the dashboard.
# ponytail: heuristic by Unicode block, swap for a real detector if it mislabels.
_SCRIPTS = [
    ("Tamil", 0x0B80, 0x0BFF),
    ("Marathi", 0x0900, 0x097F),  # Devanagari — shared with Hindi, see below
]


def detect_language(text: str) -> str:
    for name, lo, hi in _SCRIPTS:
        if any(lo <= ord(ch) <= hi for ch in text):
            # Devanagari is used by both Hindi and Marathi; default to Hindi
            # unless a Marathi-only marker shows up.
            if name == "Marathi":
                return "Marathi" if any(w in text for w in ("आहे", "काय", "तुम्ही")) else "Hindi"
            return name
    # Latin script: Hinglish if it carries common Hindi words, else English.
    lowered = f" {text.lower()} "
    if any(f" {w} " in lowered for w in ("hai", "haan", "nahi", "kya", "aap", "ji", "kar")):
        return "Hinglish"
    return "English"
