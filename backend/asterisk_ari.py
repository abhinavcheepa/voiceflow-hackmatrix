"""Asterisk ARI adapter — Phases 1-5.

Asterisk's dialplan hands a channel to Stasis(voiceflow-ai); this module picks
it up over the ARI WebSocket, answers, logs the call, and plays the greeting.

Campaign and outbound calls are originated via Asterisk ARI directly. When
answered, they enter Stasis, play the greeting (ASTERISK_GREETING, ~6 seconds),
and hang up.

Runs only when ASTERISK_ARI_URL is set, so a machine without Asterisk starts
normally.
"""

import asyncio
import base64
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from urllib.parse import quote, urlencode

import httpx

import db

log = logging.getLogger("voiceflow.ari")

ARI_URL = os.getenv("ASTERISK_ARI_URL", "").rstrip("/")
ARI_USER = os.getenv("ASTERISK_ARI_USERNAME", "voiceflow")
ARI_PASS = os.getenv("ASTERISK_ARI_PASSWORD", "")
APP_NAME = os.getenv("ASTERISK_ARI_APP", "voiceflow-ai")

# Outbound trunk configured in Asterisk pjsip.conf
ASTERISK_TRUNK = os.getenv("ASTERISK_TRUNK", "trunk")

# Stock Asterisk sound, no file needed. Swap for "sound:voiceflow-greeting"
# once you drop a generated clip into Asterisk's sounds directory.
GREETING_MEDIA = os.getenv("ASTERISK_GREETING", "sound:hello-world")

MAX_CALL_SECONDS = int(os.getenv("MAX_CALL_SECONDS", "1800"))

# Tracks outbound channel IDs originated by this process to correlate ChannelDestroyed
_OUTBOUND_CHANNELS: set[str] = set()


def configured() -> bool:
    return bool(ARI_URL and ARI_PASS)


def _auth_header() -> dict:
    token = base64.b64encode(f"{ARI_USER}:{ARI_PASS}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


async def _rest(method: str, path: str, **params) -> dict | None:
    """One ARI REST call. Returns None on 404 — a channel that already hung up."""
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.request(
            method, f"{ARI_URL}{path}", headers=_auth_header(), params=params or None
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json() if r.content else {}


# --- channel actions ----------------------------------------------------

async def answer(channel_id: str) -> None:
    await _rest("POST", f"/channels/{quote(channel_id)}/answer")


async def play(channel_id: str, media: str) -> dict | None:
    return await _rest("POST", f"/channels/{quote(channel_id)}/play", media=media)


async def hangup(channel_id: str) -> None:
    await _rest("DELETE", f"/channels/{quote(channel_id)}")


async def create_call(number: str) -> dict:
    """Originate an outbound call via Asterisk ARI.

    Dials the configured trunk and drops the answered call into Stasis(APP_NAME).
    Pre-registers channelId in _OUTBOUND_CHANNELS and db before dispatching to prevent
    race conditions if ChannelDestroyed fires immediately.
    """
    if not configured():
        raise RuntimeError("Asterisk ARI is not configured — check ASTERISK_ARI_URL and ASTERISK_ARI_PASSWORD")

    channel_id = f"out-{uuid.uuid4().hex[:12]}"
    _OUTBOUND_CHANNELS.add(channel_id)
    db.upsert_call(channel_id, phone=number, status="ringing")

    endpoint = f"PJSIP/{number}@{ASTERISK_TRUNK}"
    caller_id = os.getenv("ASTERISK_CALLER_ID", "VoiceFlow")

    try:
        res = await _rest(
            "POST",
            "/channels",
            endpoint=endpoint,
            app=APP_NAME,
            channelId=channel_id,
            callerId=caller_id,
            timeout=45,
        )
        if not res:
            raise RuntimeError(f"Asterisk ARI originate failed for {endpoint}")
        return res
    except Exception:
        db.upsert_call(channel_id, status="failed", outcome="Missed")
        _OUTBOUND_CHANNELS.discard(channel_id)
        raise


# --- event handling -----------------------------------------------------

async def _on_stasis_start(event: dict) -> None:
    channel = event.get("channel", {})
    channel_id = channel.get("id", "")
    caller = channel.get("caller", {}).get("number") or "unknown"

    log.info("call started: %s from %s", channel_id, caller)
    db.upsert_call(
        channel_id,
        phone=caller,
        status="active",
        started_at=datetime.now(timezone.utc).isoformat(),
    )

    state = channel.get("state", "").lower()
    if state != "up":
        await answer(channel_id)
    await play(channel_id, GREETING_MEDIA)

    # Phase 1-5: prove the path, then release the channel rather than
    # leaving it open with nothing driving it (~6 seconds greeting).
    await asyncio.sleep(6)
    await hangup(channel_id)


async def _on_stasis_end(event: dict) -> None:
    channel_id = event.get("channel", {}).get("id", "")
    log.info("call ended: %s", channel_id)
    _OUTBOUND_CHANNELS.discard(channel_id)
    db.upsert_call(channel_id, status="ended")


async def _on_channel_destroyed(event: dict) -> None:
    channel = event.get("channel", {})
    channel_id = channel.get("id", "")
    if channel_id not in _OUTBOUND_CHANNELS:
        return

    _OUTBOUND_CHANNELS.discard(channel_id)

    # Check if the call was already answered/active or ended normally
    with db.connect() as conn:
        row = conn.execute("SELECT status FROM calls WHERE vapi_call_id = ?", (channel_id,)).fetchone()
    current_status = row["status"] if row else ""

    if current_status in ("active", "ended"):
        return

    cause = event.get("cause")
    cause_txt = (event.get("cause_txt") or "").lower()

    if cause == 17 or "busy" in cause_txt:
        status = "busy"
    elif cause in (18, 19) or "no answer" in cause_txt:
        status = "no_answer"
    elif cause == 21 or "reject" in cause_txt:
        status = "rejected"
    else:
        status = "failed"

    log.info("originated channel destroyed before answer: %s (cause: %s / %s -> %s)", channel_id, cause, cause_txt, status)
    db.upsert_call(channel_id, status=status, outcome="Missed")


HANDLERS = {
    "StasisStart": _on_stasis_start,
    "StasisEnd": _on_stasis_end,
    "ChannelDestroyed": _on_channel_destroyed,
}


async def _consume(ws) -> None:
    async for raw in ws:
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        handler = HANDLERS.get(event.get("type", ""))
        if not handler:
            continue
        try:
            await handler(event)
        except Exception:  # noqa: BLE001 - one bad call must not kill the listener
            log.exception("ARI handler failed for %s", event.get("type"))


async def listen() -> None:
    """Connect to ARI and stay connected, reconnecting with backoff.

    Asterisk restarts, and the gateway has to survive that without a restart
    of its own.
    """
    import websockets

    ws_url = ARI_URL.replace("https://", "wss://").replace("http://", "ws://")
    query = urlencode({"app": APP_NAME, "api_key": f"{ARI_USER}:{ARI_PASS}", "subscribeAll": "true"})
    delay = 1

    while True:
        try:
            async with websockets.connect(f"{ws_url}/events?{query}") as ws:
                log.info("ARI connected to %s as app %s", ARI_URL, APP_NAME)
                delay = 1
                await _consume(ws)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("ARI disconnected (%s) — retrying in %ss", e, delay)
        await asyncio.sleep(delay)
        delay = min(delay * 2, 30)  # cap the backoff; never give up entirely
