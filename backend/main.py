"""VoiceFlow AI backend.

Serves the dashboard API and handles Vapi's webhooks. Telephony is ours —
Vapi only runs the conversation.

    uvicorn main:app --reload --port 8000
"""

from dotenv import load_dotenv

load_dotenv()  # must run before db/vapi read os.getenv at import time

import os  # noqa: E402
from datetime import datetime  # noqa: E402

from fastapi import (  # noqa: E402
    BackgroundTasks,
    Body,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

import brain  # noqa: E402
import db  # noqa: E402
import vapi  # noqa: E402
import voice  # noqa: E402
import whatsapp  # noqa: E402

app = FastAPI(title="VoiceFlow AI", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5174").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    db.init()


@app.get("/health")
def health() -> dict:
    """Which integrations are live. The dashboard uses this to warn on setup gaps."""
    return {
        "ok": True,
        "vapi": vapi.configured(),
        "whatsapp": whatsapp.configured(),
        "voice": voice.configured(),
        "brain": brain.configured(),
        "voiceCloned": bool(voice.voice_id()),
    }


# --- dashboard ----------------------------------------------------------

@app.get("/api/stats")
def stats() -> dict:
    return db.get_stats()


@app.get("/api/calls")
def calls(limit: int = 50) -> list[dict]:
    return db.get_calls(limit)


@app.get("/api/calls-by-hour")
def calls_by_hour() -> list[dict]:
    return db.get_calls_by_hour()


@app.get("/api/languages")
def languages() -> list[dict]:
    return db.get_languages()


@app.get("/api/conversations")
def conversations() -> list[dict]:
    return db.get_conversations()


# --- outbound / setup ---------------------------------------------------

@app.post("/api/calls/outbound")
async def outbound(number: str = Body(..., embed=True)) -> dict:
    try:
        return await vapi.create_call(number)
    except RuntimeError as e:
        raise HTTPException(503, str(e))


@app.post("/api/vapi/sip-number")
async def sip_number(sip_uri: str = Body(..., embed=True)) -> dict:
    """One-time setup: give our own PBX a Vapi SIP endpoint to dial."""
    try:
        return await vapi.register_sip_number(sip_uri)
    except RuntimeError as e:
        raise HTTPException(503, str(e))


# --- whatsapp -----------------------------------------------------------

@app.get("/api/whatsapp/webhook")
def whatsapp_verify(request: Request) -> Response:
    """Meta's subscription handshake. Must return the raw challenge, not JSON."""
    q = request.query_params
    try:
        challenge = whatsapp.verify(
            q.get("hub.mode", ""), q.get("hub.verify_token", ""), q.get("hub.challenge", "")
        )
    except PermissionError as e:
        raise HTTPException(403, str(e))
    return Response(content=challenge, media_type="text/plain")


@app.post("/api/whatsapp/webhook")
async def whatsapp_receive(request: Request, tasks: BackgroundTasks) -> dict:
    """Ack immediately, reply in the background.

    Meta retries anything it doesn't get a 200 for within seconds, and a full
    reply (transcribe -> LLM -> synthesise -> upload) takes longer than that.
    """
    for message in whatsapp.parse(await request.json()):
        tasks.add_task(whatsapp.handle, message)
    return {"ok": True}


@app.post("/api/whatsapp/send")
async def whatsapp_send(to: str = Body(...), text: str = Body(...)) -> dict:
    """Manual send from the dashboard — the owner taking over a thread."""
    try:
        result = await whatsapp.send_text(to, text)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    db.add_message(to, "us", "text", text)
    return result


# --- voice cloning ------------------------------------------------------

@app.get("/api/voice/profile")
def voice_profile() -> dict:
    return {**voice.profile(), "style": db.get_setting(brain.STYLE_KEY, brain.DEFAULT_STYLE)}


@app.post("/api/voice/clone")
async def voice_clone(name: str = Form(...), samples: list[UploadFile] = File(...)) -> dict:
    """Upload 30s+ of the owner's speech to create the cloned voice."""
    payload = [(f.filename or "sample.wav", await f.read(), f.content_type or "audio/wav")
               for f in samples]
    try:
        voice_id = await voice.clone(name, payload)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    return {"voiceId": voice_id, **voice.profile()}


@app.delete("/api/voice/clone")
async def voice_clone_delete() -> dict:
    try:
        await voice.delete_clone()
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    return voice.profile()


@app.post("/api/voice/preview")
async def voice_preview(text: str = Body(..., embed=True)) -> Response:
    try:
        audio = await voice.synthesize(text)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    return Response(content=audio, media_type=voice.OUTPUT_MIME)


@app.put("/api/voice/style")
def voice_style(style: str = Body(..., embed=True)) -> dict:
    """The writing style WhatsApp text replies imitate."""
    db.set_setting(brain.STYLE_KEY, style)
    return {"style": style}


# --- vapi webhook -------------------------------------------------------

# endedReason -> what the dashboard shows. Anything unmapped is just "Answered".
OUTCOME_BY_REASON = {
    "customer-did-not-answer": "Missed",
    "customer-busy": "Missed",
    "assistant-forwarded-call": "Escalated",
    "assistant-error": "Escalated",
    "pipeline-error": "Escalated",
}


@app.post("/api/vapi/webhook")
async def webhook(request: Request, x_vapi_secret: str | None = Header(default=None)) -> dict:
    if vapi.WEBHOOK_SECRET and x_vapi_secret != vapi.WEBHOOK_SECRET:
        raise HTTPException(401, "bad webhook secret")

    msg = (await request.json()).get("message", {})
    kind = msg.get("type")

    # Inbound call arrives from our own SIP trunk; tell Vapi how to behave.
    if kind == "assistant-request":
        return {"assistant": vapi.assistant()}

    if kind == "status-update":
        call = msg.get("call", {})
        if call.get("id"):
            db.upsert_call(
                call["id"],
                status=msg.get("status", "unknown"),
                phone=_caller(call),
            )
        return {"ok": True}

    if kind == "end-of-call-report":
        _save_report(msg)
        return {"ok": True}

    if kind == "tool-calls":
        return {"results": _run_tools(msg)}

    return {"ok": True}


def _caller(call: dict) -> str:
    return call.get("customer", {}).get("number") or "unknown"


def _duration(msg: dict, call: dict) -> int:
    if msg.get("durationSeconds"):
        return round(msg["durationSeconds"])
    started, ended = call.get("startedAt"), call.get("endedAt")
    if started and ended:
        delta = datetime.fromisoformat(ended.replace("Z", "+00:00")) - datetime.fromisoformat(
            started.replace("Z", "+00:00")
        )
        return round(delta.total_seconds())
    return 0


def _save_report(msg: dict) -> None:
    call = msg.get("call", {})
    artifact = msg.get("artifact", {})
    analysis = msg.get("analysis", {})
    structured = analysis.get("structuredData") or {}

    db.upsert_call(
        call.get("id", ""),
        status="ended",
        phone=_caller(call),
        name=structured.get("name") or "Unknown",
        language=structured.get("language") or "Hindi",
        intent=structured.get("intent") or (analysis.get("summary") or "General enquiry")[:80],
        duration_sec=_duration(msg, call),
        outcome=structured.get("outcome")
        or OUTCOME_BY_REASON.get(msg.get("endedReason", ""), "Answered"),
        transcript=artifact.get("transcript") or msg.get("transcript"),
        recording_url=artifact.get("recordingUrl") or msg.get("recordingUrl"),
        started_at=call.get("startedAt") or datetime.now().astimezone().isoformat(),
    )


def _run_tools(msg: dict) -> list[dict]:
    """Assistant-callable functions. Only what the demo needs, added on demand."""
    results = []
    for tc in msg.get("toolCallList", []):
        name = tc.get("name") or tc.get("function", {}).get("name", "")
        args = tc.get("arguments") or tc.get("function", {}).get("arguments") or {}
        if name == "book_appointment":
            results.append({
                "name": name,
                "toolCallId": tc.get("id"),
                "result": f"Booked for {args.get('slot', 'the requested slot')}.",
            })
        else:
            results.append({"name": name, "toolCallId": tc.get("id"),
                            "result": "Not available right now."})
    return results
