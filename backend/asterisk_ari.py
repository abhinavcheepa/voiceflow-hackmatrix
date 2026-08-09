"""Asterisk ARI adapter — Phases 1-5.

Asterisk's dialplan hands a channel to Stasis(voiceflow-ai); this module picks
it up over the ARI WebSocket, answers, logs the call, and plays the greeting.

Scope note: the realtime audio loop (ExternalMedia RTP, streaming STT, barge-in)
is Phases 6-11 and is NOT here. Today the conversation still runs on Vapi — set
VAPI_SIP_URI and the answered channel is bridged out to it. Without that the
channel gets the greeting and hangs up, which is exactly what you want while
proving the plumbing works.

Runs only when ASTERISK_ARI_URL is set, so a machine without Asterisk starts
normally.
"""

import asyncio
import base64
import json
import logging
import os
from datetime import datetime, timezone
from urllib.parse import quote, urlencode

import httpx

import db

log = logging.getLogger("voiceflow.ari")

ARI_URL = os.getenv("ASTERISK_ARI_URL", "").rstrip("/")
ARI_USER = os.getenv("ASTERISK_ARI_USERNAME", "voiceflow")
ARI_PASS = os.getenv("ASTERISK_ARI_PASSWORD", "")
APP_NAME = os.getenv("ASTERISK_ARI_APP", "voiceflow-ai")

# Stock Asterisk sound, no file needed. Swap for "sound:voiceflow-greeting"
# once you drop a generated clip into Asterisk's sounds directory.
GREETING_MEDIA = os.getenv("ASTERISK_GREETING", "sound:hello-world")

# Where to send the audio leg for the actual conversation. Empty = Phase 1-5
# mode: greet and hang up.
VAPI_SIP_URI = os.getenv("VAPI_SIP_URI", "")

MAX_CALL_SECONDS = int(os.getenv("MAX_CALL_SECONDS", "1800"))


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


async def bridge_to_vapi(channel_id: str) -> None:
    """Dial the Vapi SIP endpoint and bridge both legs together.

    This is the Path 1 architecture: Asterisk owns the number and the call
    record, Vapi runs the conversation.
    """
    bridge = await _rest("POST", "/bridges", type="mixing")
    if not bridge:
        return
    outbound = await _rest(
        "POST", "/channels", endpoint=VAPI_SIP_URI, app=APP_NAME, appArgs="vapi-leg"
    )
    if not outbound:
        return
    await _rest(
        "POST",
        f"/bridges/{bridge['id']}/addChannel",
        channel=f"{channel_id},{outbound['id']}",
    )


# --- event handling -----------------------------------------------------

async def _on_stasis_start(event: dict) -> None:
    channel = event.get("channel", {})
    channel_id = channel.get("id", "")
    caller = channel.get("caller", {}).get("number") or "unknown"
    args = event.get("args", [])

    # The outbound leg we created ourselves re-enters Stasis; don't recurse.
    if "vapi-leg" in args:
        return

    log.info("call started: %s from %s", channel_id, caller)
    db.upsert_call(
        channel_id,
        phone=caller,
        status="active",
        started_at=datetime.now(timezone.utc).isoformat(),
    )

    await answer(channel_id)
    await play(channel_id, GREETING_MEDIA)

    if VAPI_SIP_URI:
        await bridge_to_vapi(channel_id)
    else:
        # Phase 1-5: prove the path, then release the channel rather than
        # leaving it open with nothing driving it.
        await asyncio.sleep(6)
        await hangup(channel_id)


async def _on_stasis_end(event: dict) -> None:
    channel_id = event.get("channel", {}).get("id", "")
    log.info("call ended: %s", channel_id)
    db.upsert_call(channel_id, status="ended")


HANDLERS = {
    "StasisStart": _on_stasis_start,
    "StasisEnd": _on_stasis_end,
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
