"""Voice cloning and speech synthesis via Cartesia.

The cloned voice is used in two places:
  1. Phone calls  — the voice id is handed to Vapi in the assistant config.
  2. WhatsApp     — this module synthesises the audio directly.

The voice id lives in the `settings` table, not in .env, because it's created
at runtime when the owner uploads their sample.
"""

import logging
import os

import httpx

import db

log = logging.getLogger("voiceflow.voice")

# Well above the ~1.8s median, well below the 25s stalls we measured.
TTS_TIMEOUT = float(os.getenv("TTS_TIMEOUT_SECONDS") or 8)

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
VOICE_SOURCE_KEY = "voice_source"  # "library" | "cloned"


def configured() -> bool:
    return bool(API_KEY)


def voice_id() -> str:
    """The owner's cloned voice, or the fallback voice id from .env."""
    return db.get_setting(VOICE_ID_KEY) or os.getenv("VOICE_ID", "")


def profile() -> dict:
    source = db.get_setting(VOICE_SOURCE_KEY)
    return {
        "voiceId": voice_id(),
        "name": db.get_setting(VOICE_NAME_KEY),
        "source": source,                    # "" until one is chosen
        "ready": bool(voice_id()),           # can we speak at all
        "cloned": source == "cloned",
        "configured": configured(),
        "provider": "cartesia",
    }


def _headers() -> dict:
    if not configured():
        raise RuntimeError("CARTESIA_API_KEY is not set — add it to backend/.env")
    return {"Authorization": f"Bearer {API_KEY}", "Cartesia-Version": API_VERSION}


# Cartesia's own voices, filtered to what an Indian SMB would actually use.
# Cloning needs a paid plan; these are free and work today.
LIBRARY_LANGUAGES = os.getenv("VOICE_LIBRARY_LANGUAGES", "hi,ta,te,en").split(",")

_library_cache: list[dict] = []


async def library(refresh: bool = False) -> list[dict]:
    """Ready-made voices to pick from, Indian languages first."""
    global _library_cache
    if _library_cache and not refresh:
        return _library_cache

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{BASE_URL}/voices/", headers=_headers(), params={"limit": 100})
        r.raise_for_status()
        voices = r.json().get("data", [])

    wanted = [v for v in voices if v.get("language") in LIBRARY_LANGUAGES]
    order = {lang: i for i, lang in enumerate(LIBRARY_LANGUAGES)}
    wanted.sort(key=lambda v: (order.get(v.get("language"), 99), v.get("name", "")))

    _library_cache = [
        {
            "id": v["id"],
            "name": v.get("name", "Unnamed"),
            "language": v.get("language", ""),
            "description": (v.get("description") or "")[:140],
        }
        for v in wanted
    ]
    return _library_cache


def select(voice_id: str, name: str) -> None:
    """Use a ready-made voice. Same setting the clone would have written, so
    calls and WhatsApp pick it up with no other change."""
    db.set_setting(VOICE_ID_KEY, voice_id)
    db.set_setting(VOICE_NAME_KEY, name)
    db.set_setting(VOICE_SOURCE_KEY, "library")


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
    db.set_setting(VOICE_SOURCE_KEY, "cloned")
    return vid


async def synthesize(text: str, language: str | None = None,
                     as_voice: str | None = None) -> bytes:
    """Speak `text`. Returns mp3 bytes.

    `language` takes our dashboard label ("Hindi", "Tamil"); anything unknown
    falls back to DEFAULT_LANGUAGE. `as_voice` overrides the active voice for
    this one call — used to audition a voice without switching to it.
    """
    vid = as_voice or voice_id()
    if not vid:
        raise RuntimeError("No voice selected yet — pick one in Voice Studio first")

    payload = {
        "model_id": MODEL_ID,
        "transcript": text,
        "voice": {"mode": "id", "id": vid},
        "language": LANGUAGE_CODES.get(language or "", DEFAULT_LANGUAGE),
        "output_format": OUTPUT_FORMAT,
    }

    # Measured median is ~1.8s, but roughly one request in six stalls for 25s+
    # on the free tier. A 25s silence mid-call is a lost call, and this request
    # has no side effects, so give up early and retry once — the retry lands
    # fast. One retry only: two stalls in a row means the service is down, and
    # looping just makes the caller wait longer.
    last: Exception | None = None
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=TTS_TIMEOUT) as client:
                r = await client.post(
                    f"{BASE_URL}/tts/bytes",
                    headers={**_headers(), "Content-Type": "application/json"},
                    json=payload,
                )
                r.raise_for_status()
                return r.content
        except httpx.TimeoutException as e:
            last = e
            log.warning("TTS timed out after %ss (attempt %s/2)", TTS_TIMEOUT, attempt + 1)
    raise RuntimeError(f"Cartesia did not respond within {TTS_TIMEOUT}s, twice") from last


async def delete_clone() -> None:
    """Forget the clone locally, and best-effort remove it from Cartesia."""
    vid = db.get_setting(VOICE_ID_KEY)
    source = db.get_setting(VOICE_SOURCE_KEY)
    db.set_setting(VOICE_ID_KEY, "")
    db.set_setting(VOICE_NAME_KEY, "")
    db.set_setting(VOICE_SOURCE_KEY, "")
    # A library voice belongs to Cartesia, not us — never delete it upstream.
    if not vid or source != "cloned" or not configured():
        return
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            await client.delete(f"{BASE_URL}/voices/{vid}", headers=_headers())
    except httpx.HTTPError:
        pass  # local state is already cleared; a stray remote voice is harmless
