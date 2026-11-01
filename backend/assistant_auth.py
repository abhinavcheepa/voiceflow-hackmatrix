"""Assistant PIN authentication, rate limiting, session token management, and lockout protection.

Implements ISSUE-09:
- Constant-time comparison using hmac.compare_digest on bytes (never == or in)
- Minimum PIN length (>= 6) and whitespace normalization
- Refusal of unset or known default PINs (never ships a default PIN)
- Startup validation logging warning if PIN is insecure or unset
- Per-IP and per-client-identity rate limiting with exponential backoff & lockout
- Global cap on failed attempts per minute
- Generic error for wrong PIN, locked out, and rate-limited, with Retry-After header
- Short-lived random session tokens (not PIN) for authenticated requests
- Zero PIN or token leakage in logs or URLs; masked IPs and phone numbers in logs
"""

import hmac
import logging
import os
import secrets
import time
from typing import Optional, Tuple, Dict, List, Set, Any

log = logging.getLogger("voiceflow.assistant.auth")

MIN_PIN_LENGTH = 8
SESSION_TTL_SECONDS = 3600  # 1 hour
MAX_FAILURES_BEFORE_LOCKOUT = 5
BASE_LOCKOUT_SECONDS = 30
MAX_LOCKOUT_SECONDS = 900
GLOBAL_MAX_FAILURES_PER_MINUTE = 30

KNOWN_DEFAULTS = {
    "123456",
    "000000",
    "111111",
    "12345678",
    "00000000",
    "11111111",
    "654321",
    "87654321",
    "password",
    "admin",
    "default",
    "voiceflow",
    "012345",
}

GENERIC_ERROR_MESSAGE = "Invalid credentials or too many failed attempts."


def mask_ip(ip: Optional[str]) -> str:
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


def mask_identifier(ident: Optional[str]) -> str:
    """Mask client identity (e.g. phone number) for logs."""
    if not ident:
        return "unknown"
    ident = ident.strip()
    if len(ident) <= 6:
        return "****"
    return ident[:2] + "****" + ident[-4:]


def verify_pin_constant_time(candidate: Optional[str], configured: Optional[str]) -> bool:
    """Compare candidate PIN against configured PIN in constant time using hmac.compare_digest on bytes."""
    candidate_str = (candidate or "").strip()
    configured_str = (configured or "").strip()

    candidate_bytes = candidate_str.encode("utf-8")
    configured_bytes = configured_str.encode("utf-8")

    # If length constraint fails or either is empty, still execute hmac.compare_digest on dummy
    # bytes to prevent timing side channels while strictly rejecting the attempt.
    if not candidate_bytes or not configured_bytes or len(candidate_str) < MIN_PIN_LENGTH:
        hmac.compare_digest(b"dummy_timing_pad_12345", b"dummy_timing_pad_67890")
        return False

    return hmac.compare_digest(candidate_bytes, configured_bytes)


class AssistantAuthManager:
    """Stateful assistant authentication manager for PIN verification, lockouts, and session tokens."""

    def __init__(self):
        # Failed attempt timestamps: key -> list of float timestamps
        self._failed_ip_attempts: Dict[str, List[float]] = {}
        self._failed_identity_attempts: Dict[str, List[float]] = {}
        self._global_failures: List[float] = []

        # Lockout state: key -> (lockout_until_timestamp, consecutive_lockouts_count)
        self._ip_lockouts: Dict[str, Tuple[float, int]] = {}
        self._identity_lockouts: Dict[str, Tuple[float, int]] = {}
        self._ip_lockout_counts: Dict[str, int] = {}
        self._identity_lockout_counts: Dict[str, int] = {}

        # Active sessions: token -> dict(created_at, expires_at, ip, identity)
        self._sessions: Dict[str, Dict[str, Any]] = {}

    def reset(self) -> None:
        """Clear all state (primarily for test isolation)."""
        self._failed_ip_attempts.clear()
        self._failed_identity_attempts.clear()
        self._global_failures.clear()
        self._ip_lockouts.clear()
        self._identity_lockouts.clear()
        self._ip_lockout_counts.clear()
        self._identity_lockout_counts.clear()
        self._sessions.clear()

    def get_configured_pin(self) -> Optional[str]:
        """Read and validate the configured ASSISTANT_PIN from environment.
        
        Returns the stripped PIN if valid, or None if unset, too short (<6), or set to a known default.
        """
        raw = os.getenv("ASSISTANT_PIN", "").strip()
        if not raw:
            return None
        if len(raw) < MIN_PIN_LENGTH:
            return None
        if raw.lower() in KNOWN_DEFAULTS:
            return None
        return raw

    def is_pin_configured(self) -> bool:
        """Return True only if a valid, secure ASSISTANT_PIN is configured."""
        return self.get_configured_pin() is not None

    def check_startup(self) -> bool:
        """Perform startup security check on ASSISTANT_PIN and log appropriate warnings."""
        raw = os.getenv("ASSISTANT_PIN", "").strip()
        if not raw:
            log.warning("ASSISTANT_PIN is not configured. Remote owner assistant features will be DISABLED.")
            return False
        if len(raw) < MIN_PIN_LENGTH:
            log.warning(
                "ASSISTANT_PIN is too short (%d chars, minimum %d required). Remote owner assistant features will be DISABLED.",
                len(raw),
                MIN_PIN_LENGTH,
            )
            return False
        if raw.lower() in KNOWN_DEFAULTS:
            log.warning(
                "ASSISTANT_PIN is set to an insecure known default. Remote owner assistant features will be DISABLED."
            )
            return False
        log.info("ASSISTANT_PIN is securely configured (length %d).", len(raw))
        return True

    def authenticate(
        self,
        candidate_pin: Optional[str],
        ip: str = "127.0.0.1",
        identity: Optional[str] = None,
    ) -> Tuple[bool, Optional[str], int, str]:
        """Authenticate a candidate PIN under strict rate limiting and lockout rules.
        
        Returns:
            (success: bool, session_token: Optional[str], retry_after: int, error_message: str)
        """
        now = time.time()

        # 1. Check IP lockout
        if ip in self._ip_lockouts:
            until, count = self._ip_lockouts[ip]
            if now < until:
                retry_after = max(1, int(until - now + 0.99))
                log.warning("Assistant PIN auth rejected: IP %s is locked out (%ds remaining)", mask_ip(ip), retry_after)
                return False, None, retry_after, GENERIC_ERROR_MESSAGE
            else:
                del self._ip_lockouts[ip]

        # 2. Check client identity lockout
        if identity and identity in self._identity_lockouts:
            until, count = self._identity_lockouts[identity]
            if now < until:
                retry_after = max(1, int(until - now + 0.99))
                log.warning(
                    "Assistant PIN auth rejected: identity %s is locked out (%ds remaining)",
                    mask_identifier(identity),
                    retry_after,
                )
                return False, None, retry_after, GENERIC_ERROR_MESSAGE
            else:
                del self._identity_lockouts[identity]

        # 3. Verify PIN constant time
        configured = self.get_configured_pin()
        match = verify_pin_constant_time(candidate_pin, configured)

        if not match:
            # 4. Check global rate limit (failed attempts in past 60s)
            self._global_failures = [t for t in self._global_failures if now - t <= 60.0]
            if len(self._global_failures) >= GLOBAL_MAX_FAILURES_PER_MINUTE:
                log.warning("Assistant PIN global failed attempt cap reached (%d/min); throttling failed attempts", len(self._global_failures))
                return False, None, 60, GENERIC_ERROR_MESSAGE

            # Record failed attempt
            self._global_failures.append(now)

            # Record IP failures (sliding window of 15 min)
            ip_attempts = [t for t in self._failed_ip_attempts.get(ip, []) if now - t <= 900.0]
            ip_attempts.append(now)
            self._failed_ip_attempts[ip] = ip_attempts

            # Record Identity failures if provided
            if identity:
                ident_attempts = [t for t in self._failed_identity_attempts.get(identity, []) if now - t <= 900.0]
                ident_attempts.append(now)
                self._failed_identity_attempts[identity] = ident_attempts

            ip_fail_count = len(ip_attempts)
            ident_fail_count = len(self._failed_identity_attempts.get(identity, [])) if identity else 0
            is_locked = (ip_fail_count >= MAX_FAILURES_BEFORE_LOCKOUT) or (ident_fail_count >= MAX_FAILURES_BEFORE_LOCKOUT)
            fail_count = max(ip_fail_count, ident_fail_count)

            if is_locked:
                prev_ip_lockouts = self._ip_lockout_counts.get(ip, 0)
                prev_ident_lockouts = self._identity_lockout_counts.get(identity, 0) if identity else 0
                new_lockout_count = max(prev_ip_lockouts, prev_ident_lockouts) + 1
                self._ip_lockout_counts[ip] = new_lockout_count
                lockout_duration = min(
                    BASE_LOCKOUT_SECONDS * (2 ** (new_lockout_count - 1)),
                    MAX_LOCKOUT_SECONDS,
                )
                self._ip_lockouts[ip] = (now + lockout_duration, new_lockout_count)
                if identity:
                    self._identity_lockout_counts[identity] = new_lockout_count
                    self._identity_lockouts[identity] = (now + lockout_duration, new_lockout_count)
                retry_after = int(lockout_duration)
                log.warning(
                    "Assistant PIN auth locked out for IP %s and identity %s for %ds (attempt %d)",
                    mask_ip(ip),
                    mask_identifier(identity),
                    lockout_duration,
                    fail_count,
                )
            else:
                retry_after = min(2 ** (fail_count - 1), 10)
                log.warning(
                    "Assistant PIN auth failed for IP %s (attempt %d/%d)",
                    mask_ip(ip),
                    fail_count,
                    MAX_FAILURES_BEFORE_LOCKOUT,
                )

            return False, None, retry_after, GENERIC_ERROR_MESSAGE

        # Successful authentication: reset failed counters and lockouts for this IP & identity
        self._failed_ip_attempts.pop(ip, None)
        self._ip_lockouts.pop(ip, None)
        self._ip_lockout_counts.pop(ip, None)
        if identity:
            self._failed_identity_attempts.pop(identity, None)
            self._identity_lockouts.pop(identity, None)
            self._identity_lockout_counts.pop(identity, None)

        session_token = secrets.token_urlsafe(32)
        self._sessions[session_token] = {
            "created_at": now,
            "expires_at": now + SESSION_TTL_SECONDS,
            "ip": ip,
            "identity": identity,
        }
        log.info("Assistant PIN verified successfully for IP %s; issued session token", mask_ip(ip))
        return True, session_token, 0, ""

    def issue_session_token(self, ip: str = "127.0.0.1", identity: Optional[str] = None) -> str:
        """Directly issue a session token (e.g. for loopback caller or authenticated admin)."""
        now = time.time()
        session_token = secrets.token_urlsafe(32)
        self._sessions[session_token] = {
            "created_at": now,
            "expires_at": now + SESSION_TTL_SECONDS,
            "ip": ip,
            "identity": identity,
        }
        return session_token

    def verify_session_token(self, token: Optional[str]) -> bool:
        """Verify whether a session token is present and unexpired."""
        if not token or not isinstance(token, str):
            return False
        clean_token = token.strip()
        session = self._sessions.get(clean_token)
        if not session:
            return False
        if time.time() > session["expires_at"]:
            self._sessions.pop(clean_token, None)
            return False
        return True

    def get_session_remaining_ttl(self, token: Optional[str]) -> int:
        """Get remaining TTL in seconds for a valid session token, or 0 if expired/invalid."""
        if not token or not isinstance(token, str):
            return 0
        clean_token = token.strip()
        session = self._sessions.get(clean_token)
        if not session:
            return 0
        rem = session["expires_at"] - time.time()
        return max(0, int(rem)) if rem > 0 else 0

    def revoke_session_token(self, token: Optional[str]) -> bool:
        """Revoke a session token upon logout."""
        if not token:
            return False
        clean_token = token.strip()
        return self._sessions.pop(clean_token, None) is not None


# Global singleton instance
auth_manager = AssistantAuthManager()
