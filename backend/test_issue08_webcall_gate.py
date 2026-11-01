"""Tests for Part 9 (ISSUE-08): Web-call security gate, single-use tickets,
rate limits, concurrency caps, origin checks, duration timeouts, and customer tool isolation.
"""

import asyncio
import time
from unittest.mock import AsyncMock, patch
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import auth
import db
import webcall_gate
from main import app


@pytest.fixture(autouse=True)
def setup_test_gate(monkeypatch, tmp_path):
    test_db = str(tmp_path / "test_webcall.db")
    monkeypatch.setattr(db, "DB_PATH", test_db)
    db.init()

    # Reset gate singleton state
    webcall_gate.gate._tickets.clear()
    webcall_gate.gate._ip_history.clear()
    webcall_gate.gate._active_calls.clear()
    webcall_gate.gate._daily_seconds_used = 0.0

    # Defaults for tests
    monkeypatch.setattr(webcall_gate, "WEBCALL_PUBLIC", True)
    monkeypatch.setattr(webcall_gate, "ALLOWED_ORIGINS", [])
    monkeypatch.setattr(webcall_gate, "MAX_CONCURRENT_WEBCALLS", 2)
    monkeypatch.setattr(webcall_gate, "RATE_LIMIT_PER_IP", 5)
    monkeypatch.setattr(webcall_gate, "MAX_WEBCALL_DURATION_SECONDS", 2)
    monkeypatch.setattr(webcall_gate, "MAX_FRAME_SIZE_BYTES", 1024)  # 1 KB for test
    yield


def test_ticket_issue_and_atomic_consumption():
    client = TestClient(app)
    # Issue ticket via GET
    r = client.get("/api/web-call/ticket")
    assert r.status_code == 200
    token = r.json().get("ticket")
    assert token and len(token) > 20

    # Consume token
    consumed = asyncio.run(webcall_gate.gate.consume_ticket(token, "testclient"))
    assert consumed is not None
    assert consumed["ip"] == "testclient"

    # Reuse token immediately fails
    reused = asyncio.run(webcall_gate.gate.consume_ticket(token, "testclient"))
    assert reused is None


def test_ticket_expiry():
    token = asyncio.run(webcall_gate.gate.issue_ticket("testclient"))
    # Manually expire ticket
    webcall_gate.gate._tickets[token]["expires_at"] = time.time() - 1.0

    consumed = asyncio.run(webcall_gate.gate.consume_ticket(token, "testclient"))
    assert consumed is None


def test_private_mode_requires_auth(monkeypatch):
    monkeypatch.setattr(webcall_gate, "WEBCALL_PUBLIC", False)
    monkeypatch.setattr(auth, "API_TOKEN", "test-secret-token")
    client = TestClient(app)

    # No auth header -> 401
    r = client.get("/api/web-call/ticket")
    assert r.status_code == 401

    # With auth header
    headers = {"Authorization": "Bearer test-secret-token"}
    r2 = client.get("/api/web-call/ticket", headers=headers)
    assert r2.status_code == 200
    assert "ticket" in r2.json()


def test_wrong_origin_rejected(monkeypatch):
    monkeypatch.setattr(webcall_gate, "ALLOWED_ORIGINS", ["http://trusted-site.com"])
    client = TestClient(app)

    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/api/web-call/ws", headers={"origin": "http://evil-site.com"}):
            pass
    assert exc.value.code == webcall_gate.WS_CLOSE_FORBIDDEN_ORIGIN


def test_rate_limit_trip(monkeypatch):
    monkeypatch.setattr(webcall_gate, "RATE_LIMIT_PER_IP", 3)
    client = TestClient(app)
    # Attempts 1, 2, 3 succeed
    for _ in range(3):
        r = client.get("/api/web-call/ticket")
        assert r.status_code == 200

    # Attempt 4 trips rate limit -> 429
    r_tripped = client.get("/api/web-call/ticket")
    assert r_tripped.status_code == 429


def test_concurrent_cap_race_and_slot_release(monkeypatch):
    monkeypatch.setattr(webcall_gate, "MAX_CONCURRENT_WEBCALLS", 1)

    # Slot 1 acquired
    acq1 = asyncio.run(webcall_gate.gate.acquire_slot("CALL-001", "127.0.0.1"))
    assert acq1 is True
    assert webcall_gate.gate.active_count == 1

    # Slot 2 rejected due to cap
    acq2 = asyncio.run(webcall_gate.gate.acquire_slot("CALL-002", "127.0.0.1"))
    assert acq2 is False

    # Release slot 1
    asyncio.run(webcall_gate.gate.release_slot("CALL-001", 10.0))
    assert webcall_gate.gate.active_count == 0

    # Slot 2 can now be acquired
    acq2_retry = asyncio.run(webcall_gate.gate.acquire_slot("CALL-002", "127.0.0.1"))
    assert acq2_retry is True
    asyncio.run(webcall_gate.gate.release_slot("CALL-002", 5.0))


def test_websocket_ticket_exchange_and_subprotocol():
    client = TestClient(app)
    r = client.get("/api/web-call/ticket")
    token = r.json()["ticket"]

    # Connect using subprotocol
    with client.websocket_connect(
        "/api/web-call/ws",
        subprotocols=[f"vf-ticket.{token}"],
    ) as ws:
        msg = ws.receive_json()
        assert msg["type"] == "connected"
        assert "callId" in msg


def test_websocket_missing_or_invalid_ticket_closed():
    client = TestClient(app)
    with client.websocket_connect("/api/web-call/ws?ticket=fake_invalid_token") as ws:
        msg = ws.receive()
        assert msg["type"] == "websocket.close"
        assert msg["code"] == webcall_gate.WS_CLOSE_INVALID_TICKET


def test_websocket_oversized_frame_rejected(monkeypatch):
    monkeypatch.setattr(webcall_gate, "MAX_FRAME_SIZE_BYTES", 100)
    client = TestClient(app)
    r = client.get("/api/web-call/ticket")
    token = r.json()["ticket"]

    with patch("webcall.speak", new_callable=AsyncMock):
        with client.websocket_connect(f"/api/web-call/ws?ticket={token}") as ws:
            ws.receive_json()  # "connected"
            # Send oversized audio chunk (>100 bytes)
            ws.send_bytes(b"\x00" * 500)
            msg = ws.receive()
            assert msg["type"] == "websocket.close"
            assert msg["code"] == webcall_gate.WS_CLOSE_FRAME_TOO_LARGE


def test_owner_tools_unreachable_in_customer_webcall():
    import tools
    # Customer web-call context does NOT have "owner": True
    customer_ctx = {"channel": "call", "agent_id": 1, "squad": []}

    res_send = tools.run("send_whatsapp", {"phone": "919811111111", "message": "Hi"}, customer_ctx)
    assert res_send == "Tool 'send_whatsapp' is not available."

    res_chat = tools.run("start_chat", {"phone": "919811111111", "goal": "collect"}, customer_ctx)
    assert res_chat == "Tool 'start_chat' is not available."

    res_settings = tools.run("auto_reply_settings", {"action": "on"}, customer_ctx)
    assert res_settings == "Tool 'auto_reply_settings' is not available."
