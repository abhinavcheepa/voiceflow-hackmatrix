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
    monkeypatch.setenv("ASSISTANT_PIN", "SecretPasscode987")
    success, token, retry_after, error_msg = assistant_auth.auth_manager.authenticate(
        "WrongPass123", ip="192.168.1.50"
    )
    assert success is False
    assert token is None
    assert error_msg == assistant_auth.GENERIC_ERROR_MESSAGE
    assert retry_after >= 1


def test_correct_pin_succeeds_and_issues_token(monkeypatch):
    """Correct candidate PIN succeeds, resets counters, and returns a session token."""
    monkeypatch.setenv("ASSISTANT_PIN", "SecretPasscode987")
    success, token, retry_after, error_msg = assistant_auth.auth_manager.authenticate(
        "SecretPasscode987", ip="192.168.1.50"
    )
    assert success is True
    assert token is not None
    assert len(token) >= 32
    assert retry_after == 0
    assert error_msg == ""
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
    """Known default PINs (e.g. 12345678, password, admin) are strictly refused."""
    for default_pin in ["12345678", "00000000", "password", "admin", "voiceflow"]:
        monkeypatch.setenv("ASSISTANT_PIN", default_pin)
        with caplog.at_level(logging.WARNING):
            is_ok = assistant_auth.auth_manager.check_startup()
        assert is_ok is False
        assert assistant_auth.auth_manager.is_pin_configured() is False

        success, token, _, _ = assistant_auth.auth_manager.authenticate(default_pin, ip="192.168.1.50")
        assert success is False
        assert token is None


def test_length_check_refuses_short_pin(monkeypatch, caplog):
    """Candidate or configured PIN < 8 characters is refused."""
    monkeypatch.setenv("ASSISTANT_PIN", "1234567")  # 7 characters (< 8)
    with caplog.at_level(logging.WARNING):
        is_ok = assistant_auth.auth_manager.check_startup()
    assert is_ok is False
    assert "too short" in caplog.text

    # Candidate shorter than 8 characters
    monkeypatch.setenv("ASSISTANT_PIN", "ValidLongPin999")
    success, token, _, _ = assistant_auth.auth_manager.authenticate("abc1234", ip="192.168.1.50")
    assert success is False
    assert token is None


def test_global_rate_limit_throttles_failed_attempts_only(monkeypatch):
    """Global cap on failed attempts throttles attackers but NEVER locks out owner with correct PIN."""
    monkeypatch.setenv("ASSISTANT_PIN", "OwnerSecurePass2026")
    # Send 30 failed attempts from 30 distinct attacker IPs
    for i in range(assistant_auth.GLOBAL_MAX_FAILURES_PER_MINUTE):
        ok, _, _, _ = assistant_auth.auth_manager.authenticate("BadAttackerPin", ip=f"10.0.0.{i+1}")
        assert ok is False

    # 31st attacker attempt from a new IP is throttled
    ok_att, token_att, retry_att, _ = assistant_auth.auth_manager.authenticate(
        "BadAttackerPin", ip="10.0.1.99"
    )
    assert ok_att is False
    assert retry_att == 60

    # BUT the legitimate owner presenting the CORRECT PIN still successfully authenticates!
    ok_owner, token_owner, retry_owner, _ = assistant_auth.auth_manager.authenticate(
        "OwnerSecurePass2026", ip="192.168.1.100"
    )
    assert ok_owner is True
    assert token_owner is not None
    assert retry_owner == 0


def test_lockout_after_n_failures_per_ip(monkeypatch):
    """After 5 failed attempts from the same IP, that IP is locked out."""
    monkeypatch.setenv("ASSISTANT_PIN", "CorrectPasscode99")
    ip = "192.168.1.88"

    for _ in range(4):
        ok, token, _, _ = assistant_auth.auth_manager.authenticate("BadPin123", ip=ip)
        assert ok is False

    # 5th failure triggers lockout
    ok, token, retry_after, _ = assistant_auth.auth_manager.authenticate("BadPin123", ip=ip)
    assert ok is False
    assert retry_after >= assistant_auth.BASE_LOCKOUT_SECONDS

    # 6th attempt even with correct PIN is blocked due to active IP lockout
    ok, token, retry_after, error_msg = assistant_auth.auth_manager.authenticate("CorrectPasscode99", ip=ip)
    assert ok is False
    assert token is None
    assert error_msg == assistant_auth.GENERIC_ERROR_MESSAGE
    assert retry_after >= 1


def test_lockout_after_n_failures_per_identity(monkeypatch):
    """After 5 failed attempts for the same client identity (e.g. WhatsApp number), identity is locked out."""
    monkeypatch.setenv("ASSISTANT_PIN", "CorrectPasscode99")
    identity = "919999900001"

    # Send 5 failures from changing IPs but same identity
    for i in range(5):
        ok, _, retry_after, _ = assistant_auth.auth_manager.authenticate(
            "BadPin123", ip=f"192.168.2.{i+1}", identity=identity
        )
        assert ok is False

    assert retry_after >= assistant_auth.BASE_LOCKOUT_SECONDS

    # 6th attempt from yet another IP for this locked out identity is blocked
    ok, token, retry_after, _ = assistant_auth.auth_manager.authenticate(
        "CorrectPasscode99", ip="192.168.2.99", identity=identity
    )
    assert ok is False
    assert token is None
    assert retry_after >= 1


def test_lockout_expiry(monkeypatch):
    """Once lockout duration elapses, client can authenticate again."""
    monkeypatch.setenv("ASSISTANT_PIN", "CorrectPasscode99")
    ip = "192.168.1.89"

    for _ in range(5):
        assistant_auth.auth_manager.authenticate("BadPin123", ip=ip)

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

    with patch("time.time", return_value=until1 + 1):
        for _ in range(5):
            assistant_auth.auth_manager.authenticate("BadPin123", ip=ip)
        until2 = assistant_auth.auth_manager._ip_lockouts[ip][0]
        # Duration doubles: 30 * 2 = 60s
        assert int(until2 - (until1 + 1)) == 60


def test_generic_error_and_retry_after_endpoint(monkeypatch):
    """HTTP endpoint returns generic error and Retry-After header for wrong PIN and locked out."""
    monkeypatch.setenv("ASSISTANT_PIN", "ValidPasscode999")
    client = TestClient(app)

    res1 = client.post("/api/assistant/auth", json={"pin": "WrongPass123"})
    assert res1.status_code == 401
    assert res1.json()["detail"] == assistant_auth.GENERIC_ERROR_MESSAGE
    assert "Retry-After" in res1.headers

    for _ in range(4):
        client.post("/api/assistant/auth", json={"pin": "WrongPass123"})

    res_locked = client.post("/api/assistant/auth", json={"pin": "ValidPasscode999"})
    assert res_locked.status_code == 401
    assert res_locked.json()["detail"] == assistant_auth.GENERIC_ERROR_MESSAGE
    assert int(res_locked.headers["Retry-After"]) >= assistant_auth.BASE_LOCKOUT_SECONDS - 5


def test_status_endpoint_never_leaks_info_to_unauthenticated(monkeypatch):
    """GET /api/assistant/status returns only authenticated: False and never reveals unset/length/lockout."""
    # 1. PIN unset
    monkeypatch.delenv("ASSISTANT_PIN", raising=False)
    client = TestClient(app)
    res_unset = client.get("/api/assistant/status")
    assert res_unset.status_code == 200
    assert res_unset.json() == {"authenticated": False}
    assert "remote_enabled" not in res_unset.json()

    # 2. PIN set but unauthenticated
    monkeypatch.setenv("ASSISTANT_PIN", "ValidPasscode999")
    res_set = client.get("/api/assistant/status")
    assert res_set.status_code == 200
    assert res_set.json() == {"authenticated": False}

    # 3. Authenticated with valid session token
    tok_res = client.post("/api/assistant/auth", json={"pin": "ValidPasscode999"})
    token = tok_res.json()["token"]
    res_auth = client.get("/api/assistant/status", headers={"Authorization": f"Bearer {token}"})
    assert res_auth.status_code == 200
    assert res_auth.json()["authenticated"] is True
    assert res_auth.json()["expires_in"] > 0


def test_websocket_rejects_raw_pin_and_requires_session_token(monkeypatch):
    """WebSocket strictly rejects raw PIN messages; only valid session tokens are accepted."""
    monkeypatch.setenv("ASSISTANT_PIN", "ValidPasscode123")
    client = TestClient(app)

    # 1. Sending raw PIN over WebSocket is rejected
    t_res = client.get("/api/web-call/ticket?mode=assistant")
    ticket = t_res.json()["ticket"]
    with client.websocket_connect(
        "/api/web-call/ws?mode=assistant",
        subprotocols=[f"vf-ticket.{ticket}"],
        headers={"x-forwarded-for": "198.51.100.5"}
    ) as ws:
        ws.send_json({"type": "auth", "pin": "ValidPasscode123"})
        res = ws.receive_json()
        assert res.get("type") == "error"
        assert "WebSocket requires a session token" in res.get("text")

    # 2. Authenticating via POST /api/assistant/auth first, then passing session token succeeds
    auth_res = client.post("/api/assistant/auth", json={"pin": "ValidPasscode123"})
    token = auth_res.json()["token"]

    t_res2 = client.get("/api/web-call/ticket?mode=assistant")
    ticket2 = t_res2.json()["ticket"]
    with patch("webcall.speak", new_callable=AsyncMock):
        with client.websocket_connect(
            "/api/web-call/ws?mode=assistant",
            subprotocols=[f"vf-ticket.{ticket2}"],
            headers={"x-forwarded-for": "198.51.100.5"}
        ) as ws:
            ws.send_json({"type": "auth", "token": token})
            conn_msg = ws.receive_json()
            assert conn_msg.get("type") == "connected"


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
        candidate = "SensitiveCandidateString123"
        client.post("/api/assistant/auth", json={"pin": candidate})
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

    now = time.time()
    with patch("time.time", return_value=now + assistant_auth.SESSION_TTL_SECONDS + 10):
        assert assistant_auth.auth_manager.verify_session_token(token) is False
