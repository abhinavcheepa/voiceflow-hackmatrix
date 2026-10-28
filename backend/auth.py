"""API token authentication.

Every /api/* route requires a Bearer token matching API_TOKEN from the env.
Exceptions:
  - /health                  — monitoring, always open
  - /api/events              — uses one-time ticket (?ticket=) generated via POST /api/events/ticket
  - /api/vapi/webhook        — secured by X-Vapi-Secret
  - /api/whatsapp/webhook    — Meta verification token / HMAC signature
  - /api/whatsapp/web/incoming — secured by X-Bridge-Token / BRIDGE_SECRET

The token is intentionally a static secret, not a session: there is one owner,
one dashboard, and no user accounts. JWT would add complexity for no gain.
"""

import hmac
import logging
import os

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

log = logging.getLogger("voiceflow.auth")

API_TOKEN = os.getenv("API_TOKEN", "").strip()
ENV = os.getenv("ENV", "development").strip().lower()
IS_PROD = ENV in ("production", "prod")

if IS_PROD and not API_TOKEN:
    log.warning("CRITICAL: ENV=production but API_TOKEN is empty! All protected routes will reject requests.")

# Paths that skip Bearer token verification because they either:
# 1. Are public health checks
# 2. Authenticate using their own webhook secret / one-time ticket
_OPEN_PATHS = frozenset({
    "/health",
    "/api/events",                # Verified via one-time ticket inside events.sse_endpoint
    "/api/vapi/webhook",          # Verified via X-Vapi-Secret
    "/api/whatsapp/webhook",      # Verified via Meta verify token / signature
    "/api/whatsapp/web/incoming", # Verified via X-Bridge-Token
})

_OPEN_PREFIXES: tuple[str, ...] = ()


def _is_open(path: str) -> bool:
    if path in _OPEN_PATHS:
        return True
    for prefix in _OPEN_PREFIXES:
        if path.startswith(prefix):
            return True
    return False


def _extract_token(request: Request) -> str | None:
    """Bearer header only — query strings are disallowed to avoid leaking tokens in logs."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


class TokenAuthMiddleware(BaseHTTPMiddleware):
    """Reject requests to protected paths that lack a valid API_TOKEN."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path.rstrip("/")

        # Open paths pass through to their respective endpoint security checks
        if _is_open(path):
            return await call_next(request)

        # In production without an API_TOKEN, reject all protected API routes
        if IS_PROD and not API_TOKEN and path.startswith("/api"):
            return JSONResponse(
                {"detail": "Production server requires API_TOKEN to be configured"},
                status_code=401,
            )

        # In development without an API_TOKEN, allow open access for convenience
        if not API_TOKEN:
            return await call_next(request)

        # Everything under /api/* requires the valid Bearer token
        if path.startswith("/api"):
            token = _extract_token(request)
            if not token or not hmac.compare_digest(token, API_TOKEN):
                return JSONResponse(
                    {"detail": "Missing or invalid API token"},
                    status_code=401,
                )

        return await call_next(request)
