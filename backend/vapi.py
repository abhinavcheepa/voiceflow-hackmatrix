"""Vapi client + assistant definition.

Vapi runs the realtime voice loop (STT -> LLM -> TTS). Telephony stays ours:
our own dialer/PBX sends the leg to Vapi over SIP, and Vapi asks this backend
which assistant to use via the `assistant-request` webhook.
"""

import os

import httpx

import db
import voice

BASE_URL = os.getenv("VAPI_BASE_URL", "https://api.vapi.ai")
PRIVATE_KEY = os.getenv("VAPI_PRIVATE_KEY", "")
ASSISTANT_ID = os.getenv("VAPI_ASSISTANT_ID", "")
WEBHOOK_SECRET = os.getenv("VAPI_WEBHOOK_SECRET", "")
PUBLIC_URL = os.getenv("PUBLIC_URL", "")

BUSINESS_NAME = os.getenv("BUSINESS_NAME", "VoiceFlow Demo")
OWNER_NAME = os.getenv("OWNER_NAME", "the owner")
# When set, the agent can hand a stuck call to a human on this number.
OWNER_PHONE = os.getenv("OWNER_PHONE", "")
MAX_CALL_SECONDS = int(os.getenv("MAX_CALL_SECONDS") or 1800)
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


# What Vapi should pull out of every call. The keys here MUST match what the
# webhook's _save_report reads (name, language, intent, outcome) — the schema is
# the contract between "what Vapi extracts" and "what the dashboard stores".
ANALYSIS_PLAN = {
    "summaryPrompt": (
        "Summarise this call in two short sentences: what the caller wanted and "
        "what was agreed. Plain language, no preamble."
    ),
    "structuredDataPrompt": (
        "Extract the caller's details from the conversation. Use null for anything "
        "not actually stated — never guess."
    ),
    "structuredDataSchema": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Caller's name, or null"},
            "language": {
                "type": "string",
                "description": "Language spoken: Hindi, Hinglish, English, Marathi, Tamil",
            },
            "intent": {"type": "string", "description": "What they called about, in a few words"},
            "item": {"type": "string", "description": "Product or service discussed, or null"},
            "outcome": {
                "type": "string",
                "enum": ["Answered", "Booking", "Escalated", "Missed"],
                "description": "How the call ended",
            },
        },
    },
    "successEvaluationPrompt": (
        "Did the agent help the caller and stay truthful — never inventing a price, "
        "stock item or appointment slot it was not given?"
    ),
    "successEvaluationRubric": "PassFail",
}

# When the caller says any of these, end the call cleanly instead of talking on.
END_CALL_PHRASES = ["bye", "goodbye", "alvida", "thank you bye", "dhanyavaad", "ok bye", "that's all"]

# Spoken if the caller goes silent, before the call is finally timed out.
IDLE_MESSAGES = ["Aap wahan hain? Main aapki madad ke liye yahin hoon.", "Hello, kya aap sun sakte hain?"]


def tools() -> list[dict]:
    """Functions the assistant may call. Each maps to a branch in the webhook's
    _run_tools, or to a Vapi built-in. transferCall is only offered when there is
    a real number to transfer to."""
    built = [
        {"type": "endCall"},
        {"type": "dtmf"},  # press keypad digits for IVR menus
        {
            "type": "function",
            "function": {
                "name": "book_appointment",
                "description": (
                    "Record a requested appointment or order. Call this once you have "
                    "the caller's name and the time or item they want."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Caller's name"},
                        "slot": {"type": "string", "description": "Requested time, date or item"},
                    },
                    "required": ["slot"],
                },
            },
        },
    ]
    if OWNER_PHONE:
        built.append({
            "type": "transferCall",
            "destinations": [{
                "type": "number",
                "number": OWNER_PHONE,
                "message": "Main aapko owner se connect kar rahi hoon, ek pal rukiye.",
            }],
        })
    return built


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
            "tools": tools(),
        },
        "transcriber": {"provider": "deepgram", "model": "nova-2", "language": "multi"},
        "endCallFunctionEnabled": True,
        # Say a couple of Indian end-call phrases out loud, then hang up.
        "endCallPhrases": END_CALL_PHRASES,
        # Every call gets a summary, extracted fields and a pass/fail — this is
        # what fills structuredData that the webhook already knows how to store.
        "analysisPlan": ANALYSIS_PLAN,
        # Nudge a silent caller instead of sitting in dead air.
        "messagePlan": {"idleMessages": IDLE_MESSAGES, "idleTimeoutSeconds": 8},
        "backgroundDenoisingEnabled": True,
        "silenceTimeoutSeconds": 20,
        "maxDurationSeconds": MAX_CALL_SECONDS,
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
