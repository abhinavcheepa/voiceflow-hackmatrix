"""Tests for ISSUE-09: ASSISTANT_PIN authentication, rate limiting, session token management, and lockout protection."""

import asyncio
import hmac
import logging
import time
from unittest.mock import patch, AsyncMock

import pytest
from starlette.testclient import TestClient

import assistant_auth
import db
import main
from main import app


@pytest.fixture(autouse=True)
def setup_test_auth(monkeypatch, tmp_path):
    """Reset AssistantAuthManager state and initialize test DB between tests."""
    test_db = str(tmp_path / "test_assistant.db")
    monkeypatch.setattr(db, "DB_PATH", test_db)
    db.init()
    assistant_auth.auth_manager.reset()
    yield
    assistant_auth.auth_manager.reset()


def test_wrong_pin_rejected(monkeypatch):
    """Candidate PIN that does not match is rejected with generic error."""
    monkeypatch.setenv("ASSISTANT_PIN", "Secret987")
    success, token, retry_after, error_msg = assistant_auth.auth_manager.authenticate(
        "WrongPass123", ip="192.168.1.50"
    )
    assert success is False
    assert token is None
    assert error_msg == assistant_auth.GENERIC_ERROR_MESSAGE
    assert retry_after >= 1


def test_correct_pin_succeeds_and_issues_token(monkeypatch):
    """Correct candidate PIN succeeds, resets counters, and returns a session token."""
    monkeypatch.setenv("ASSISTANT_PIN", "Secret987")
    success, token, retry_after, error_msg = assistant_auth.auth_manager.authenticate(
        "Secret987", ip="192.168.1.50"
    )
    assert success is True
    assert token is not None
    assert len(token) >= 32
    assert retry_after == 0
    assert error_msg == ""
    # Verify session token is valid immediately
    assert assistant_auth.auth_manager.verify_session_token(token) is True


def test_unset_pin_refused(monkeypatch, caplog):
    """Unset or empty ASSISTANT_PIN refuses remote access and logs warning."""
    monkeypatch.delenv("ASSISTANT_PIN", raising=False)
    with caplog.at_level(logging.WARNING):
        is_startup_ok = assistant_auth.auth_manager.check_startup()
    assert is_startup_ok is False
    assert "ASSISTANT_PIN is not configured" in caplog.text

    success, token, retry_after, error_msg = assistant_auth.auth_manager.authenticate(
        "AnyCandidatePin", ip="192.168.1.50"
    )
    assert success is False
    assert token is None
    assert error_msg == assistant_auth.GENERIC_ERROR_MESSAGE


def test_default_pin_refused(monkeypatch, caplog):
    """Known default PINs (e.g. 123456, 000000, password, admin) are strictly refused."""
    for default_pin in ["123456", "000000", "password", "admin", "voiceflow"]:
        monkeypatch.setenv("ASSISTANT_PIN", default_pin)
        with caplog.at_level(logging.WARNING):
            is_ok = assistant_auth.auth_manager.check_startup()
        assert is_ok is False
        assert assistant_auth.auth_manager.is_pin_configured() is False

        success, token, _, _ = assistant_auth.auth_manager.authenticate(default_pin, ip="192.168.1.50")
        assert success is False
        assert token is None


def test_length_check_refuses_short_pin(monkeypatch, caplog):
    """Candidate or configured PIN < 6 characters is refused."""
    monkeypatch.setenv("ASSISTANT_PIN", "12345")  # 5 characters
    with caplog.at_level(logging.WARNING):
        is_ok = assistant_auth.auth_manager.check_startup()
    assert is_ok is False
    assert "too short" in caplog.text

    # Candidate shorter than 6 characters
    monkeypatch.setenv("ASSISTANT_PIN", "ValidLongPin999")
    success, token, _, _ = assistant_auth.auth_manager.authenticate("abc", ip="192.168.1.50")
    assert success is False
    assert token is None


def test_global_rate_limit_failed_attempts(monkeypatch):
    """Global cap on failed attempts per minute rejects attempts once exceeded."""
    monkeypatch.setenv("ASSISTANT_PIN", "CorrectPasscode99")
    # Send 30 failed attempts from 30 distinct IPs
    for i in range(assistant_auth.GLOBAL_MAX_FAILURES_PER_MINUTE):
        ok, _, _, _ = assistant_auth.auth_manager.authenticate("BadPin", ip=f"10.0.0.{i+1}")
        assert ok is False

    # 31st attempt from a new IP is blocked by the global failed attempt cap
    ok, token, retry_after, error_msg = assistant_auth.auth_manager.authenticate(
        "CorrectPasscode99", ip="10.0.1.99"
    )
    assert ok is False
    assert token is None
    assert error_msg == assistant_auth.GENERIC_ERROR_MESSAGE
    assert retry_after == 60


def test_lockout_after_n_failures(monkeypatch):
    """After 5 failed attempts from the same IP or identity, client is locked out."""
    monkeypatch.setenv("ASSISTANT_PIN", "CorrectPasscode99")
    ip = "192.168.1.88"

    # First 4 failures do not lock out
    for i in range(4):
        ok, token, retry_after, _ = assistant_auth.auth_manager.authenticate("BadPin123", ip=ip)
        assert ok is False
        assert token is None

    # 5th failure triggers lockout
    ok, token, retry_after, error_msg = assistant_auth.auth_manager.authenticate("BadPin123", ip=ip)
    assert ok is False
    assert retry_after >= assistant_auth.BASE_LOCKOUT_SECONDS

    # 6th attempt even with correct PIN is blocked due to active lockout
    ok, token, retry_after, error_msg = assistant_auth.auth_manager.authenticate("CorrectPasscode99", ip=ip)
    assert ok is False
    assert token is None
    assert error_msg == assistant_auth.GENERIC_ERROR_MESSAGE
    assert retry_after >= 1


def test_lockout_expiry(monkeypatch):
    """Once lockout duration elapses, client can authenticate again."""
    monkeypatch.setenv("ASSISTANT_PIN", "CorrectPasscode99")
    ip = "192.168.1.89"

    # Trigger lockout
    for _ in range(5):
        assistant_auth.auth_manager.authenticate("BadPin123", ip=ip)

    # Simulate time jump past lockout duration
    now = time.time()
    with patch("time.time", return_value=now + assistant_auth.BASE_LOCKOUT_SECONDS + 1):
        ok, token, retry_after, _ = assistant_auth.auth_manager.authenticate("CorrectPasscode99", ip=ip)
        assert ok is True
        assert token is not None


def test_exponential_backoff(monkeypatch):
    """Repeated lockouts exponentially increase lockout duration up to MAX_LOCKOUT_SECONDS."""
    monkeypatch.setenv("ASSISTANT_PIN", "CorrectPasscode99")
    ip = "192.168.1.90"

    # Lockout 1: BASE_LOCKOUT_SECONDS (30s)
    for _ in range(5):
        assistant_auth.auth_manager.authenticate("BadPin123", ip=ip)
    until1 = assistant_auth.auth_manager._ip_lockouts[ip][0]

    # Fast-forward past lockout 1, trigger 2nd lockout
    with patch("time.time", return_value=until1 + 1):
        for _ in range(5):
            assistant_auth.auth_manager.authenticate("BadPin123", ip=ip)
        until2 = assistant_auth.auth_manager._ip_lockouts[ip][0]
        # Duration should double: 30 * 2 = 60s
        assert int(until2 - (until1 + 1)) == 60


def test_generic_error_and_retry_after_endpoint(monkeypatch):
    """HTTP endpoint returns generic error and Retry-After header for wrong PIN and locked out."""
    monkeypatch.setenv("ASSISTANT_PIN", "ValidPass999")
    client = TestClient(app)

    # 1. Wrong PIN
    res1 = client.post("/api/assistant/auth", json={"pin": "WrongPass123"})
    assert res1.status_code == 401
    assert res1.json()["detail"] == assistant_auth.GENERIC_ERROR_MESSAGE
    assert "Retry-After" in res1.headers

    # 2. Lock client out
    for _ in range(4):
        client.post("/api/assistant/auth", json={"pin": "WrongPass123"})

    # 3. Locked out request returns identical error message
    res_locked = client.post("/api/assistant/auth", json={"pin": "ValidPass999"})
    assert res_locked.status_code == 401
    assert res_locked.json()["detail"] == assistant_auth.GENERIC_ERROR_MESSAGE
    assert int(res_locked.headers["Retry-After"]) >= assistant_auth.BASE_LOCKOUT_SECONDS


def test_timing_compare_digest_used(monkeypatch):
    """Verifies that hmac.compare_digest is strictly invoked on bytes and not python equality ==."""
    monkeypatch.setenv("ASSISTANT_PIN", "SecureCode888")
    with patch("hmac.compare_digest", wraps=hmac.compare_digest) as spy:
        assistant_auth.verify_pin_constant_time("SecureCode888", "SecureCode888")
        assert spy.called, "hmac.compare_digest must be called for constant-time PIN comparison"
        args, _ = spy.call_args
        assert isinstance(args[0], (bytes, bytearray)), "First argument must be bytes"
        assert isinstance(args[1], (bytes, bytearray)), "Second argument must be bytes"


def test_pin_and_token_never_in_logs(monkeypatch, caplog):
    """Raw PIN and session token values never appear anywhere in application log output."""
    raw_pin = "SuperSecretPin999"
    monkeypatch.setenv("ASSISTANT_PIN", raw_pin)

    with caplog.at_level(logging.DEBUG):
        client = TestClient(app)
        # Attempt failed login with a specific sensitive candidate
        candidate = "SensitiveCandidateString123"
        client.post("/api/assistant/auth", json={"pin": candidate})
        # Attempt successful login
        res = client.post("/api/assistant/auth", json={"pin": raw_pin})
        token = res.json()["token"]

    log_content = caplog.text
    assert raw_pin not in log_content, "Configured PIN must never appear in logs"
    assert candidate not in log_content, "Candidate PIN must never appear in logs"
    assert token not in log_content, "Session token must never appear in logs"


def test_token_expiry(monkeypatch):
    """Session tokens expire after SESSION_TTL_SECONDS and are invalidated."""
    monkeypatch.setenv("ASSISTANT_PIN", "ValidPasscode123")
    ok, token, _, _ = assistant_auth.auth_manager.authenticate("ValidPasscode123", ip="192.168.1.95")
    assert ok is True
    assert assistant_auth.auth_manager.verify_session_token(token) is True

    # Advance time beyond TTL
    now = time.time()
    with patch("time.time", return_value=now + assistant_auth.SESSION_TTL_SECONDS + 10):
        assert assistant_auth.auth_manager.verify_session_token(token) is False


def test_owner_tools_unreachable_without_valid_token(monkeypatch):
    """Remote WebSocket request without valid session token or PIN cannot access owner assistant."""
    monkeypatch.setenv("ASSISTANT_PIN", "ValidPasscode123")
    client = TestClient(app)

    # 1. Remote connection with wrong PIN is rejected
    t_res = client.get("/api/web-call/ticket?mode=assistant")
    ticket = t_res.json()["ticket"]
    with client.websocket_connect(
        "/api/web-call/ws?mode=assistant",
        subprotocols=[f"vf-ticket.{ticket}"],
        headers={"x-forwarded-for": "198.51.100.5"}
    ) as ws:
        ws.send_json({"type": "auth", "pin": "WrongPass123"})
        res = ws.receive_json()
        assert res.get("type") == "error"
        assert res.get("text") == assistant_auth.GENERIC_ERROR_MESSAGE

    # 2. Remote connection with valid session token succeeds
    t_res2 = client.get("/api/web-call/ticket?mode=assistant")
    ticket2 = t_res2.json()["ticket"]
    token = assistant_auth.auth_manager.issue_session_token(ip="198.51.100.5")
    with patch("webcall.speak", new_callable=AsyncMock):
        with client.websocket_connect(
            "/api/web-call/ws?mode=assistant",
            subprotocols=[f"vf-ticket.{ticket2}"],
            headers={"x-forwarded-for": "198.51.100.5"}
        ) as ws:
            ws.send_json({"type": "auth", "token": token})
            # Connection accepted and session opened
            conn_msg = ws.receive_json()
            assert conn_msg.get("type") == "connected"

