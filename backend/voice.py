"""Voice cloning and speech synthesis via Cartesia.

The cloned voice is used in two places:
  1. Phone calls  — the voice id is handed to Vapi in the assistant config.
  2. WhatsApp     — this module synthesises the audio directly.

The voice id lives in the `settings` table, not in .env, because it's created
at runtime when the owner uploads their sample.
"""

import os

import httpx

import db

BASE_URL = "https://api.cartesia.ai"
API_KEY = os.getenv("CARTESIA_API_KEY", "")
API_VERSION = os.getenv("CARTESIA_VERSION", "2026-03-01")
MODEL_ID = os.getenv("CARTESIA_MODEL", "sonic-3.5")

# Cartesia has no ogg/opus container, so WhatsApp gets mp3. It renders as an
# audio message with a play button rather than a waveform voice note — the
# audio is identical, only the bubble styling differs.
OUTPUT_FORMAT = {"container": "mp3", "sample_rate": 44100, "bit_rate": 128000}
OUTPUT_MIME = "audio/mpeg"
OUTPUT_EXT = "mp3"

# Default language for cloning and synthesis. ISO 639-1.
DEFAULT_LANGUAGE = os.getenv("VOICE_LANGUAGE", "hi")

# Our dashboard language labels -> what Cartesia expects. Hinglish is Hindi
# written in Latin script; Cartesia reads it best as Hindi.
LANGUAGE_CODES = {
    "Hindi": "hi",
    "Hinglish": "hi",
    "English": "en",
    "Marathi": "mr",
    "Tamil": "ta",
}

VOICE_ID_KEY = "voice_id"
VOICE_NAME_KEY = "voice_name"


def configured() -> bool:
    return bool(API_KEY)


def voice_id() -> str:
    """The owner's cloned voice, or the fallback voice id from .env."""
    return db.get_setting(VOICE_ID_KEY) or os.getenv("VOICE_ID", "")


def profile() -> dict:
    return {
        "voiceId": voice_id(),
        "name": db.get_setting(VOICE_NAME_KEY),
        "cloned": bool(db.get_setting(VOICE_ID_KEY)),
        "configured": configured(),
        "provider": "cartesia",
    }


def _headers() -> dict:
    if not configured():
        raise RuntimeError("CARTESIA_API_KEY is not set — add it to backend/.env")
    return {"Authorization": f"Bearer {API_KEY}", "Cartesia-Version": API_VERSION}


async def clone(name: str, samples: list[tuple[str, bytes, str]],
                language: str | None = None) -> str:
    """Clone the owner's voice from a recorded sample.

    Cartesia clones from ONE clip, so if several were recorded we send the
    longest — more audio gives a closer match. Returns the new voice id and
    stores it as the active voice.
    """
    if not samples:
        raise RuntimeError("No audio sample provided")

    filename, data, mime = max(samples, key=lambda s: len(s[1]))

    async with httpx.AsyncClient(timeout=180) as client:
        r = await client.post(
            f"{BASE_URL}/voices/clone",
            headers=_headers(),
            data={"name": name, "language": language or DEFAULT_LANGUAGE},
            files={"clip": (filename, data, mime)},
        )
        r.raise_for_status()
        vid = r.json()["id"]

    db.set_setting(VOICE_ID_KEY, vid)
    db.set_setting(VOICE_NAME_KEY, name)
    return vid


async def synthesize(text: str, language: str | None = None) -> bytes:
    """Speak `text` in the cloned voice. Returns mp3 bytes.

    `language` takes our dashboard label ("Hindi", "Tamil"); anything unknown
    falls back to DEFAULT_LANGUAGE.
    """
    vid = voice_id()
    if not vid:
        raise RuntimeError("No cloned voice yet — record a sample in Voice Studio first")

    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            f"{BASE_URL}/tts/bytes",
            headers={**_headers(), "Content-Type": "application/json"},
            json={
                "model_id": MODEL_ID,
                "transcript": text,
                "voice": {"mode": "id", "id": vid},
                "language": LANGUAGE_CODES.get(language or "", DEFAULT_LANGUAGE),
                "output_format": OUTPUT_FORMAT,
            },
        )
        r.raise_for_status()
        return r.content


async def delete_clone() -> None:
    """Forget the clone locally, and best-effort remove it from Cartesia."""
    vid = db.get_setting(VOICE_ID_KEY)
    db.set_setting(VOICE_ID_KEY, "")
    db.set_setting(VOICE_NAME_KEY, "")
    if not vid or not configured():
        return
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            await client.delete(f"{BASE_URL}/voices/{vid}", headers=_headers())
    except httpx.HTTPError:
        pass  # local state is already cleared; a stray remote voice is harmless
