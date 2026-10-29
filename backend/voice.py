"""The agent's voice.

Two providers behind one interface:

  edge      Microsoft Edge's TTS. Free, no API key, no quota, 21 Indian voices
            across 8 languages. The default.
  cartesia  Better prosody and a streaming API, but the free tier is ~30 credits
            per request and runs out fast. Voice cloning is Cartesia-only.

`VOICE_PROVIDER` picks. Everything else in the app calls the same functions
either way, so switching is one line in .env.
"""

import asyncio
import logging
import os
import re

import httpx

import db

log = logging.getLogger("voiceflow.voice")

PROVIDER = (os.getenv("VOICE_PROVIDER") or "edge").lower()

VOICE_ID_KEY = "voice_id"
VOICE_NAME_KEY = "voice_name"
VOICE_SOURCE_KEY = "voice_source"  # "library" | "cloned"
# A locally cloned voice (cloning.py) is stored as "clone:<id>" — Edge speaks the
# words, the converter turns them into the owner's timbre.
CLONE_PREFIX = "clone:"
VOICE_GENDER_KEY = "voice_gender"  # "female" | "male"
RATE_KEY = "voice_rate"
PITCH_KEY = "voice_pitch"

# Edge's default pace reads slow on a phone call, where people expect a brisk
# receptionist. Pitch exists because Edge ships exactly one female voice per
# Indian language — shifting pitch is the only way to get a different-sounding
# agent out of the same voice.
DEFAULT_RATE = os.getenv("VOICE_RATE") or "+15%"
DEFAULT_PITCH = os.getenv("VOICE_PITCH") or "+0Hz"


def tuning() -> dict:
    return {
        "rate": db.get_setting(RATE_KEY) or DEFAULT_RATE,
        "pitch": db.get_setting(PITCH_KEY) or DEFAULT_PITCH,
    }


def set_tuning(rate: str | None = None, pitch: str | None = None) -> dict:
    if rate:
        db.set_setting(RATE_KEY, rate)
    if pitch:
        db.set_setting(PITCH_KEY, pitch)
    return tuning()

# Both providers return mp3, which WhatsApp accepts as an audio message and
# every browser can play.
OUTPUT_MIME = "audio/mpeg"
OUTPUT_EXT = "mp3"

DEFAULT_LANGUAGE = os.getenv("VOICE_LANGUAGE", "hi")

# Our dashboard language labels -> locale.
LANGUAGE_CODES = {
    "Hindi": "hi", "Hinglish": "hi", "English": "en",
    "Marathi": "mr", "Tamil": "ta", "Telugu": "te",
}

# ── Edge ────────────────────────────────────────────────────────────────

EDGE_MODEL = "edge"
# Fallback voice per language, used when the caller switches to a language the
# selected voice can't speak. Reading Tamil with a Hindi voice is worse than
# swapping voice for that reply.
EDGE_BY_LANGUAGE = {
    "hi": "hi-IN-SwaraNeural",
    "en": "en-IN-NeerjaNeural",
    "mr": "mr-IN-AarohiNeural",
    "ta": "ta-IN-PallaviNeural",
    "te": "te-IN-ShrutiNeural",
    "bn": "bn-IN-TanishaaNeural",
    "gu": "gu-IN-DhwaniNeural",
    "kn": "kn-IN-SapnaNeural",
    "ml": "ml-IN-SobhanaNeural",
}

# Same, for a male agent. Swapping a male voice to a female one on a language
# switch is exactly the "sounds female, writes male" mismatch — so the swap
# keeps the speaker's gender.
EDGE_MALE_BY_LANGUAGE = {
    "hi": "hi-IN-MadhurNeural",
    "en": "en-IN-PrabhatNeural",
    "mr": "mr-IN-ManoharNeural",
    "ta": "ta-IN-ValluvarNeural",
    "te": "te-IN-MohanNeural",
    "bn": "bn-IN-BashkarNeural",
    "gu": "gu-IN-NiranjanNeural",
    "kn": "kn-IN-GaganNeural",
    "ml": "ml-IN-MidhunNeural",
}
# Male voice-name stems, for a voice chosen before gender was stored and with
# the library not loaded yet. Edge ids look like "hi-IN-MadhurNeural".
_MALE_STEMS = {v.split("-")[-1].replace("Neural", "") for v in EDGE_MALE_BY_LANGUAGE.values()} | {
    "Aarav", "Kunal", "Rehaan", "Arjun",
}

# ── Cartesia ────────────────────────────────────────────────────────────

BASE_URL = "https://api.cartesia.ai"
API_KEY = os.getenv("CARTESIA_API_KEY", "")
API_VERSION = os.getenv("CARTESIA_VERSION", "2026-03-01")
MODEL_ID = os.getenv("CARTESIA_MODEL", "sonic-3.5")
OUTPUT_FORMAT = {"container": "mp3", "sample_rate": 44100, "bit_rate": 128000}
TTS_TIMEOUT = float(os.getenv("TTS_TIMEOUT_SECONDS") or 8)
LIBRARY_LANGUAGES = os.getenv("VOICE_LIBRARY_LANGUAGES", "hi,ta,te,en").split(",")


def configured() -> bool:
    # Edge needs nothing at all; Cartesia needs its key.
    return True if PROVIDER == EDGE_MODEL else bool(API_KEY)


def voice_id() -> str:
    """The chosen voice, or a sensible default so the agent is never mute."""
    chosen = db.get_setting(VOICE_ID_KEY) or os.getenv("VOICE_ID", "")
    if chosen:
        return chosen
    return EDGE_BY_LANGUAGE[DEFAULT_LANGUAGE] if PROVIDER == EDGE_MODEL else ""


def profile() -> dict:
    source = db.get_setting(VOICE_SOURCE_KEY)
    vid = voice_id()
    return {
        "voiceId": vid,
        "name": db.get_setting(VOICE_NAME_KEY) or vid,
        "source": source,
        "ready": bool(vid),
        "cloned": source == "cloned",
        "configured": configured(),
        "provider": PROVIDER,
        "canClone": bool(API_KEY) if PROVIDER == "cartesia" else _clone_missing() is None,
        # Why local cloning can't run yet (what to install), or None.
        "cloneMissing": None if PROVIDER == "cartesia" else _clone_missing(),
        "gender": gender(vid) if vid else None,
        **tuning(),
    }


def _gender_from_text(text: str) -> str | None:
    t = (text or "").lower()
    # "female" first — it contains "male".
    if "female" in t or "woman" in t:
        return "female"
    if re.search(r"\bmale\b|\bman\b", t):
        return "male"
    return None


def gender(vid: str | None = None) -> str:
    """"female" or "male" — who the agent sounds like.

    The reply text must agree with it: Hindi first-person verbs are gendered
    ("kar sakti hoon" vs "kar sakta hoon"), and a female voice reading male
    grammar is the mismatch callers notice instantly. VOICE_GENDER in .env
    overrides everything (useful for a cloned voice).
    """
    forced = (os.getenv("VOICE_GENDER") or "").lower()
    if forced in ("female", "male"):
        return forced
    vid = vid or voice_id()
    if vid == voice_id():
        stored = db.get_setting(VOICE_GENDER_KEY)
        if stored in ("female", "male"):
            return stored
    for v in _library_cache:
        if v["id"] == vid and (g := _gender_from_text(v.get("description", ""))):
            return g
    stem = vid.split("-")[-1].replace("Neural", "")
    return "male" if stem in _MALE_STEMS else "female"


def select(voice_id: str, name: str) -> None:
    db.set_setting(VOICE_ID_KEY, voice_id)
    db.set_setting(VOICE_NAME_KEY, name)
    db.set_setting(VOICE_SOURCE_KEY, "library")
    db.set_setting(VOICE_GENDER_KEY, "")  # re-derive for the new voice
    db.set_setting(VOICE_GENDER_KEY, gender(voice_id))


def _headers() -> dict:
    if not API_KEY:
        raise RuntimeError("CARTESIA_API_KEY is not set — add it to backend/.env")
    return {"Authorization": f"Bearer {API_KEY}", "Cartesia-Version": API_VERSION}


# ── voice library ───────────────────────────────────────────────────────

_library_cache: list[dict] = []


async def library(refresh: bool = False) -> list[dict]:
    """Voices to choose from, Indian languages only."""
    global _library_cache
    if _library_cache and not refresh:
        return _library_cache
    _library_cache = await (_edge_library() if PROVIDER == EDGE_MODEL else _cartesia_library())
    return _library_cache


async def _edge_library() -> list[dict]:
    import edge_tts

    voices = await edge_tts.list_voices()
    indian = [v for v in voices if v["Locale"].endswith("-IN")]
    indian.sort(key=lambda v: (v["Locale"], v["ShortName"]))
    return [
        {
            "id": v["ShortName"],
            # "hi-IN-SwaraNeural" -> "Swara" reads better than the raw id.
            "name": v["ShortName"].split("-")[-1].replace("Neural", ""),
            "language": v["Locale"].split("-")[0],
            "description": f"{v['Gender']} · {v['Locale']}",
        }
        for v in indian
    ]


async def _cartesia_library() -> list[dict]:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{BASE_URL}/voices/", headers=_headers(), params={"limit": 100})
        r.raise_for_status()
        voices = r.json().get("data", [])

    wanted = [v for v in voices if v.get("language") in LIBRARY_LANGUAGES]
    order = {lang: i for i, lang in enumerate(LIBRARY_LANGUAGES)}
    wanted.sort(key=lambda v: (order.get(v.get("language"), 99), v.get("name", "")))
    return [
        {
            "id": v["id"],
            "name": v.get("name", "Unnamed"),
            "language": v.get("language", ""),
            "description": (v.get("description") or "")[:140],
        }
        for v in wanted
    ]


# ── speech ──────────────────────────────────────────────────────────────

# Read-aloud clean-up. The text on screen stays as written; only what the voice
# says changes. Each rule is a measured mis-reading (Edge Hindi voices,
# re-transcribed with Whisper):
#   "batayein.Ji"      -> "batayein DOT ji"   (a period with no space after it)
#   "BILL-0001"        -> "bill bill 00001"
#   "V-O-I-C-E"        -> letters spelled one by one
#   "(Waiting for …)"  -> the model's own stage note, read out loud
_ACRONYMS = {
    "AI", "GST", "UPI", "OTP", "PDF", "EMI", "SMS", "HOD", "CSE", "ECE", "IT",
    "ID", "OK", "TV", "AC", "KYC", "PAN", "HTML", "CSS", "JS", "URL", "HTTP",
    "SIM", "GSM", "PBX", "SIP", "CRM", "CEO", "CTO", "HR", "FAQ", "MB", "GB", "KB",
}
_SPEECH_RULES = [
    (re.compile(r"\((?:waiting|note|pause|thinking|silence)[^)]*\)\.?", re.I), " "),
    (re.compile(r"[*_#`~]+"), ""),                                   # markdown
    (re.compile(r"[“”\"«»]"), ""),                                    # quote marks
    (re.compile(r"\s*[—–]\s*"), ", "),                               # dashes -> pause
    (re.compile(r"(?<=\w)[‑‐](?=\w)"), " "),                         # "10‑digit"
    (re.compile(r"\b(?:[A-Za-z]-){2,}[A-Za-z]\b"), lambda m: m.group(0).replace("-", "")),
    (re.compile(r"\b([A-Za-z]+)\s+\1-0*(\d+)\b", re.I), r"\1 \2"),   # "Bill BILL-0001" -> "Bill 1"
    (re.compile(r"\b([A-Za-z]+)-0*(\d+)\b"), r"\1 \2"),              # BILL-0001 -> BILL 1
    (re.compile(r"₹\s*(\d(?:[\d,]*\d)?(?:\.\d+)?)"), r"\1 rupaye"),
    (re.compile(r"\bRs\.?\s*(\d(?:[\d,]*\d)?(?:\.\d+)?)", re.I), r"\1 rupaye"),
    (re.compile(r"(\d+)%"), r"\1 percent"),                           # percentages
    # Spacing 10-digit phone numbers for clean digit-by-digit pronunciation
    (re.compile(r"\b(\d{5})(\d{5})\b"), r"\1 \2"),
    (re.compile(r"\b(\d)(\d)(\d)(\d)(\d)\b"), r"\1 \2 \3 \4 \5"),
    # Natural breathing pause after common Indian discourse markers
    (re.compile(r"\b(haanji|ji|bilkul|namaste|shukriya|dhanyawad|ji haan)\b[,.]?", re.I), r"\1, "),
    (re.compile(r"(?<=[^\s\d.])([.!?।])(?=[^\s\d.!?।)])"), r"\1 "),  # "hai.Ji" -> "hai. Ji"
    (re.compile(r"[()\[\]]"), " "),
]


def speakable(text: str) -> str:
    """`text` as it should be SAID — see the rules above."""
    for pattern, repl in _SPEECH_RULES:
        text = pattern.sub(repl, text)
    # ALL-CAPS words are a shout or STT noise ("VICE FLOW"), not an acronym —
    # say them as words. Real acronyms keep their letters.
    text = re.sub(r"\b[A-Z]{3,}\b", lambda m: m.group(0) if m.group(0) in _ACRONYMS
                  else m.group(0).capitalize(), text)
    return re.sub(r"\s{2,}", " ", text).strip()


async def synthesize(text: str, language: str | None = None,
                     as_voice: str | None = None) -> bytes:
    """Speak `text`. Returns mp3 bytes.

    `as_voice` auditions a specific voice without changing the saved one.
    """
    text = speakable(text) or text
    vid = as_voice or voice_id()
    if not vid:
        raise RuntimeError("No voice selected — pick one in Voice Studio first")
    locale = LANGUAGE_CODES.get(language or "", DEFAULT_LANGUAGE)

    if vid.startswith(CLONE_PREFIX):
        return await _clone_speak(text, vid, locale)
    if PROVIDER == EDGE_MODEL:
        return await _edge_speak(text, vid, locale)
    return await _cartesia_speak(text, vid, locale)


async def warm_up() -> None:
    """Pay the first-synthesis cost at startup instead of mid-call.

    Measured: the first Edge request of a process takes ~7.7s against ~2.1s for
    every one after it — TLS, the websocket handshake and the voice list all
    happen once. That penalty landed on the caller's first question, which is
    the worst possible place for it.
    """
    if voice_id().startswith(CLONE_PREFIX):
        import cloning

        # Loading torch + the converter is seconds; do it before the first caller.
        await asyncio.to_thread(cloning.warm)
    try:
        await synthesize("Namaste")
    except Exception as e:  # noqa: BLE001 - a cold first call is not worth a crash
        log.info("Voice warm-up skipped: %s", e)


def _clone_missing() -> str | None:
    import cloning

    return cloning.missing()


async def _clone_speak(text: str, vid: str, locale: str) -> bytes:
    """Edge says the words in the caller's language, the converter re-voices
    them as the owner. The base voice matches the owner's gender — converting
    across genders is where tone-colour conversion sounds worst."""
    import cloning

    by_language = EDGE_MALE_BY_LANGUAGE if gender(vid) == "male" else EDGE_BY_LANGUAGE
    base_voice = by_language.get(locale) or by_language[DEFAULT_LANGUAGE]
    base = await _edge_speak(text, base_voice, locale)
    try:
        clone_timeout = float(os.getenv("CLONE_TIMEOUT_SECONDS", "8.0"))
        return await asyncio.wait_for(
            asyncio.to_thread(cloning.convert, base, vid[len(CLONE_PREFIX):], 0.20, text, True),
            timeout=clone_timeout,
        )
    except Exception as e:  # noqa: BLE001 - a stock voice beats a silent turn
        log.warning("clone conversion failed or timed out (%s), speaking the base voice: %s", type(e).__name__, e)
        return base


async def _edge_speak(text: str, vid: str, locale: str) -> bytes:
    import edge_tts

    # An Edge voice only speaks its own language. If the caller switched, swap
    # to that language's voice for this reply rather than mangling the words.
    by_language = EDGE_MALE_BY_LANGUAGE if gender(vid) == "male" else EDGE_BY_LANGUAGE
    if not vid.startswith(f"{locale}-") and locale in by_language:
        vid = by_language[locale]

    t = tuning()
    fallback = by_language.get(locale)

    # Edge hands back an empty stream now and then — no error, just no audio.
    # Mid-call that is a turn the caller never hears an answer to, which reads
    # as "the agent randomly stops replying". Retry, then try the language's
    # standard voice before giving up on the turn.
    attempts = [vid, vid] + ([fallback] if fallback and fallback != vid else [])
    last = ""
    for attempt, voice_id_ in enumerate(attempts, 1):
        audio = bytearray()
        try:
            async for chunk in edge_tts.Communicate(
                text, voice_id_, rate=t["rate"], pitch=t["pitch"]
            ).stream():
                if chunk["type"] == "audio":
                    audio += chunk["data"]
        except Exception as e:  # noqa: BLE001 - any failure is worth one more try
            last = f"{type(e).__name__}: {e}"
            continue
        if audio:
            if attempt > 1:
                log.info("Edge TTS needed %s attempts (voice %s)", attempt, voice_id_)
            return bytes(audio)
        last = f"empty stream from {voice_id_}"

    raise RuntimeError(f"Edge TTS returned no audio after {len(attempts)} tries — {last}")


async def _cartesia_speak(text: str, vid: str, locale: str) -> bytes:
    payload = {
        "model_id": MODEL_ID,
        "transcript": text,
        "voice": {"mode": "id", "id": vid},
        "language": locale,
        "output_format": OUTPUT_FORMAT,
    }

    # Median is ~1.8s but roughly one request in six stalls past 25s, and a
    # 25-second silence mid-call is a lost call. No side effects, so give up
    # early and retry once.
    last: Exception | None = None
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=TTS_TIMEOUT) as client:
                r = await client.post(
                    f"{BASE_URL}/tts/bytes",
                    headers={**_headers(), "Content-Type": "application/json"},
                    json=payload,
                )
                if r.status_code == 402:
                    raise RuntimeError(
                        "Cartesia credits exhausted. Set VOICE_PROVIDER=edge in "
                        "backend/.env for free voices, or top up your plan."
                    )
                r.raise_for_status()
                return r.content
        except httpx.TimeoutException as e:
            last = e
            log.warning("TTS timed out after %ss (attempt %s/2)", TTS_TIMEOUT, attempt + 1)
    raise RuntimeError(f"Cartesia did not respond within {TTS_TIMEOUT}s, twice") from last


# ── cloning ─────────────────────────────────────────────────────────────
# Local (cloning.py, free, default) unless Cartesia is the provider.

async def clone(name: str, samples: list[tuple[str, bytes, str]],
                language: str | None = None, gender_: str = "female") -> str:
    """Clone the owner's voice from recorded samples."""
    if not samples:
        raise RuntimeError("No audio sample provided")

    if PROVIDER == "cartesia":
        # Cartesia clones from ONE clip, so send the longest — more audio, closer match.
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
    else:
        import cloning

        if reason := cloning.missing():
            raise RuntimeError(f"Local voice cloning is not installed: {reason}")
        # Every clip counts here — the embedding is averaged over all of them.
        clone_id = await asyncio.to_thread(cloning.enroll, [data for _, data, _ in samples])
        old = db.get_setting(VOICE_ID_KEY)
        if old.startswith(CLONE_PREFIX):
            cloning.delete(old[len(CLONE_PREFIX):])
        vid = CLONE_PREFIX + clone_id

    db.set_setting(VOICE_ID_KEY, vid)
    db.set_setting(VOICE_NAME_KEY, name)
    db.set_setting(VOICE_SOURCE_KEY, "cloned")
    # A clone's gender can't be read from any library — the owner states it.
    db.set_setting(VOICE_GENDER_KEY, "male" if gender_ == "male" else "female")
    return vid


async def delete_clone() -> None:
    """Forget the clone locally, and best-effort remove it upstream."""
    vid = db.get_setting(VOICE_ID_KEY)
    source = db.get_setting(VOICE_SOURCE_KEY)
    for key in (VOICE_ID_KEY, VOICE_NAME_KEY, VOICE_SOURCE_KEY, VOICE_GENDER_KEY):
        db.set_setting(key, "")
    if vid.startswith(CLONE_PREFIX):
        import cloning

        cloning.delete(vid[len(CLONE_PREFIX):])
        return
    # A library voice belongs to the provider, not us — never delete it upstream.
    if not vid or source != "cloned" or PROVIDER != "cartesia" or not API_KEY:
        return
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            await client.delete(f"{BASE_URL}/voices/{vid}", headers=_headers())
    except httpx.HTTPError:
        pass  # local state is already cleared; a stray remote voice is harmless
