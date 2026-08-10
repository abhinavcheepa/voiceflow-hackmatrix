"""WhatsApp via the local wa-bridge (WhatsApp Web).

The alternative to Meta's Cloud API: no business KYC, no approval wait — scan a
QR code with an ordinary WhatsApp account and it works. The bridge is a small
Node service in ../wa-bridge that drives WhatsApp Web; this module is just the
client for it.

⚠ Driving WhatsApp Web is against WhatsApp's Terms of Service and numbers do
  get banned. Use a dedicated number, never a personal or primary one.
"""

import base64
import os

import httpx

BRIDGE_URL = (os.getenv("WA_BRIDGE_URL") or "http://127.0.0.1:8100").rstrip("/")
TOKEN = os.getenv("WA_BRIDGE_TOKEN") or ""


def configured() -> bool:
    return bool(BRIDGE_URL)


def _headers() -> dict:
    return {"X-Bridge-Token": TOKEN} if TOKEN else {}


async def status() -> dict:
    """Link state: starting | qr | connected | disconnected | auth_failure."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{BRIDGE_URL}/status", headers=_headers())
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError as e:
        # A bridge that isn't running is a normal state, not an error worth
        # failing the dashboard over.
        return {"status": "offline", "error": str(e), "qr": None, "me": None}


async def send_text(to: str, body: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{BRIDGE_URL}/send/text", headers=_headers(), json={"to": to, "text": body}
        )
        r.raise_for_status()
        return r.json()


async def send_voice(to: str, audio: bytes, mime: str = "audio/mpeg") -> dict:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{BRIDGE_URL}/send/audio",
            headers=_headers(),
            json={
                "to": to,
                "audio_base64": base64.b64encode(audio).decode(),
                "mime": mime,
            },
        )
        r.raise_for_status()
        return r.json()


async def sync(chats: int = 20, messages: int = 10) -> list[dict]:
    """Recent one-to-one chats with their latest messages, newest chats first."""
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            f"{BRIDGE_URL}/sync", headers=_headers(), json={"chats": chats, "messages": messages}
        )
        r.raise_for_status()
        return r.json().get("threads", [])


async def logout() -> None:
    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(f"{BRIDGE_URL}/logout", headers=_headers())


def parse(payload: dict) -> dict:
    """One message from the bridge, in the shape whatsapp.handle() expects.

    The bridge sends audio inline as base64 — unlike Meta, which gives a media
    id that needs a second fetch — so there is no download step on this path.
    """
    audio_b64 = payload.get("audio_base64")
    return {
        "from": payload.get("from", ""),
        "name": payload.get("name"),
        "type": payload.get("type", "text"),
        "text": payload.get("text", ""),
        "audio": base64.b64decode(audio_b64) if audio_b64 else None,
    }
