"""Server-Sent Events for live dashboard updates.

Endpoints:
    POST /api/events/ticket  — returns a single-use 60s ticket (requires Bearer token)
    GET  /api/events         — the SSE stream (authenticated via ?ticket=...)

A heartbeat every 15 seconds keeps proxies from closing the connection.
The real API token NEVER appears in the EventSource URL or proxy access logs.

Usage from other modules:
    import events
    events.broadcast("call_update", {"id": "CL-abc", "status": "ended"})
"""

import asyncio
import hmac
import json
import logging
import secrets
import time
from typing import Any

from fastapi import HTTPException, Request
from starlette.responses import StreamingResponse

log = logging.getLogger("voiceflow.events")

# All currently-connected SSE clients.
_clients: set[asyncio.Queue] = set()

# In-memory single-use tickets: {ticket: expiry_timestamp}
_tickets: dict[str, float] = {}

HEARTBEAT_INTERVAL = 15  # seconds
TICKET_TTL = 60.0  # seconds


def _cleanup_tickets() -> None:
    now = time.time()
    expired = [t for t, exp in _tickets.items() if exp < now]
    for t in expired:
        _tickets.pop(t, None)


def create_ticket() -> str:
    """Generate a cryptographically secure 60s single-use ticket."""
    _cleanup_tickets()
    ticket = secrets.token_urlsafe(32)
    _tickets[ticket] = time.time() + TICKET_TTL
    return ticket


def verify_and_consume_ticket(ticket: str | None) -> bool:
    """Validate and consume a single-use ticket atomically."""
    if not ticket:
        return False
    _cleanup_tickets()
    matched_key = None
    for active_ticket in list(_tickets.keys()):
        if hmac.compare_digest(active_ticket, ticket):
            matched_key = active_ticket
            break
    if matched_key:
        _tickets.pop(matched_key, None)
        return True
    return False


def broadcast(event_type: str, data: Any = None) -> None:
    """Push an event to every connected client. Fire-and-forget, never blocks."""
    payload = json.dumps(data) if data is not None else "{}"
    message = f"event: {event_type}\ndata: {payload}\n\n"
    dead: list[asyncio.Queue] = []
    for q in _clients:
        try:
            q.put_nowait(message)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        _clients.discard(q)


async def _event_generator(request: Request, queue: asyncio.Queue):
    """Yield SSE messages until the client disconnects."""
    try:
        while True:
            if await request.is_disconnected():
                break
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_INTERVAL)
                yield msg
            except asyncio.TimeoutError:
                # Heartbeat — SSE comment line keeps proxies and browsers alive.
                yield f": heartbeat {int(time.time())}\n\n"
    finally:
        _clients.discard(queue)
        log.debug("SSE client disconnected (%d remaining)", len(_clients))


async def sse_endpoint(request: Request) -> StreamingResponse:
    """GET /api/events — the SSE stream.

    If an API_TOKEN is configured in backend, a valid single-use ticket is required.
    """
    import auth

    if auth.API_TOKEN:
        ticket = request.query_params.get("ticket")
        if not verify_and_consume_ticket(ticket):
            raise HTTPException(401, "Invalid or expired SSE ticket")

    queue: asyncio.Queue = asyncio.Queue(maxsize=256)
    _clients.add(queue)
    log.debug("SSE client connected (%d total)", len(_clients))

    # Send an initial connected event so the client knows it's working.
    await queue.put("event: connected\ndata: {}\n\n")

    return StreamingResponse(
        _event_generator(request, queue),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable Nginx/reverse proxy buffering
        },
    )


def client_count() -> int:
    return len(_clients)
