"""Security and resource gate for browser web calls.

Enforces:
- Single-use ticket exchange (TTL 30s) via subprotocol or first frame (never URL)
- Origin allowlist validation
- Separate per-IP sliding-window rate limiting for ticket issue and WS connect
- Global concurrent session limits (guaranteed release in finally)
- Global and per-IP daily minute budgets to prevent resource exhaustion
- Hard max call duration limits and idle timeouts
- Frame size and frame rate safeguards
- IP and phone masking in logs
"""

import asyncio
import logging
import os
import secrets
import time
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("voiceflow.webcall.gate")

# Configuration via environment variables
WEBCALL_PUBLIC = os.getenv("WEBCALL_PUBLIC", "true").lower() in ("true", "1", "yes")
ALLOWED_ORIGINS_RAW = os.getenv("ALLOWED_ORIGINS", "")
ALLOWED_ORIGINS = [o.strip().rstrip("/") for o in ALLOWED_ORIGINS_RAW.split(",") if o.strip()]

# Demo day mode support: raises default rate limits and budgets for high-traffic live presentations
WEBCALL_DEMO_MODE = os.getenv("WEBCALL_DEMO_MODE", "false").lower() in ("true", "1", "yes")

_DEFAULT_RATE_LIMIT = 50 if WEBCALL_DEMO_MODE else 5
_DEFAULT_PER_IP_MINUTES = 120.0 if WEBCALL_DEMO_MODE else 30.0

RATE_LIMIT_PER_IP = int(os.getenv("WEBCALL_RATE_LIMIT_PER_IP") or _DEFAULT_RATE_LIMIT)
WS_CONNECT_RATE_LIMIT_PER_IP = int(os.getenv("WEBCALL_WS_CONNECT_RATE_LIMIT_PER_IP") or _DEFAULT_RATE_LIMIT)
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("WEBCALL_RATE_LIMIT_WINDOW_SECONDS") or 600)  # 10 min

MAX_CONCURRENT_WEBCALLS = int(os.getenv("MAX_CONCURRENT_WEBCALLS") or 10)
MAX_WEBCALL_DURATION_SECONDS = int(os.getenv("MAX_WEBCALL_DURATION_SECONDS") or 300)  # 5 min
WEBCALL_IDLE_TIMEOUT_SECONDS = int(os.getenv("WEBCALL_IDLE_TIMEOUT_SECONDS") or 60)
MAX_FRAME_SIZE_BYTES = int(os.getenv("MAX_FRAME_SIZE_BYTES") or 1_048_576)  # 1 MB
MAX_FRAME_RATE_PER_SEC = int(os.getenv("WEBCALL_MAX_FRAME_RATE_PER_SEC") or 25)

DAILY_WEBCALL_MINUTES_BUDGET = float(os.getenv("DAILY_WEBCALL_MINUTES_BUDGET") or 120.0)
PER_IP_DAILY_MINUTES = float(os.getenv("WEBCALL_PER_IP_DAILY_MINUTES") or _DEFAULT_PER_IP_MINUTES)

TRUSTED_PROXIES = {p.strip() for p in os.getenv("TRUSTED_PROXIES", "127.0.0.1,::1,localhost").split(",") if p.strip()}

# WebSocket Close Codes
WS_CLOSE_INVALID_TICKET = 4001
WS_CLOSE_BUDGET_EXCEEDED = 4002
WS_CLOSE_FORBIDDEN_ORIGIN = 4003
WS_CLOSE_RATE_LIMITED = 4008
WS_CLOSE_SERVER_BUSY = 4009
WS_CLOSE_DURATION_EXCEEDED = 4010
WS_CLOSE_FRAME_TOO_LARGE = 1009


def mask_ip(ip: str | None) -> str:
    """Mask IP for logs to protect user privacy (e.g. 192.168.***.***)."""
    if not ip:
        return "unknown"
    if "." in ip:
        parts = ip.split(".")
        if len(parts) == 4:
            return f"{parts[0]}.{parts[1]}.***.***"
    if ":" in ip:
        parts = ip.split(":")
        if len(parts) >= 2:
            return f"{parts[0]}:***:***:{parts[-1]}"
    return ip[:3] + "***"


def get_real_client_ip(request_or_ws: Any) -> str:
    """Extract client IP, verifying X-Forwarded-For only when the immediate peer is trusted."""
    client_host = request_or_ws.client.host if request_or_ws.client else "127.0.0.1"
    if client_host in TRUSTED_PROXIES:
        fwd = request_or_ws.headers.get("x-forwarded-for")
        if fwd:
            candidate = fwd.split(",")[0].strip()
            if candidate:
                return candidate
    return client_host


class WebCallGate:
    """Stateful gate manager for web call concurrency, tickets, rate limits and budgets."""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._tickets: dict[str, dict] = {}
        self._ip_history_ticket: dict[str, list[float]] = {}
        self._ip_history_ws: dict[str, list[float]] = {}
        self._active_calls: set[str] = set()
        self._daily_seconds_used: float = 0.0
        self._ip_daily_seconds_used: dict[str, float] = {}
        self._current_day: str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _reset_daily_budget_if_needed(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._current_day:
            self._current_day = today
            self._daily_seconds_used = 0.0
            self._ip_daily_seconds_used.clear()

    async def issue_ticket(self, client_ip: str, mode: str = "customer", agent_id: int | None = None) -> str:
        """Create a single-use ticket with 30s TTL."""
        async with self._lock:
            # Separate rate limit check on ticket issue
            if not self._check_rate_limit_locked(self._ip_history_ticket, client_ip, RATE_LIMIT_PER_IP):
                raise PermissionError("Rate limit exceeded for web call tickets")

            # Check daily budget
            self._reset_daily_budget_if_needed()
            if self._daily_seconds_used >= DAILY_WEBCALL_MINUTES_BUDGET * 60:
                raise ValueError("Daily global web call budget exhausted")
            if self._ip_daily_seconds_used.get(client_ip, 0.0) >= PER_IP_DAILY_MINUTES * 60:
                raise ValueError("Daily per-IP web call budget exhausted")

            now = time.time()
            expired = [t for t, data in self._tickets.items() if data["expires_at"] < now]
            for t in expired:
                del self._tickets[t]

            token = secrets.token_urlsafe(32)
            self._tickets[token] = {
                "ip": client_ip,
                "created_at": now,
                "expires_at": now + 30.0,
                "mode": mode,
                "agent_id": agent_id,
            }
            log.info("Issued web-call ticket for IP %s (expires in 30s)", mask_ip(client_ip))
            return token

    async def consume_ticket(self, token: str | None, client_ip: str) -> dict | None:
        """Atomically validate and consume ticket. Cannot be reused."""
        if not token:
            return None
        async with self._lock:
            now = time.time()
            data = self._tickets.pop(token, None)
            if not data:
                log.warning("Ticket rejected: unknown or already consumed from IP %s", mask_ip(client_ip))
                return None
            if now > data["expires_at"]:
                log.warning("Ticket rejected: expired from IP %s", mask_ip(client_ip))
                return None
            return data

    def check_origin(self, origin_header: str | None) -> bool:
        """Validate Origin header against configured allowlist."""
        if not ALLOWED_ORIGINS:
            return True
        if not origin_header:
            return False
        clean_origin = origin_header.strip().rstrip("/")
        return clean_origin in ALLOWED_ORIGINS

    def _check_rate_limit_locked(self, history: dict[str, list[float]], client_ip: str, limit: int) -> bool:
        now = time.time()
        window_start = now - RATE_LIMIT_WINDOW_SECONDS
        timestamps = [t for t in history.get(client_ip, []) if t > window_start]
        if len(timestamps) >= limit:
            log.warning("Rate limit tripped for IP %s (%d attempts in window)", mask_ip(client_ip), len(timestamps))
            return False
        timestamps.append(now)
        history[client_ip] = timestamps
        return True

    async def check_ticket_rate_limit(self, client_ip: str) -> bool:
        async with self._lock:
            return self._check_rate_limit_locked(self._ip_history_ticket, client_ip, RATE_LIMIT_PER_IP)

    async def check_ws_rate_limit(self, client_ip: str) -> bool:
        async with self._lock:
            return self._check_rate_limit_locked(self._ip_history_ws, client_ip, WS_CONNECT_RATE_LIMIT_PER_IP)

    async def acquire_slot(self, call_id: str, client_ip: str) -> bool:
        """Acquire a concurrent call slot."""
        async with self._lock:
            if len(self._active_calls) >= MAX_CONCURRENT_WEBCALLS:
                log.warning("Concurrent cap reached (%d active). Rejecting %s from %s",
                            len(self._active_calls), call_id, mask_ip(client_ip))
                return False
            self._active_calls.add(call_id)
            log.info("Call slot acquired: %s (%d/%d active)", call_id, len(self._active_calls), MAX_CONCURRENT_WEBCALLS)
            return True

    async def release_slot(self, call_id: str, client_ip: str = "", duration_sec: float = 0.0) -> None:
        """Release call slot and record consumed duration against global and per-IP budgets."""
        async with self._lock:
            self._active_calls.discard(call_id)
            self._reset_daily_budget_if_needed()
            dur = max(0.0, duration_sec)
            self._daily_seconds_used += dur
            if client_ip:
                self._ip_daily_seconds_used[client_ip] = self._ip_daily_seconds_used.get(client_ip, 0.0) + dur
            log.info("Call slot released: %s (%d/%d active, daily used: %.1fm/%.1fm)",
                     call_id, len(self._active_calls), MAX_CONCURRENT_WEBCALLS,
                     self._daily_seconds_used / 60, DAILY_WEBCALL_MINUTES_BUDGET)

    async def check_budget_ok(self, client_ip: str = "") -> bool:
        async with self._lock:
            self._reset_daily_budget_if_needed()
            if self._daily_seconds_used >= DAILY_WEBCALL_MINUTES_BUDGET * 60:
                return False
            if client_ip and self._ip_daily_seconds_used.get(client_ip, 0.0) >= PER_IP_DAILY_MINUTES * 60:
                return False
            return True

    @property
    def active_count(self) -> int:
        return len(self._active_calls)


# Singleton gate instance
gate = WebCallGate()
