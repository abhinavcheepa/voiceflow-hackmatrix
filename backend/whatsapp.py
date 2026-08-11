"""WhatsApp automation on the Meta Cloud API.

Flow for one incoming message:

    text  in ──► Groq reply ─────────────────────────► text out
    voice in ──► Whisper ──► Groq reply ──► ElevenJs ─► voice note out

A voice note gets a voice note back, in the owner's cloned voice. A text gets
text back, written in the owner's style. Both are logged to the dashboard.
"""

import asyncio
import logging
import os

import httpx

import brain
import db
import voice
import whatsapp_web

log = logging.getLogger("voiceflow.whatsapp")

GRAPH_VERSION = os.getenv("WHATSAPP_API_VERSION", "v21.0")
BASE_URL = f"https://graph.facebook.com/{GRAPH_VERSION}"
TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")

# "meta" = official Cloud API (needs business KYC).
# "web"  = the local wa-bridge driving WhatsApp Web (no KYC, against WhatsApp's
#          terms, ban risk — use a dedicated number).
PROVIDER = (os.getenv("WHATSAPP_PROVIDER") or "meta").lower()


def configured() -> bool:
    if PROVIDER == "web":
        return whatsapp_web.configured()
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
    """Send a text reply through whichever provider is active."""
    if PROVIDER == "web":
        return await whatsapp_web.send_text(to, body)
    return await _meta_send_text(to, body)


async def send_voice(to: str, audio: bytes) -> dict:
    if PROVIDER == "web":
        return await whatsapp_web.send_voice(to, audio, voice.OUTPUT_MIME)
    return await _meta_send_voice(to, audio)


async def _meta_send_text(to: str, body: str) -> dict:
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


async def _meta_send_voice(to: str, audio: bytes) -> dict:
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
            # The web bridge hands us the bytes directly; Meta gives a media id
            # that needs fetching first.
            audio = message.get("audio")
            if audio is None:
                if not message.get("media_id"):
                    return
                audio = await download_media(message["media_id"])
            incoming = await brain.transcribe(audio)
        else:
            incoming = message.get("text", "")

        if not incoming.strip():
            return

        language = brain.detect_language(incoming)

        # The message is recorded whatever happens next. Only the *reply* is
        # switchable — turning auto-reply off must never lose a customer's
        # message, just leave it for the owner to answer.
        #
        # needs_reply survives a crash or restart between here and the send,
        # which used to drop the reply with no trace at all.
        wants_reply = db.should_auto_reply(sender)
        message_id = db.add_message(
            sender, "them", "voice" if is_voice else "text", incoming,
            name=name, language=language, needs_reply=wants_reply,
        )

        if not wants_reply:
            log.info("auto-reply off for %s — message stored, not answered", sender)
            return

        await reply_to(sender, name, incoming, is_voice, language, message_id)

    except Exception as e:  # noqa: BLE001 - never let a webhook crash the worker
        # Logged on the thread so the owner sees the gap, but flagged so it
        # doesn't count as a reply in the response-rate metric. needs_reply stays
        # set, so the sweep retries it rather than leaving the customer hanging.
        log.warning("auto-reply failed for %s: %s", sender, describe(e))
        db.add_message(sender, "us", "text", f"[auto-reply failed: {describe(e)}]",
                       name=name, failed=True)


def describe(e: Exception) -> str:
    """Never produce an empty error string.

    httpx timeout exceptions stringify to "", which produced the useless
    "[auto-reply failed: ]" on the thread — an error that told nobody anything.
    """
    text = str(e).strip()
    return f"{type(e).__name__}: {text}" if text else type(e).__name__


async def reply_to(sender: str, name: str | None, incoming: str, is_voice: bool,
                   language: str, message_id: int | None = None) -> None:
    """Generate, send and record one reply.

    Separate from handle() so the startup catch-up can reuse it without storing
    the customer's message a second time.
    """
    # WhatsApp gives us the sender's profile name; pass it through so the
    # agent can greet them properly instead of guessing.
    answer = await brain.reply(sender, incoming, customer=name)

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

    # Answered — nothing left to pick up on the next startup.
    if message_id:
        db.clear_needs_reply(message_id)


SWEEP_SECONDS = int(os.getenv("REPLY_SWEEP_SECONDS") or 120)


async def answer_pending() -> None:
    """Reply to anything accepted but never answered.

    Two ways a reply goes missing: the process dies between "message stored" and
    "reply sent" (a deploy, a crash), or the reply throws — a provider timeout,
    a rate limit. Both leave needs_reply set, and both are picked up here.
    """
    pending = db.pending_replies()
    if not pending:
        return

    log.info("picking up %d unanswered message(s)", len(pending))
    for row in pending:
        try:
            await reply_to(
                row["phone"], row["name"], row["body"],
                row["kind"] == "voice", brain.detect_language(row["body"]), row["id"],
            )
        except Exception as e:  # noqa: BLE001 - one bad row must not stop the rest
            log.warning("catch-up failed for %s: %s", row["phone"], describe(e))
            # Left set on purpose — the next sweep tries again, and
            # pending_replies() ages rows out so this can't loop forever.


async def sweep_forever() -> None:
    """Retry unanswered messages on a timer, not only at startup.

    A provider timeout at 3am shouldn't wait for the next deploy to be retried.
    """
    while True:
        await asyncio.sleep(SWEEP_SECONDS)
        try:
            await answer_pending()
        except Exception as e:  # noqa: BLE001 - the sweep must never die
            log.warning("reply sweep failed: %s", describe(e))


def _estimate_seconds(text: str) -> int:
    """Voice-note length for the dashboard. ~2.5 words a second, floor of 1s."""
    return max(1, round(len(text.split()) / 2.5))
