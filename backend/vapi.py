"""Vapi client + assistant definition.

Telephony is handled via Asterisk. This module retains the Vapi assistant configuration
and persona definition to support the Vapi webhook (/api/vapi/webhook) when active.
"""

import os

import db
import voice

BASE_URL = os.getenv("VAPI_BASE_URL", "https://api.vapi.ai")
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


def assistant(agent_id: int | None = None) -> dict:
    """Transient assistant returned to Vapi on `assistant-request`.

    Persona comes from the selected agent in the dashboard; SYSTEM_PROMPT here
    is the base brief that every agent's own prompt is appended to.
    """
    agent = db.get_agent(agent_id) or {}
    prompt = f"{SYSTEM_PROMPT}\n{agent.get('prompt', '')}".strip()

    config = {
        "name": agent.get("name") or BUSINESS_NAME,
        "firstMessage": agent.get("greeting")
        or f"Namaste! {BUSINESS_NAME} me aapka swagat hai. Main aapki kya madad kar sakti hoon?",
        "model": {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "messages": [{"role": "system", "content": prompt}],
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
