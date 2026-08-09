"""Vapi client + assistant definition.

Vapi runs the realtime voice loop (STT -> LLM -> TTS). Telephony stays ours:
our own dialer/PBX sends the leg to Vapi over SIP, and Vapi asks this backend
which assistant to use via the `assistant-request` webhook.
"""

import os

import httpx

import voice

BASE_URL = os.getenv("VAPI_BASE_URL", "https://api.vapi.ai")
PRIVATE_KEY = os.getenv("VAPI_PRIVATE_KEY", "")
ASSISTANT_ID = os.getenv("VAPI_ASSISTANT_ID", "")
WEBHOOK_SECRET = os.getenv("VAPI_WEBHOOK_SECRET", "")
PUBLIC_URL = os.getenv("PUBLIC_URL", "")

BUSINESS_NAME = os.getenv("BUSINESS_NAME", "VoiceFlow Demo")
OWNER_NAME = os.getenv("OWNER_NAME", "the owner")
VOICE_PROVIDER = os.getenv("VOICE_PROVIDER", "cartesia")

SYSTEM_PROMPT = f"""You are the voice of {BUSINESS_NAME}, answering calls on behalf of {OWNER_NAME}.

Rules:
- Detect the caller's language from their first words and reply in that same
  language for the whole call. Hindi, Hinglish, Marathi, Tamil and English are
  all common. Never switch languages unless the caller does.
- Speak like a warm, efficient shop owner, not a call-centre script. Short
  sentences. No corporate filler.
- You can answer questions about timings, pricing and availability, take
  bookings, and capture the caller's name and requirement.
- If you cannot help, say so plainly and offer a callback.
- Never invent prices, stock or appointment slots you were not given.
"""


def assistant() -> dict:
    """Transient assistant returned to Vapi on `assistant-request`.

    Editing this file changes behaviour on the next call — no dashboard edits.
    """
    config = {
        "name": BUSINESS_NAME,
        "firstMessage": f"Namaste! {BUSINESS_NAME} me aapka swagat hai. Main aapki kya madad kar sakti hoon?",
        "model": {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}],
        },
        "transcriber": {"provider": "deepgram", "model": "nova-2", "language": "multi"},
        "endCallFunctionEnabled": True,
        "silenceTimeoutSeconds": 20,
    }
    # The cloned voice created in Voice Studio; falls back to VOICE_ID from .env.
    cloned = voice.voice_id()
    if cloned:
        config["voice"] = {"provider": VOICE_PROVIDER, "voiceId": cloned}
    if PUBLIC_URL:
        config["server"] = {"url": f"{PUBLIC_URL}/api/vapi/webhook", "secret": WEBHOOK_SECRET}
    return config


def configured() -> bool:
    return bool(PRIVATE_KEY)


async def _post(path: str, payload: dict) -> dict:
    if not configured():
        raise RuntimeError("VAPI_PRIVATE_KEY is not set — copy .env.example to .env and fill it in")
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{BASE_URL}{path}",
            json=payload,
            headers={"Authorization": f"Bearer {PRIVATE_KEY}"},
        )
        r.raise_for_status()
        return r.json()


async def create_call(number: str, phone_number_id: str | None = None) -> dict:
    """Outbound call. Only needed while our own dialer isn't wired up yet."""
    payload: dict = {"customer": {"number": number}}
    payload.update({"assistantId": ASSISTANT_ID} if ASSISTANT_ID else {"assistant": assistant()})
    pnid = phone_number_id or os.getenv("VAPI_PHONE_NUMBER_ID", "")
    if pnid:
        payload["phoneNumberId"] = pnid
    return await _post("/call", payload)


async def register_sip_number(sip_uri: str) -> dict:
    """Point our own PBX at Vapi.

    Creates a SIP endpoint like sip:voiceflow@sip.vapi.ai. Our Asterisk/FreeSWITCH
    dials that URI and Vapi handles the conversation. Run once during setup.
    """
    payload = {"provider": "vapi", "sipUri": sip_uri}
    if ASSISTANT_ID:
        payload["assistantId"] = ASSISTANT_ID
    return await _post("/phone-number", payload)
