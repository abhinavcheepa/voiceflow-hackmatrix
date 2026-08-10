"""VoiceFlow AI backend.

Serves the dashboard API and handles Vapi's webhooks. Telephony is ours —
Vapi only runs the conversation.

    uvicorn main:app --reload --port 8000
"""

import os

from dotenv import load_dotenv

load_dotenv()  # must run before the local imports read os.getenv at import time

# A key that exists in .env but is left blank returns "", and os.getenv's
# default never fires — so `CHAT_BASE_URL=` silently became an empty URL rather
# than falling back. Dropping blanks makes "left blank" mean "use the default"
# everywhere at once, instead of patching ~19 call sites.
for _key, _value in list(os.environ.items()):
    if _value == "":
        del os.environ[_key]

import asyncio  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
from datetime import datetime  # noqa: E402

import httpx  # noqa: E402

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
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

import asterisk_ari  # noqa: E402
import brain  # noqa: E402
import db  # noqa: E402
import vapi  # noqa: E402
import voice  # noqa: E402
import webcall  # noqa: E402
import whatsapp  # noqa: E402
import whatsapp_web  # noqa: E402

app = FastAPI(title="VoiceFlow AI", version="0.1.0")

log = logging.getLogger("voiceflow")


@app.middleware("http")
async def surface_errors(request: Request, call_next):
    """Turn any unhandled exception into JSON the browser can actually read.

    Without this, an exception escapes to Starlette's outermost error handler,
    which sits ABOVE the CORS middleware — so the 500 arrives with no
    Access-Control-Allow-Origin header and the browser reports a CORS failure
    instead of the real cause. Every provider error looked like a CORS bug.

    Registered BEFORE CORSMiddleware on purpose: add_middleware prepends, so
    whatever is added last ends up outermost. CORS must stay outermost to wrap
    these responses.
    """
    try:
        return await call_next(request)
    except httpx.HTTPStatusError as e:
        # Providers explain themselves in the response body — pass that through
        # rather than a generic 500.
        detail = f"{e.request.url.host} returned {e.response.status_code}: {e.response.text[:400]}"
        log.warning("%s %s -> %s", request.method, request.url.path, detail)
        return JSONResponse({"detail": detail}, status_code=502)
    except Exception as e:  # noqa: BLE001
        log.exception("%s %s failed", request.method, request.url.path)
        return JSONResponse({"detail": f"{type(e).__name__}: {e}"}, status_code=500)


app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5174").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup() -> None:
    db.init()
    # Only when Asterisk is configured — a machine without it starts normally.
    if asterisk_ari.configured():
        asyncio.create_task(asterisk_ari.listen())


@app.get("/health")
def health() -> dict:
    """Which integrations are live. The dashboard uses this to warn on setup gaps."""
    return {
        "ok": True,
        "vapi": vapi.configured(),
        "whatsapp": whatsapp.configured(),
        "voice": voice.configured(),
        "brain": brain.configured(),
        "voiceReady": bool(voice.voice_id()),
        "stt": brain.stt_backend(),
        "asterisk": asterisk_ari.configured(),
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


@app.post("/api/whatsapp/web/incoming")
async def whatsapp_web_incoming(
    request: Request, tasks: BackgroundTasks, x_bridge_token: str | None = Header(default=None)
) -> dict:
    """Inbound from the local wa-bridge. Same pipeline as the Meta webhook."""
    if whatsapp_web.TOKEN and x_bridge_token != whatsapp_web.TOKEN:
        raise HTTPException(401, "bad bridge token")
    tasks.add_task(whatsapp.handle, whatsapp_web.parse(await request.json()))
    return {"ok": True}


@app.get("/api/whatsapp/link")
async def whatsapp_link() -> dict:
    """Connection state for the dashboard: whether a QR needs scanning."""
    if whatsapp.PROVIDER != "web":
        return {"provider": "meta", "status": "connected" if whatsapp.configured() else "offline"}
    state = await whatsapp_web.status()
    return {
        "provider": "web",
        "status": state.get("status", "offline"),
        "number": state.get("me"),
        "qr": state.get("qr"),
        "error": state.get("error"),
    }


@app.get("/api/whatsapp/auto-reply")
def auto_reply_get() -> dict:
    return {"enabled": db.auto_reply_global(), "allowlist": db.get_allowlist()}


@app.put("/api/whatsapp/allowlist")
def allowlist_set(numbers: str = Body(..., embed=True)) -> dict:
    """Restrict auto-replies to specific numbers.

    Empty means everyone — right for a dedicated business number, wrong for a
    number that also carries personal chats.
    """
    return {"allowlist": db.set_allowlist(numbers.replace("\n", ",").split(","))}


@app.put("/api/whatsapp/auto-reply")
def auto_reply_set(enabled: bool = Body(..., embed=True)) -> dict:
    """Master switch. Off means the agent answers nobody — messages still
    arrive and are stored, they just don't get a reply."""
    db.set_auto_reply_global(enabled)
    return {"enabled": enabled}


@app.put("/api/conversations/{phone}/auto-reply")
def auto_reply_thread(phone: str, enabled: bool = Body(..., embed=True)) -> dict:
    """Per-thread override, for taking one conversation over by hand."""
    db.set_auto_reply_for(phone, enabled)
    return {"phone": phone, "enabled": enabled}


@app.post("/api/whatsapp/sync")
async def whatsapp_sync(chats: int = Body(20, embed=True), messages: int = Body(10, embed=True)) -> dict:
    """Pull existing WhatsApp chats into the dashboard.

    History only — these are written straight to the database and never run
    through the agent, so importing cannot fire replies at old conversations.
    Safe to run twice: messages carry WhatsApp's own id and duplicates are
    ignored.
    """
    if whatsapp.PROVIDER != "web":
        raise HTTPException(400, "Chat import is only available on the WhatsApp Web provider")

    try:
        threads = await whatsapp_web.sync(chats, messages)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 503:
            raise HTTPException(503, e.response.json().get("error", "Chat import unavailable"))
        raise
    imported = 0
    for thread in threads:
        for m in thread["messages"]:
            db.add_message(
                thread["from"],
                m["sender"],
                "voice" if m["type"] == "audio" else "text",
                m["text"] or "🎤 Voice note",
                name=thread.get("name"),
                language=brain.detect_language(m["text"] or ""),
                at=m.get("at"),
                wa_id=m.get("id"),
            )
            imported += 1
    return {"threads": len(threads), "messages": imported}


@app.post("/api/whatsapp/logout")
async def whatsapp_logout() -> dict:
    if whatsapp.PROVIDER != "web":
        raise HTTPException(400, "Only the WhatsApp Web provider can log out")
    await whatsapp_web.logout()
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


@app.get("/api/voice/library")
async def voice_library(refresh: bool = False) -> list[dict]:
    """Ready-made voices. Free on every Cartesia plan, unlike cloning."""
    try:
        return await voice.library(refresh)
    except RuntimeError as e:
        raise HTTPException(503, str(e))


@app.post("/api/voice/select")
def voice_select(voice_id: str = Body(...), name: str = Body(...)) -> dict:
    voice.select(voice_id, name)
    return voice.profile()


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
async def voice_preview(
    text: str = Body(...), voice_id: str = Body(None), language: str = Body(None)
) -> Response:
    """Speak `text`. Pass `voice_id` to audition a voice without selecting it."""
    try:
        audio = await voice.synthesize(text, language, as_voice=voice_id)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    return Response(content=audio, media_type=voice.OUTPUT_MIME)


@app.put("/api/voice/style")
def voice_style(style: str = Body(..., embed=True)) -> dict:
    """The writing style WhatsApp text replies imitate."""
    db.set_setting(brain.STYLE_KEY, style)
    return {"style": style}


# --- agents -------------------------------------------------------------

@app.get("/api/agents")
def agents() -> list[dict]:
    return db.get_agents()


@app.post("/api/agents")
def agent_create(
    name: str = Body(...),
    prompt: str = Body(...),
    greeting: str = Body(...),
    language: str = Body("Hindi"),
    is_default: bool = Body(False),
) -> dict:
    return db.save_agent(None, name=name, prompt=prompt, greeting=greeting,
                         language=language, is_default=is_default)


@app.put("/api/agents/{agent_id}")
def agent_update(
    agent_id: int,
    name: str = Body(...),
    prompt: str = Body(...),
    greeting: str = Body(...),
    language: str = Body("Hindi"),
    is_default: bool = Body(False),
) -> dict:
    return db.save_agent(agent_id, name=name, prompt=prompt, greeting=greeting,
                         language=language, is_default=is_default)


@app.delete("/api/agents/{agent_id}")
def agent_delete(agent_id: int) -> dict:
    try:
        db.delete_agent(agent_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


# --- contacts -----------------------------------------------------------

@app.get("/api/contacts")
def contacts() -> list[dict]:
    return db.get_contacts()


@app.get("/api/contacts/{contact_id}")
def contact(contact_id: int) -> dict:
    found = db.get_contact(contact_id)
    if not found:
        raise HTTPException(404, "No such contact")
    return found


@app.put("/api/contacts/{contact_id}")
def contact_update(
    contact_id: int,
    name: str = Body(None),
    email: str = Body(None),
    status: str = Body(None),
    notes: str = Body(None),
) -> dict:
    fields = {k: v for k, v in
              {"name": name, "email": email, "status": status, "notes": notes}.items()
              if v is not None}
    updated = db.update_contact(contact_id, **fields)
    if not updated:
        raise HTTPException(404, "No such contact")
    return updated


# --- campaigns ----------------------------------------------------------

@app.get("/api/campaigns")
def campaigns() -> list[dict]:
    return db.get_campaigns()


@app.get("/api/campaigns/{campaign_id}/targets")
def campaign_targets(campaign_id: int) -> list[dict]:
    return db.get_campaign_targets(campaign_id)


@app.post("/api/campaigns")
def campaign_create(
    name: str = Body(...),
    numbers: str = Body(...),
    agent_id: int = Body(None),
) -> dict:
    """`numbers` is pasted text — one per line, optional "name, phone"."""
    targets = _parse_numbers(numbers)
    if not targets:
        raise HTTPException(400, "No valid phone numbers found")
    return {"id": db.create_campaign(name, agent_id, targets), "targets": len(targets)}


@app.post("/api/campaigns/{campaign_id}/start")
async def campaign_start(campaign_id: int, tasks: BackgroundTasks) -> dict:
    if not vapi.configured():
        raise HTTPException(503, "No telephony configured — set VAPI_PRIVATE_KEY or Asterisk")
    db.set_campaign_status(campaign_id, "running")
    tasks.add_task(_run_campaign, campaign_id)
    return {"ok": True, "status": "running"}


@app.post("/api/campaigns/{campaign_id}/stop")
def campaign_stop(campaign_id: int) -> dict:
    db.set_campaign_status(campaign_id, "paused")
    return {"ok": True, "status": "paused"}


PHONE_RE = re.compile(r"\+?\d[\d\s-]{7,18}\d")


def _parse_numbers(text: str) -> list[dict]:
    """Accept pasted lists: bare numbers, or "Name, +91..." per line."""
    out, seen = [], set()
    for line in text.splitlines():
        match = PHONE_RE.search(line)
        if not match:
            continue
        phone = re.sub(r"[\s-]", "", match.group())
        if phone in seen:
            continue
        seen.add(phone)
        name = line[: match.start()].strip(" ,\t") or None
        out.append({"name": name, "phone": phone})
    return out


async def _run_campaign(campaign_id: int) -> None:
    """Dial targets one at a time.

    Sequential on purpose: a parallel dialer on an unverified trunk is how you
    get rate-limited or flagged for spam on the first run.
    """
    while (target := db.next_pending_target(campaign_id)):
        if db.get_campaigns() and next(
            (c for c in db.get_campaigns() if c["id"] == campaign_id), {}
        ).get("status") != "running":
            return  # stopped from the dashboard
        try:
            result = await vapi.create_call(target["phone"])
            db.set_target_status(target["id"], "called", call_id=result.get("id", ""))
        except Exception as e:  # noqa: BLE001
            db.set_target_status(target["id"], "failed", error=str(e)[:200])
        await asyncio.sleep(float(os.getenv("CAMPAIGN_GAP_SECONDS", "5")))
    db.set_campaign_status(campaign_id, "done")


# --- analytics ----------------------------------------------------------

@app.get("/api/analytics")
def analytics(days: int = 30) -> dict:
    return db.get_analytics(days)


# --- web calling --------------------------------------------------------

@app.websocket("/api/web-call/ws")
async def web_call(ws: WebSocket) -> None:
    """Talk to the agent from the browser. One socket per call."""
    await ws.accept()
    agent_id = ws.query_params.get("agent")
    session = webcall.Session(int(agent_id) if agent_id else None)
    session.open_record()
    await ws.send_json(
        {"type": "connected", "callId": session.id, "agent": session.agent.get("name", "")}
    )

    try:
        await webcall.speak(ws, session, session.greeting)
        while True:
            message = await ws.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if (audio := message.get("bytes")) is not None:
                await webcall.handle_turn(ws, session, audio)
    except WebSocketDisconnect:
        pass
    finally:
        session.close_record()


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
