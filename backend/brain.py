"""Transcription and reply generation via Groq.

Groq serves both Whisper (speech-to-text) and the chat model, so one key
covers WhatsApp voice notes and WhatsApp text replies. Phone calls don't come
through here — Vapi runs that loop itself.
"""

import asyncio
import io
import json
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
# Hosted models get retired with little warning — llama-3.3-70b-versatile was
# the default here until Groq dropped it, and every reply started 404ing. If
# replies stop, check this first: GET /v1/models lists what is actually served.
CHAT_MODEL = os.getenv("CHAT_MODEL") or "openai/gpt-oss-120b"
MAX_REPLY_TOKENS = int(os.getenv("MAX_REPLY_TOKENS") or 400)


def _is_reasoning(model: str) -> bool:
    """Reasoning models spend part of max_tokens thinking before writing."""
    return "gpt-oss" in model or "qwen3" in model


REASONING_MODEL = _is_reasoning(CHAT_MODEL)

# Second Groq model, same key. A hosted model can be retired without warning —
# that is exactly the Groq 404 that once broke every reply — and a burst can
# exhaust one model's per-minute budget. A different model on the same provider
# rides out both at zero extra cost and no new key.
CHAT_FALLBACK_MODEL = os.getenv("CHAT_FALLBACK_MODEL") or "openai/gpt-oss-20b"

# Optional third tier on a different provider entirely, for when Groq itself is
# down. Any OpenAI-compatible free API works — NVIDIA NIM
# (https://integrate.api.nvidia.com/v1, a free nvapi- key from build.nvidia.com),
# OpenRouter, Cerebras. Set all three to switch it on.
FALLBACK_BASE_URL = os.getenv("FALLBACK_BASE_URL", "")
FALLBACK_API_KEY = os.getenv("FALLBACK_API_KEY", "")
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL", "")


def chat_providers() -> list[dict]:
    """Ordered chat endpoints to try. Each is tried in turn until one answers,
    so a provider outage degrades to the next instead of dropping the reply."""
    providers = [{"base": CHAT_BASE_URL, "key": CHAT_API_KEY, "model": CHAT_MODEL}]
    # A second Groq model catches model-retirement and per-minute exhaustion.
    if CHAT_API_KEY == GROQ_KEY and CHAT_FALLBACK_MODEL != CHAT_MODEL:
        providers.append({"base": GROQ_URL, "key": GROQ_KEY, "model": CHAT_FALLBACK_MODEL})
    # A different provider catches a full Groq outage — only if configured.
    if FALLBACK_BASE_URL and FALLBACK_API_KEY and FALLBACK_MODEL:
        providers.append({"base": FALLBACK_BASE_URL, "key": FALLBACK_API_KEY, "model": FALLBACK_MODEL})
    return providers

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


ANALYSIS_SYSTEM = """You review a finished customer call and return ONLY JSON.

Schema:
{
  "summary": "one plain sentence: what the caller wanted and what happened",
  "name": "caller's name if stated, else null",
  "intent": "a few words for why they called",
  "item": "product or service discussed, else null",
  "outcome": "one of: Answered, Booking, Escalated, Missed",
  "success": true or false
}

Rules:
- Use null for anything not actually said. Never invent.
- "success" is true only if the caller's need was addressed and the agent stayed
  truthful (did not make up a price, slot or stock).
- Return the JSON object and nothing else.
"""


def _extract_json(text: str) -> dict:
    """Parse a model reply that should be JSON, tolerating stray prose around it."""
    import json

    text = (text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
    return {}


async def analyse_call(transcript: str, language: str = "Hindi") -> dict:
    """Post-call analysis on our own stack — summary, extracted fields, success.

    This is the Vapi `analysisPlan` capability rebuilt with one Groq call; no
    Vapi involved. Returns {} on any failure so a call is still recorded even
    when analysis is unavailable.
    """
    transcript = (transcript or "").strip()
    if not transcript:
        return {}

    body = {
        "model": CHAT_MODEL,
        "messages": [
            {"role": "system", "content": ANALYSIS_SYSTEM},
            {"role": "user", "content": f"Call language: {language}\n\nTranscript:\n{transcript}"},
        ],
        "temperature": 0,
        "max_tokens": MAX_REPLY_TOKENS,
        "response_format": {"type": "json_object"},
    }
    if REASONING_MODEL:
        body["reasoning_effort"] = os.getenv("REASONING_EFFORT") or "low"

    try:
        r = await _post(http(), f"{CHAT_BASE_URL}/chat/completions",
                        headers={**_chat_headers(), "Content-Type": "application/json"},
                        json=body, timeout=60)
        data = _extract_json(r.json()["choices"][0]["message"]["content"])
    except Exception as e:  # noqa: BLE001 - analysis is best-effort, never fatal
        log.warning("call analysis failed: %s", e)
        return {}

    # Normalise to exactly the fields callers store, dropping anything spurious.
    out: dict = {}
    for key in ("summary", "name", "intent", "item", "outcome"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            out[key] = val.strip()
    if isinstance(data.get("success"), bool):
        out["success"] = data["success"]
    return out


def facts_block(facts: str | None) -> str:
    """Render an agent's business facts for the prompt.

    When there are none, say so explicitly rather than leaving a blank the model
    fills with plausible-sounding invented prices — the failure this fixes was
    the agent quoting three different rates for one table across three calls.
    """
    facts = (facts or "").strip()
    if not facts:
        return (
            "BUSINESS FACTS: none provided. You do not know any prices, timings "
            "or stock. For any such question, say you will confirm and get back "
            "to them. Never invent a number."
        )
    return f"BUSINESS FACTS (the only prices, timings and items you may quote):\n{facts}"


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
    facts = facts_block(agent.get("facts"))

    # Only claim to know the name when we actually do. Telling the model to
    # "greet by name" with no name makes it invent one.
    who = (
        f"The customer's name is {customer}. Use it naturally."
        if customer
        else "You do NOT know the customer's name. Never guess or make one up."
    )

    return f"""You are replying to WhatsApp messages on behalf of {OWNER_NAME}, who runs {business}.

{persona}

{facts}

Language: reply in the SAME language and script the customer used. If they wrote
Hinglish in Latin script, reply in Hinglish in Latin script. Never switch.

Writing style to imitate: {style}

{who}

Rules:
- You are answering as the business, not as an AI. Never mention being an AI.
- Prices, timings and stock come ONLY from the BUSINESS FACTS section. If a
  price or item is not listed there, say you will confirm it and get back to
  them — never state or estimate a number that is not in BUSINESS FACTS.

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

# Scripts a Hindi / English / Hinglish caller never produces. Whisper emits these
# only when it auto-detects the wrong language on silence or room noise — that is
# the "Chinese/Japanese appeared and I never said it" bug. CJK ideographs + kana,
# Hangul, Thai, Cyrillic, Arabic. Any of these means hallucination, so drop it.
ALIEN_SCRIPT_RE = re.compile(
    r"[぀-ヿ㐀-䶿一-鿿가-힯฀-๿Ѐ-ӿ؀-ۿ]"
)

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
    if ALIEN_SCRIPT_RE.search(stripped):
        log.debug("dropped foreign-script hallucination: %r", stripped[:60])
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


# Groq's free tier caps requests and tokens per minute. A burst of WhatsApp
# messages, or a web call running alongside them, hits it easily — and the 429
# clears on its own within seconds. Losing a customer's reply to that is silly,
# so wait it out here rather than letting the caller fail.
RATE_LIMIT_TRIES = int(os.getenv("RATE_LIMIT_RETRIES") or 3)
MAX_BACKOFF_SECONDS = 20

# Last-seen rate-limit counters from the provider, surfaced on /health.
budget: dict[str, str] = {}

_pool: httpx.AsyncClient | None = None


def http() -> httpx.AsyncClient:
    """One connection pool for the whole process.

    A fresh AsyncClient per request means a new TCP + TLS handshake to Groq on
    every call — and a voice turn makes two of them, transcription then reply.
    On a slow link that was seconds of every turn spent shaking hands. Keeping
    the connection alive removes that from every turn after the first.
    """
    global _pool
    if _pool is None or _pool.is_closed:
        _pool = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=15.0),
            limits=httpx.Limits(max_keepalive_connections=8, keepalive_expiry=600),
        )
    return _pool


async def close_http() -> None:
    global _pool
    if _pool is not None and not _pool.is_closed:
        await _pool.aclose()
    _pool = None

# Groq puts the wait in the message: "…Please try again in 5.04s."
_RETRY_IN = re.compile(r"try again in ([\d.]+)\s*s", re.I)


def _rate_limit_wait(r: httpx.Response) -> float:
    """How long the API says to wait. Falls back to a few seconds."""
    header = r.headers.get("retry-after")
    if header:
        try:
            return min(float(header), MAX_BACKOFF_SECONDS)
        except ValueError:
            pass
    if m := _RETRY_IN.search(r.text or ""):
        return min(float(m.group(1)) + 0.5, MAX_BACKOFF_SECONDS)
    return 5.0


def _rate_limit_reason(r: httpx.Response) -> str:
    """The provider's own explanation, so the log says which limit was hit."""
    try:
        return r.json()["error"]["message"]
    except Exception:  # noqa: BLE001 - any unparseable body falls back to raw text
        return (r.text or "")[:200] or "rate limited"


async def _post(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
    """POST and wait out rate limits. Any other error raises as usual."""
    for attempt in range(RATE_LIMIT_TRIES):
        r = await client.post(url, **kwargs)
        if r.status_code == 404 and url.endswith("/chat/completions"):
            # A 404 on this endpoint means the model is gone, not the URL. Say
            # so: a bare "404 Not Found" sent us hunting for a broken base URL
            # while the real answer was that Groq had retired the model.
            raise RuntimeError(
                f"The chat model {CHAT_MODEL!r} is not available at "
                f"{CHAT_BASE_URL} — it was most likely retired. List the "
                f"current ones with: curl {CHAT_BASE_URL}/models "
                f"-H 'Authorization: Bearer $GROQ_API_KEY', then set CHAT_MODEL "
                f"in backend/.env to one of them."
            )
        if r.status_code != 429:
            # Keep the provider's own budget counters. The free tier's cap is
            # per MINUTE, and hitting it looks identical to a slow model from
            # the outside — the backoff below is what the caller experiences.
            # Surfacing the numbers is the difference between "it's laggy" and
            # "you have 900 tokens left this minute".
            for k, v in r.headers.items():
                if k.lower().startswith("x-ratelimit-"):
                    budget[k.lower()[len("x-ratelimit-"):]] = v
            r.raise_for_status()
            return r
        if attempt == RATE_LIMIT_TRIES - 1:
            break
        wait = _rate_limit_wait(r)
        log.warning("rate limited (%s/%s), waiting %.1fs: %s",
                    attempt + 1, RATE_LIMIT_TRIES, wait, _rate_limit_reason(r))
        await asyncio.sleep(wait)
    raise RuntimeError(f"Rate limited after {RATE_LIMIT_TRIES} tries — {_rate_limit_reason(r)}")


def _local_stt_available() -> bool:
    import importlib.util

    return importlib.util.find_spec("faster_whisper") is not None


async def transcribe(audio: bytes, filename: str = "note.ogg") -> str:
    """Speech-to-text. Language is auto-detected, so nothing to configure.

    Hosted Groq Whisper is primary; if it fails and faster-whisper is installed,
    fall back to on-device transcription so a caller is still heard through a
    Groq outage. No local model installed means no fallback — Groq is reliable
    enough that we don't force the ~460 MB download on everyone.
    """
    if stt_backend() == "local":
        return await asyncio.to_thread(_transcribe_local, audio)
    try:
        return await _transcribe_groq(audio, filename)
    except Exception as e:  # noqa: BLE001 - any Groq failure is worth a local retry
        if _local_stt_available():
            log.warning("Groq STT failed (%s) — falling back to local whisper", type(e).__name__)
            return await asyncio.to_thread(_transcribe_local, audio)
        raise


async def _transcribe_groq(audio: bytes, filename: str) -> str:
    r = await _post(
        http(),
        f"{GROQ_URL}/audio/transcriptions",
        headers=_stt_headers(),
        files={"file": (filename, audio, "application/octet-stream")},
        # verbose_json carries no_speech_prob per segment, which is the only
        # reliable signal that Whisper was handed silence.
        data={"model": STT_MODEL, "response_format": "verbose_json"},
    )
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


# ── Speaker gender ────────────────────────────────────────────────────────
# Hindi, Hinglish and Marathi mark the SPEAKER's gender on first-person verbs.
# The model was never told who it sounds like, so a female voice read out
# "main kar sakta hoon" (male grammar) and vice versa. The rule below goes into
# every prompt; fix_gender() then catches the common slips deterministically.

def agent_gender() -> str:
    try:
        import voice

        return voice.gender()
    except Exception:  # noqa: BLE001 - no voice configured -> the old default
        return "female"


def gender_rule(gender: str) -> str:
    if gender == "male":
        return (
            "SPEAKER GENDER: you are a MAN — the caller hears a male voice. Every "
            "sentence about yourself must use masculine grammar. Hindi/Hinglish: "
            "'main kar sakta hoon', 'main dekh raha hoon', 'main samajh gaya', "
            "'main bata deta hoon', 'main nahi jaanta', 'main confirm karunga'. "
            "Marathi: 'मी करतो', 'मी सांगतो'. NEVER sakti/rahi/gayi/deti/karungi "
            "about yourself. Other people keep their own gender."
        )
    return (
        "SPEAKER GENDER: you are a WOMAN — the caller hears a female voice. Every "
        "sentence about yourself must use feminine grammar. Hindi/Hinglish: "
        "'main kar sakti hoon', 'main dekh rahi hoon', 'main samajh gayi', "
        "'main bata deti hoon', 'main nahi jaanti', 'main confirm karungi'. "
        "Marathi: 'मी करते', 'मी सांगते'. NEVER sakta/raha/gaya/deta/karunga "
        "about yourself. Other people keep their own gender."
    )


_DEV = "ऀ-ॣ०-ॿ"  # Devanagari letters/marks, minus the dandas (। ॥)
_END = rf"(?![\w{_DEV}])"          # \b is unreliable on Devanagari matras
_START = rf"(?<![\w{_DEV}])"

# Participles that only change by gender (masc -> fem). Latin is lowercase-only
# on purpose: names are capitalised, so "Main Geeta hoon" is never touched.
_LATIN_PAIRS = {
    "raha": "rahi", "gaya": "gayi", "saka": "saki", "chuka": "chuki",
    "paaya": "paayi", "paya": "payi", "aaya": "aayi", "aya": "ayi", "hua": "hui",
    "baitha": "baithi", "khada": "khadi", "thaka": "thaki",
}
_DEV_PAIRS = {
    "रहा": "रही", "गया": "गई", "सका": "सकी", "चुका": "चुकी", "पाया": "पाई",
    "आया": "आई", "हुआ": "हुई", "बैठा": "बैठी", "थका": "थकी",
}
# Devanagari habitual participles ("करता") by explicit stem: Devanagari has no
# capitals, so a generic -ता rule would turn the name "गीता" into "गीती".
_DEV_STEMS = ("कर|सक|चाह|जान|समझ|दे|ले|बोल|देख|सुन|भेज|बता|रख|कह|पूछ|मान|सोच|"
              "जा|आ|पढ़|लिख|ढूंढ|ढूँढ|मिल|बन|दिला|करवा")
_HOON_LATIN = r"(\s+(?:nahi\s+)?(?:hoon|hun|hu|hoo)(?![A-Za-z]))"
_HOON_DEV = rf"(\s+(?:हूँ|हूं|हु){_END})"
_LUNGI = {"lungi"}  # a garment, not a verb — "-ungi" alone isn't enough


def fix_gender(text: str, gender: str) -> str:
    """Force the speaker's first-person verb forms to match the voice.

    Only touches unambiguous first-person patterns: a participle directly before
    "hoon/हूँ" (only "main" takes hoon), and the -unga/-ungi future (only ever
    first person singular). Everything else is left to the prompt rule.
    """
    if not text:
        return text
    fem = gender != "male"
    latin = _LATIN_PAIRS if fem else {v: k for k, v in _LATIN_PAIRS.items()} | {"gai": "gaya", "gayee": "gaya"}
    dev = _DEV_PAIRS if fem else {v: k for k, v in _DEV_PAIRS.items()} | {"गयी": "गया", "पायी": "पाया"}
    src_t, dst_t = ("ta", "ti") if fem else ("ti", "ta")

    def latin_word(m: re.Match) -> str:
        w = m.group(1)
        if w in latin:
            w = latin[w]
        elif w.endswith(src_t):
            w = w[: -len(src_t)] + dst_t
        return w + m.group(2)

    text = re.sub(rf"(?<![A-Za-z])([a-z]+){_HOON_LATIN}", latin_word, text)

    dev_alt = "|".join(sorted(dev, key=len, reverse=True))
    text = re.sub(rf"{_START}({dev_alt}){_HOON_DEV}", lambda m: dev[m.group(1)] + m.group(2), text)
    a, b = ("ता", "ती") if fem else ("ती", "ता")
    text = re.sub(rf"{_START}((?:{_DEV_STEMS})){a}{_HOON_DEV}", rf"\g<1>{b}\g<2>", text)

    # Future tense: karunga/karungi, bataoonga, करूँगा/करूँगी.
    fut_src, fut_dst = ("a", "i") if fem else ("i", "a")
    text = re.sub(rf"(?<![A-Za-z])([a-z]+(?:un|oon)g){fut_src}(?![A-Za-z])",
                  lambda m: m.group(0) if m.group(0) in _LUNGI else m.group(1) + fut_dst, text)
    g_src, g_dst = ("गा", "गी") if fem else ("गी", "गा")
    text = re.sub(rf"([ूुऊउ][ँं]){g_src}{_END}", rf"\g<1>{g_dst}", text)
    return text


# Tools the agent can call to actually DO something, not just talk. This is
# Vapi's "Tools" capability rebuilt on our stack: the model emits a tool call,
# we run real code (write a booking, flag an escalation), feed the result back,
# and it composes the spoken confirmation. See tools.py for the executors.
def _tool_schemas() -> list[dict]:
    import tools

    return tools.SCHEMAS


def _chat_body(provider: dict, messages: list[dict], with_tools: bool) -> dict:
    body = {
        "model": provider["model"],
        "messages": messages,
        "temperature": 0.6,
        # Counted against the per-minute budget as *requested*, not as used —
        # Groq's own 429 reads "Limit 6000, Used 6000, Requested 500". Asking
        # for headroom nobody spends is how a call runs out of budget.
        "max_tokens": MAX_REPLY_TOKENS,
    }
    if with_tools:
        body["tools"] = _tool_schemas()
        body["tool_choice"] = "auto"
    # gpt-oss models think before answering, and that thinking is billed against
    # max_tokens; at default effort it consumed the whole budget and returned an
    # empty reply that reached TTS as silence. A phone reply needs no deliberation.
    if _is_reasoning(provider["model"]):
        body["reasoning_effort"] = os.getenv("REASONING_EFFORT") or "low"
    return body


async def _chat_once(messages: list[dict], with_tools: bool) -> dict:
    """One completion, with provider failover. Returns the raw assistant message
    (which may carry tool_calls).

    Tries each configured provider in order. A rate limit on the first gets one
    short wait; any hard failure — retired model (404), provider down (5xx,
    timeout), bad key — falls straight through to the next provider so the reply
    survives an outage that used to drop it entirely.
    """
    providers = chat_providers()
    last = "no chat provider configured"

    for i, p in enumerate(providers):
        if not p["key"]:
            continue
        url = f"{p['base']}/chat/completions"
        headers = {"Authorization": f"Bearer {p['key']}", "Content-Type": "application/json"}
        body = _chat_body(p, messages, with_tools)
        try:
            r = await http().post(url, headers=headers, json=body, timeout=60)
            if r.status_code == 429:
                # One provider-local wait for a transient limit before failing over.
                await asyncio.sleep(_rate_limit_wait(r))
                r = await http().post(url, headers=headers, json=body, timeout=60)
            if r.status_code == 200:
                if i == 0:
                    for k, v in r.headers.items():
                        if k.lower().startswith("x-ratelimit-"):
                            budget[k.lower()[len("x-ratelimit-"):]] = v
                else:
                    log.warning("chat: primary failed, answered via fallback #%s (%s)",
                                i, p["model"])
                return r.json()["choices"][0]["message"]
            last = f"{p['model']}: HTTP {r.status_code} {_rate_limit_reason(r)[:100]}"
        except (httpx.TimeoutException, httpx.HTTPError) as e:
            last = f"{p['model']}: {type(e).__name__}"
        log.warning("chat provider failed, trying next: %s", last)

    raise RuntimeError(f"All chat providers failed — {last}")


# Agentic retrieval: the pre-fetched REFERENCE is a fast first guess, not the
# whole knowledge. The model decides when it is not enough and searches itself.
LOOKUP_RULE = (
    "LOOKUP: you have a search_knowledge tool over the business documents, "
    "verified past answers, live stock, and this caller's own bookings, bills and "
    "messages. If the REFERENCE above does not clearly answer the question, call "
    "search_knowledge (rephrase; try English keywords and names) BEFORE saying you "
    "don't know. Answer only from what you found. When a figure must be worked "
    "out, apply every condition in the found text (free limits, slabs, minimums) "
    "before quoting a number. If two searches find nothing, "
    "say you will confirm and get back."
)


async def reply(phone: str, message: str, history: list[dict] | None = None,
                prompt: str | None = None, customer: str | None = None,
                kb_context: str = "", tools_ctx: dict | None = None,
                agent_id: int | None = None) -> str:
    """Generate one reply.

    `history` overrides the stored thread — web calls keep their turns in memory
    rather than in the WhatsApp tables. `kb_context` is retrieved knowledge-base
    material for this question. `tools_ctx`, when given, lets the agent call
    tools (book, escalate, bill, stock) tied to this caller; it carries
    phone/name/channel. `agent_id` enables adaptive learning context.
    """
    import tools as tools_mod

    gender = agent_gender()
    system = f"{prompt or system_prompt(customer)}\n\n{gender_rule(gender)}"
    if kb_context:
        system = f"{system}\n\n{kb_context}"
    if tools_ctx is not None:
        system = f"{system}\n\n{LOOKUP_RULE}"

    # Adaptive learning — inject approved answers from past conversations.
    try:
        import adaptive
        learned = adaptive.learned_context_block(agent_id, message)
        if learned:
            system = f"{system}\n\n{learned}"
    except Exception:
        pass  # adaptive is best-effort, never blocks a reply

    messages = [
        {"role": "system", "content": system},
        *(db.get_history(phone) if history is None else history),
        {"role": "user", "content": message},
    ]

    use_tools = tools_ctx is not None
    # Bill creation needs multiple tool rounds (create → add item → add item →
    # finalize), so allow up to 4 rounds instead of the original 2. Each round
    # is one LLM call, so this still bounds latency.
    for _round in range(4):
        msg = await _chat_once(messages, with_tools=use_tools)
        calls = msg.get("tool_calls")
        if not calls:
            answer = (msg.get("content") or "").strip()
            if answer:
                return fix_gender(answer, gender)
            # An empty reply with nothing to run means the budget was spent on
            # reasoning — fail loudly so the caller hears the fallback, not silence.
            raise RuntimeError(
                f"{CHAT_MODEL} returned an empty reply — most likely max_tokens "
                f"({MAX_REPLY_TOKENS}) was consumed by reasoning. Raise "
                f"MAX_REPLY_TOKENS or lower REASONING_EFFORT in backend/.env."
            )
        # Run each requested tool and hand the results back for a spoken reply.
        messages.append(msg)  # the assistant message must precede its tool results
        for tc in calls:
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except (ValueError, TypeError):
                args = {}
            result = tools_mod.run(fn.get("name", ""), args, tools_ctx or {})
            messages.append({"role": "tool", "tool_call_id": tc.get("id"), "content": result})
        # After 3 tool rounds, force a natural-language answer.
        if _round >= 2:
            use_tools = False

    # Both rounds asked for tools without ever composing a reply — very rare.
    raise RuntimeError("model kept calling tools without answering")


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
