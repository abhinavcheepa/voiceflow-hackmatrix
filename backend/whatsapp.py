"""WhatsApp automation on the Meta Cloud API.

Flow for one incoming message:

    text  in ──► Groq reply ─────────────────────────► text out
    voice in ──► Whisper ──► Groq reply ──► ElevenJs ─► voice note out

A voice note gets a voice note back, in the owner's cloned voice. A text gets
text back, written in the owner's style. Both are logged to the dashboard.
"""

import os

import httpx

import brain
import db
import voice

GRAPH_VERSION = os.getenv("WHATSAPP_API_VERSION", "v21.0")
BASE_URL = f"https://graph.facebook.com/{GRAPH_VERSION}"
TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")


def configured() -> bool:
    return bool(TOKEN and PHONE_NUMBER_ID)


def _headers() -> dict:
    if not configured():
        raise RuntimeError(
            "WHATSAPP_TOKEN / WHATSAPP_PHONE_NUMBER_ID are not set — add them to backend/.env"
        )
    return {"Authorization": f"Bearer {TOKEN}"}


def verify(mode: str, token: str, challenge: str) -> str:
    """Meta's one-time webhook handshake. Must echo the challenge verbatim."""
    if mode == "subscribe" and token and token == VERIFY_TOKEN:
        return challenge
    raise PermissionError("verify token mismatch")


# --- outbound -----------------------------------------------------------

async def send_text(to: str, body: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{BASE_URL}/{PHONE_NUMBER_ID}/messages",
            headers={**_headers(), "Content-Type": "application/json"},
            json={
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                "text": {"body": body},
            },
        )
        r.raise_for_status()
        return r.json()


async def send_voice(to: str, audio: bytes) -> dict:
    """Upload the synthesised audio, then send it as a message."""
    async with httpx.AsyncClient(timeout=60) as client:
        up = await client.post(
            f"{BASE_URL}/{PHONE_NUMBER_ID}/media",
            headers=_headers(),
            data={"messaging_product": "whatsapp", "type": voice.OUTPUT_MIME},
            files={"file": (f"reply.{voice.OUTPUT_EXT}", audio, voice.OUTPUT_MIME)},
        )
        up.raise_for_status()
        media_id = up.json()["id"]

        r = await client.post(
            f"{BASE_URL}/{PHONE_NUMBER_ID}/messages",
            headers={**_headers(), "Content-Type": "application/json"},
            json={
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "audio",
                "audio": {"id": media_id},
            },
        )
        r.raise_for_status()
        return r.json()


async def download_media(media_id: str) -> bytes:
    """Two hops: media id -> signed URL -> bytes. The URL needs the token too."""
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        meta = await client.get(f"{BASE_URL}/{media_id}", headers=_headers())
        meta.raise_for_status()
        blob = await client.get(meta.json()["url"], headers=_headers())
        blob.raise_for_status()
        return blob.content


# --- inbound ------------------------------------------------------------

def parse(payload: dict) -> list[dict]:
    """Flatten Meta's nested webhook envelope into a list of messages.

    Delivery/read receipts arrive on the same webhook under `statuses` — those
    carry no `messages` key and fall out here, which is what we want.
    """
    out = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            names = {c["wa_id"]: c.get("profile", {}).get("name") for c in value.get("contacts", [])}
            for m in value.get("messages", []):
                out.append({
                    "from": m.get("from", ""),
                    "name": names.get(m.get("from", "")),
                    "type": m.get("type"),
                    "text": m.get("text", {}).get("body", ""),
                    "media_id": (m.get("audio") or m.get("voice") or {}).get("id"),
                })
    return out


async def handle(message: dict) -> None:
    """Process one inbound message end to end. Runs as a background task.

    Anything that fails is logged as a message on the thread rather than
    raised, so the owner sees the gap in the dashboard instead of silence.
    """
    sender = message["from"]
    name = message.get("name")
    is_voice = message["type"] in ("audio", "voice")

    try:
        if is_voice:
            if not message.get("media_id"):
                return
            audio = await download_media(message["media_id"])
            incoming = await brain.transcribe(audio)
        else:
            incoming = message.get("text", "")

        if not incoming.strip():
            return

        language = brain.detect_language(incoming)
        db.add_message(sender, "them", "voice" if is_voice else "text", incoming,
                       name=name, language=language)

        answer = await brain.reply(sender, incoming)

        if is_voice:
            audio_out = await voice.synthesize(answer, language)
            await send_voice(sender, audio_out)
            # Store the words, not the bytes — the dashboard shows the text
            # under the voice-note bubble.
            db.add_message(sender, "us", "voice", answer,
                           seconds=_estimate_seconds(answer), name=name, language=language)
        else:
            await send_text(sender, answer)
            db.add_message(sender, "us", "text", answer, name=name, language=language)

    except Exception as e:  # noqa: BLE001 - never let a webhook crash the worker
        # Logged on the thread so the owner sees the gap, but flagged so it
        # doesn't count as a reply in the response-rate metric.
        db.add_message(sender, "us", "text", f"[auto-reply failed: {e}]", name=name, failed=True)


def _estimate_seconds(text: str) -> int:
    """Voice-note length for the dashboard. ~2.5 words a second, floor of 1s."""
    return max(1, round(len(text.split()) / 2.5))
