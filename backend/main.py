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
import time  # noqa: E402
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
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402

import asterisk_ari  # noqa: E402
import brain  # noqa: E402
import db  # noqa: E402
import kb  # noqa: E402
import recording  # noqa: E402
import vapi  # noqa: E402
import voice  # noqa: E402
import webcall  # noqa: E402
import whatsapp  # noqa: E402
import whatsapp_web  # noqa: E402
import adaptive  # noqa: E402
import auth
from auth import TokenAuthMiddleware  # noqa: E402
import events  # noqa: E402
import push  # noqa: E402
import worker  # noqa: E402

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


app.add_middleware(TokenAuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5174,http://localhost:5173").split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup() -> None:
    if auth.IS_PROD and not auth.API_TOKEN:
        raise RuntimeError("FATAL: API_TOKEN must be configured when ENV=production")
    db.init()
    # Anything that arrived while we were down, or whose reply threw, still
    # deserves an answer — once now, then on a timer.
    asyncio.create_task(whatsapp.answer_pending())
    asyncio.create_task(whatsapp.sweep_forever())
    # Only when Asterisk is configured — a machine without it starts normally.
    if asterisk_ari.configured():
        asyncio.create_task(asterisk_ari.listen())
    asyncio.create_task(voice.warm_up())
    asyncio.create_task(worker.run_forever())


@app.on_event("shutdown")
async def shutdown() -> None:
    await brain.close_http()


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
        "model": brain.CHAT_MODEL,
        # Empty until the first reply. The per-minute token cap is the ceiling
        # a live call actually runs into, and it is invisible otherwise.
        "budget": brain.budget,
        "worker": worker.state(),
        "push": push.configured(),
        "sseClients": events.client_count(),
        "pendingJobs": db.get_pending_jobs_count(),
        "automation": worker.automation_enabled(),
    }




# --- events, push & system controls -------------------------------------

@app.post("/api/events/ticket")
def sse_ticket() -> dict:
    """Issue a 60-second single-use ticket to connect to the SSE stream.

    Protected by TokenAuthMiddleware (requires Bearer API_TOKEN).
    The real API token never appears in the EventSource URL or proxy logs.
    """
    return {"ticket": events.create_ticket()}


@app.get("/api/events")
async def sse_events(request: Request):
    """Server-Sent Events stream for real-time dashboard updates."""
    return await events.sse_endpoint(request)


@app.get("/api/push/vapid-key")
def push_vapid_key() -> dict:
    return {"publicKey": push.VAPID_PUBLIC_KEY}


@app.post("/api/push/subscribe")
def push_subscribe(subscription: dict = Body(...)) -> dict:
    try:
        push.subscribe(subscription)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/push/unsubscribe")
def push_unsubscribe(payload: dict = Body(...)) -> dict:
    endpoint = payload.get("endpoint", "")
    if endpoint:
        push.unsubscribe(endpoint)
    return {"ok": True}


@app.post("/api/push/test")
def push_test() -> dict:
    sent = push.notify("VoiceFlow AI Test", "Push notifications are working!", {"url": "/app"})
    return {"ok": True, "sent": sent}


@app.get("/api/system/status")
def system_status() -> dict:
    return {
        "automation": worker.automation_enabled(),
        "whatsappAutoReply": db.auto_reply_global(),
        "worker": worker.state(),
        "pendingJobs": db.get_pending_jobs_count(),
        "recentJobs": db.get_recent_jobs(10),
    }


@app.post("/api/system/controls")
def system_controls(
    automation: bool | None = Body(None),
    whatsapp_auto_reply: bool | None = Body(None),
) -> dict:
    if automation is not None:
        worker.set_automation_enabled(automation)
    if whatsapp_auto_reply is not None:
        db.set_auto_reply_global(whatsapp_auto_reply)
        events.broadcast("status_change", {"whatsappAutoReply": whatsapp_auto_reply})
    return {
        "automation": worker.automation_enabled(),
        "whatsappAutoReply": db.auto_reply_global(),
    }

# --- dashboard ----------------------------------------------------------

@app.get("/api/stats")
def stats() -> dict:
    return db.get_stats()


@app.get("/api/calls")
def calls(limit: int = 50) -> list[dict]:
    return db.get_calls(limit)


def _call_file(call_id: str, ext: str) -> str:
    try:
        p = recording.path(call_id, ext)
    except ValueError:
        raise HTTPException(400, "Bad call id")
    if not os.path.exists(p):
        raise HTTPException(404, "No such recording")
    return p


@app.get("/api/calls/{call_id}/recording")
def call_recording(call_id: str) -> FileResponse:
    """The whole call as one mp3 — caller and agent, in order."""
    return FileResponse(_call_file(call_id, "mp3"), media_type="audio/mpeg",
                        filename=f"{call_id}.mp3", content_disposition_type="inline")


@app.get("/api/calls/{call_id}/record.txt")
def call_record(call_id: str) -> FileResponse:
    """Plain-text record: details, summary, timestamped transcript."""
    return FileResponse(_call_file(call_id, "txt"), media_type="text/plain; charset=utf-8",
                        filename=f"{call_id}.txt")


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
    raw_body = await request.body()
    sig = request.headers.get("x-hub-signature-256")
    if not whatsapp.verify_signature(raw_body, sig):
        raise HTTPException(401, "invalid webhook signature")
    import json
    try:
        payload = json.loads(raw_body)
    except Exception:
        raise HTTPException(400, "invalid json payload")
    for message in whatsapp.parse(payload):
        tasks.add_task(whatsapp.handle, message)
    return {"ok": True}


@app.post("/api/whatsapp/web/incoming")
async def whatsapp_web_incoming(
    request: Request, tasks: BackgroundTasks, x_bridge_token: str | None = Header(default=None)
) -> dict:
    """Inbound from the local wa-bridge. Same pipeline as the Meta webhook."""
    import hmac
    if not whatsapp_web.TOKEN or not x_bridge_token or not hmac.compare_digest(x_bridge_token, whatsapp_web.TOKEN):
        raise HTTPException(401, "bad or missing bridge token")
    tasks.add_task(whatsapp.handle, whatsapp_web.parse(await request.json()))
    return {"ok": True}


@app.get("/api/whatsapp/link")
async def whatsapp_link() -> dict:
    """Connection state for the dashboard: whether a QR needs scanning."""
    if whatsapp.PROVIDER != "web":
        return {"provider": "meta", "status": "connected" if whatsapp.configured() else "offline"}
    state = await whatsapp_web.status()
    # "connected" alone has been wrong before — the bridge once sat healthy for
    # three hours without receiving anything. Surface how long it has been
    # quiet so the dashboard can say so.
    last = state.get("lastEventAt") or state.get("lastCheckAt")
    quiet_minutes = round((time.time() * 1000 - last) / 60000) if last else None
    return {
        "provider": "web",
        "status": state.get("status", "offline"),
        "number": state.get("me"),
        "qr": state.get("qr"),
        "error": state.get("error"),
        "quietMinutes": quiet_minutes,
        "recoveries": state.get("recoveries", 0),
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
    events.broadcast("message_update", {"phone": to, "direction": "us"})
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


@app.put("/api/voice/tuning")
def voice_tuning(rate: str = Body(None), pitch: str = Body(None)) -> dict:
    """Speaking speed and pitch.

    Edge ships one female voice per Indian language, so pitch is the only way
    to make two agents sound different.
    """
    return voice.set_tuning(rate, pitch)


@app.post("/api/voice/select")
def voice_select(voice_id: str = Body(...), name: str = Body(...)) -> dict:
    voice.select(voice_id, name)
    return voice.profile()


@app.post("/api/voice/clone")
async def voice_clone(name: str = Form(...), samples: list[UploadFile] = File(...),
                      gender: str = Form("female")) -> dict:
    """Upload 30s+ of the owner's speech to create the cloned voice. `gender`
    ("female"/"male") makes the reply grammar match the cloned speaker."""
    payload = [(f.filename or "sample.wav", await f.read(), f.content_type or "audio/wav")
               for f in samples]
    try:
        voice_id = await voice.clone(name, payload, gender_=gender)
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


# --- knowledge base -----------------------------------------------------

@app.get("/api/kb")
def kb_list(agent_id: int | None = None) -> list[dict]:
    """Reference documents. Pass ?agent_id= to include an agent's own files."""
    return db.list_kb_docs(agent_id)


@app.post("/api/kb")
async def kb_upload(
    file: UploadFile = File(...), agent_id: int | None = Form(None)
) -> dict:
    """Upload one reference file the agent will answer from. agent_id blank = all
    agents."""
    data = await file.read()
    try:
        return kb.add_document(agent_id, file.filename or "document", data)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/kb/{doc_id}")
def kb_delete(doc_id: int) -> dict:
    db.delete_kb_doc(doc_id)
    return {"ok": True}


# --- bookings (recorded by agent tool calls) ----------------------------

@app.get("/api/bookings")
def bookings() -> list[dict]:
    """Bookings and escalations the agent recorded during conversations."""
    return db.get_bookings()


@app.delete("/api/bookings/{booking_id}")
def booking_delete(booking_id: int) -> dict:
    db.delete_booking(booking_id)
    return {"ok": True}


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


@app.get("/api/voice/cloned-audios")
def list_cloned_audios(limit: int = 50) -> list[dict]:
    """List all saved cloned audio files."""
    return db.list_cloned_audios(limit=limit)


@app.get("/api/voice/cloned-audios/play/{filename}")
def play_cloned_audio(filename: str):
    """Serve saved cloned audio file."""
    import cloning
    from fastapi.responses import FileResponse
    safe_name = os.path.basename(filename)
    path = cloning.CLONED_AUDIO_DIR / safe_name
    if not path.exists():
        raise HTTPException(404, "Audio file not found")
    return FileResponse(str(path), media_type="audio/mpeg")


@app.delete("/api/voice/cloned-audios/{audio_id}")
def delete_cloned_audio(audio_id: str):
    """Delete a saved cloned audio entry and file."""
    deleted = db.delete_cloned_audio(audio_id)
    if not deleted:
        raise HTTPException(404, "Cloned audio not found")
    return {"ok": True, "id": audio_id}


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
    facts: str = Body(""),
    is_default: bool = Body(False),
) -> dict:
    return db.save_agent(None, name=name, prompt=prompt, greeting=greeting,
                         language=language, facts=facts, is_default=is_default)


@app.put("/api/agents/{agent_id}")
def agent_update(
    agent_id: int,
    name: str = Body(...),
    prompt: str = Body(...),
    greeting: str = Body(...),
    language: str = Body("Hindi"),
    facts: str = Body(""),
    is_default: bool = Body(False),
) -> dict:
    return db.save_agent(agent_id, name=name, prompt=prompt, greeting=greeting,
                         language=language, facts=facts, is_default=is_default)


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


@app.delete("/api/contacts/{contact_id}")
def contact_delete(contact_id: int) -> dict:
    db.delete_contact(contact_id)
    return {"ok": True}


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
    if not vapi.configured() and not asterisk_ari.configured():
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
        if not worker.automation_enabled():
            log.info("campaign %d paused: automation disabled globally", campaign_id)
            db.set_campaign_status(campaign_id, "paused")
            events.broadcast("status_change", {"campaign_paused": campaign_id})
            return
        if db.get_campaigns() and next(
            (c for c in db.get_campaigns() if c["id"] == campaign_id), {}
        ).get("status") != "running":
            return  # stopped from the dashboard
        try:
            if vapi.configured():
                result = await vapi.create_call(target["phone"])
                db.set_target_status(target["id"], "called", call_id=result.get("id", ""))
            elif asterisk_ari.configured():
                endpoint = f"PJSIP/{target['phone']}@trunk"
                res = await asterisk_ari._rest("POST", "/channels", endpoint=endpoint, app=asterisk_ari.APP_NAME)
                db.set_target_status(target["id"], "called", call_id=(res or {}).get("id", ""))
            else:
                db.set_target_status(target["id"], "failed", error="No telephony configured")
        except Exception as e:  # noqa: BLE001
            db.set_target_status(target["id"], "failed", error=str(e)[:200])
        await asyncio.sleep(float(os.getenv("CAMPAIGN_GAP_SECONDS", "5")))
    db.set_campaign_status(campaign_id, "done")


# --- analytics ----------------------------------------------------------

@app.get("/api/analytics")
def analytics(days: int = 30) -> dict:
    return db.get_analytics(days)


# --- stock management ---------------------------------------------------

@app.get("/api/stock")
def stock_list(agent_id: int | None = None) -> list[dict]:
    return db.get_stock_items(agent_id)


@app.post("/api/stock")
def stock_create(
    name: str = Body(...),
    category: str = Body(None),
    quantity: float = Body(0),
    unit: str = Body("pcs"),
    rate: float = Body(0),
    gst_percent: float = Body(0),
    min_stock: float = Body(0),
    agent_id: int = Body(None),
) -> dict:
    return db.add_stock_item(name=name, category=category, quantity=quantity,
                             unit=unit, rate=rate, gst_percent=gst_percent,
                             min_stock=min_stock, agent_id=agent_id)


@app.put("/api/stock/{stock_id}")
def stock_update(
    stock_id: int,
    name: str = Body(None),
    category: str = Body(None),
    quantity: float = Body(None),
    unit: str = Body(None),
    rate: float = Body(None),
    gst_percent: float = Body(None),
    min_stock: float = Body(None),
) -> dict:
    fields = {k: v for k, v in
              {"name": name, "category": category, "quantity": quantity,
               "unit": unit, "rate": rate, "gst_percent": gst_percent,
               "min_stock": min_stock}.items()
              if v is not None}
    updated = db.update_stock_item(stock_id, **fields)
    if not updated:
        raise HTTPException(404, "No such stock item")
    return updated


@app.delete("/api/stock/{stock_id}")
def stock_delete(stock_id: int) -> dict:
    db.delete_stock_item(stock_id)
    return {"ok": True}


@app.get("/api/stock/{stock_id}/entries")
def stock_entries(stock_id: int) -> list[dict]:
    return db.get_stock_entries(stock_id)


@app.post("/api/stock/{stock_id}/entries")
def stock_entry_create(
    stock_id: int,
    entry_type: str = Body(...),
    quantity: float = Body(...),
    notes: str = Body(None),
) -> dict:
    if entry_type not in ("in", "out", "adjust"):
        raise HTTPException(400, "entry_type must be 'in', 'out', or 'adjust'")
    return db.add_stock_entry(stock_id, entry_type, quantity, notes)


# --- bills --------------------------------------------------------------

@app.get("/api/bills")
def bills_list() -> list[dict]:
    return db.get_bills()


@app.get("/api/bills/{bill_id}")
def bill_detail(bill_id: int) -> dict:
    found = db.get_bill(bill_id)
    if not found:
        raise HTTPException(404, "No such bill")
    return found


@app.delete("/api/bills/{bill_id}")
def bill_delete(bill_id: int) -> dict:
    db.delete_bill(bill_id)
    return {"ok": True}


# --- adaptive learning --------------------------------------------------

@app.get("/api/learned")
def learned_list(agent_id: int | None = None, status: str | None = None) -> list[dict]:
    return db.get_learned_answers(agent_id, status)


@app.put("/api/learned/{learned_id}")
def learned_update(learned_id: int, status: str = Body(..., embed=True)) -> dict:
    if status not in ("approved", "rejected"):
        raise HTTPException(400, "status must be 'approved' or 'rejected'")
    result = db.update_learned_status(learned_id, status)
    if not result:
        raise HTTPException(404, "No such learned answer")
    return result


@app.delete("/api/learned/{learned_id}")
def learned_delete(learned_id: int) -> dict:
    db.delete_learned(learned_id)
    return {"ok": True}


# --- evals / simulations ------------------------------------------------

@app.get("/api/evals")
def evals_list() -> list[dict]:
    return db.get_eval_cases()


@app.post("/api/evals")
def eval_create(
    name: str = Body(...),
    rubric: str = Body(...),
    mode: str = Body("script"),
    agent_id: int | None = Body(None),
    turns: str = Body(""),
    persona: str = Body(""),
    max_turns: int = Body(4),
) -> dict:
    if mode not in ("script", "simulate"):
        raise HTTPException(400, "mode must be 'script' or 'simulate'")
    if mode == "script" and not turns.strip():
        raise HTTPException(400, "A script test needs at least one caller line")
    if mode == "simulate" and not persona.strip():
        raise HTTPException(400, "A simulation needs a caller persona")
    if not name.strip() or not rubric.strip():
        raise HTTPException(400, "Name and pass criteria are required")
    # Bounded: each simulated turn is two model calls on a per-minute budget.
    max_turns = min(max(max_turns, 1), 8)
    case_id = db.add_eval_case(name.strip(), agent_id, mode, turns, persona, max_turns, rubric)
    return {"id": case_id}


@app.post("/api/evals/{case_id}/run")
async def eval_run(case_id: int) -> dict:
    import evals

    case = db.get_eval_case(case_id)
    if not case:
        raise HTTPException(404, "No such test")
    return await evals.run_case(case)


@app.delete("/api/evals/{case_id}")
def eval_delete(case_id: int) -> dict:
    db.delete_eval_case(case_id)
    return {"ok": True}


# --- web calling --------------------------------------------------------

@app.websocket("/api/web-call/ws")
async def web_call(ws: WebSocket) -> None:
    """Talk to the agent from the browser. One socket per call."""
    await ws.accept()
    agent_id = ws.query_params.get("agent")
    assistant = ws.query_params.get("mode") == "assistant"
    if assistant and not await _owner_ok(ws):
        await ws.send_json({"type": "error", "text": "Assistant mode needs the owner PIN "
                            "(ASSISTANT_PIN in backend/.env) when used from another device."})
        await ws.close()
        return
    session = webcall.Session(int(agent_id) if agent_id else None,
                              caller_key=ws.query_params.get("caller"), assistant=assistant)
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
            if "text" in message:
                try:
                    import json
                    payload = json.loads(message["text"])
                    kind = payload.get("type")
                except Exception:
                    payload, kind = {}, None
                if kind == "ping":
                    await ws.send_json({"type": "pong"})
                elif kind == "barge":
                    session.interrupted()
                elif kind == "say" and isinstance(payload.get("text"), str):
                    await webcall.respond(ws, session, payload["text"])
                continue
            if (audio := message.get("bytes")) is not None:
                await webcall.handle_turn(ws, session, audio)
    except WebSocketDisconnect:
        pass
    finally:
        session.close_record()
        # Analysis, stitched recording and text record — as their own task, not
        # awaited here. This handler can be cancelled the moment the socket
        # goes, and an await in its `finally` died with it: the call kept its
        # segments but never got a summary, an mp3 or a text record.
        task = asyncio.create_task(_finalize(session))
        _finalizing.add(task)  # a bare task can be garbage-collected mid-run
        task.add_done_callback(_finalizing.discard)


_finalizing: set[asyncio.Task] = set()

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


async def _owner_ok(ws: WebSocket) -> bool:
    """May this socket drive the owner assistant? It can message any number,
    so it is not open to whoever reaches the port.

    On this machine (directly, or through the Vite proxy from this machine):
    yes. From anywhere else — a phone on the Wi-Fi — the first message must
    carry ASSISTANT_PIN; with no PIN configured, remote use is refused.
    """
    origin = ws.client.host if ws.client else ""
    # X-Forwarded-For is only believed when the hop itself is local (our Vite
    # proxy); from anyone else it's a header they could simply type.
    fwd = ws.headers.get("x-forwarded-for", "")
    if origin in _LOOPBACK and fwd:
        origin = fwd.split(",")[0].strip()
    if origin in _LOOPBACK:
        return True
    pin = os.getenv("ASSISTANT_PIN", "")
    if not pin:
        return False
    try:
        first = await asyncio.wait_for(ws.receive_json(), timeout=15)
    except Exception:  # noqa: BLE001 - timeout, bad json, disconnect: all a no
        return False
    import hmac

    return first.get("type") == "auth" and hmac.compare_digest(str(first.get("pin", "")), pin)


async def _finalize(session) -> None:
    try:
        await webcall.finalize(session)
        events.broadcast("call_update", {"callId": session.id, "status": "ended"})
    except Exception as e:  # noqa: BLE001
        log.warning("post-call processing failed for %s: %s", session.id, e)


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
    import hmac
    if not vapi.WEBHOOK_SECRET or not x_vapi_secret or not hmac.compare_digest(x_vapi_secret, vapi.WEBHOOK_SECRET):
        raise HTTPException(401, "bad or missing webhook secret")

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
    caller_phone = _caller(call)
    call_outcome = structured.get("outcome") or OUTCOME_BY_REASON.get(msg.get("endedReason", ""), "Answered")
    events.broadcast("call_update", {"callId": call.get("id", ""), "phone": caller_phone, "status": "ended", "outcome": call_outcome})
    push.notify("Call Completed", f"Call with {caller_phone} ({call_outcome})", {"url": "/app/calls"}, tag="call")


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
