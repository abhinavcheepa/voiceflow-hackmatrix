"""Tests for Part 9 (ISSUE-08): Web-call security gate, single-use tickets,
rate limits, concurrency caps, origin checks, duration timeouts, idle timeouts,
frame-rate caps, budget enforcement, and customer tool isolation.
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
    webcall_gate.gate._ip_history_ticket.clear()
    webcall_gate.gate._ip_history_ws.clear()
    webcall_gate.gate._active_calls.clear()
    webcall_gate.gate._daily_seconds_used = 0.0
    webcall_gate.gate._ip_daily_seconds_used.clear()

    # Defaults for tests
    monkeypatch.setattr(webcall_gate, "WEBCALL_PUBLIC", True)
    monkeypatch.setattr(webcall_gate, "ALLOWED_ORIGINS", [])
    monkeypatch.setattr(webcall_gate, "MAX_CONCURRENT_WEBCALLS", 2)
    monkeypatch.setattr(webcall_gate, "RATE_LIMIT_PER_IP", 5)
    monkeypatch.setattr(webcall_gate, "WS_CONNECT_RATE_LIMIT_PER_IP", 5)
    monkeypatch.setattr(webcall_gate, "MAX_WEBCALL_DURATION_SECONDS", 300)
    monkeypatch.setattr(webcall_gate, "WEBCALL_IDLE_TIMEOUT_SECONDS", 60)
    monkeypatch.setattr(webcall_gate, "MAX_FRAME_SIZE_BYTES", 1024)
    monkeypatch.setattr(webcall_gate, "MAX_FRAME_RATE_PER_SEC", 25)
    monkeypatch.setattr(webcall_gate, "DAILY_WEBCALL_MINUTES_BUDGET", 120.0)
    monkeypatch.setattr(webcall_gate, "PER_IP_DAILY_MINUTES", 30.0)
    yield


def test_ticket_single_use_dedicated():
    """Single-use ticket: consumed once, second attempt fails immediately."""
    token = asyncio.run(webcall_gate.gate.issue_ticket("testclient"))
    assert token and len(token) > 20

    # First consumption succeeds
    first = asyncio.run(webcall_gate.gate.consume_ticket(token, "testclient"))
    assert first is not None
    assert first["ip"] == "testclient"

    # Second consumption immediately returns None
    second = asyncio.run(webcall_gate.gate.consume_ticket(token, "testclient"))
    assert second is None


def test_ticket_ttl_expiry_dedicated():
    """Ticket expires after TTL (30s) and cannot be consumed."""
    token = asyncio.run(webcall_gate.gate.issue_ticket("testclient"))
    # Manually expire
    webcall_gate.gate._tickets[token]["expires_at"] = time.time() - 1.0

    consumed = asyncio.run(webcall_gate.gate.consume_ticket(token, "testclient"))
    assert consumed is None


def test_ticket_never_accepted_from_query_string():
    """Passing ticket in URL query string is NOT accepted (subprotocol or first msg only)."""
    client = TestClient(app)
    r = client.get("/api/web-call/ticket")
    token = r.json()["ticket"]

    # Attempting to authenticate via ?ticket= in URL is rejected
    with client.websocket_connect(f"/api/web-call/ws?ticket={token}") as ws:
        msg = ws.receive()
        assert msg["type"] == "websocket.close"
        assert msg["code"] == webcall_gate.WS_CLOSE_INVALID_TICKET


def test_ticket_accepted_via_subprotocol():
    """Ticket delivered via sec-websocket-protocol is accepted and negotiated."""
    client = TestClient(app)
    r = client.get("/api/web-call/ticket")
    token = r.json()["ticket"]

    with client.websocket_connect(
        "/api/web-call/ws",
        subprotocols=[f"vf-ticket.{token}"],
    ) as ws:
        msg = ws.receive_json()
        assert msg["type"] == "connected"
        assert "callId" in msg


def test_ticket_accepted_via_first_json_frame():
    """Ticket delivered via initial JSON message frame is accepted."""
    client = TestClient(app)
    r = client.get("/api/web-call/ticket")
    token = r.json()["ticket"]

    with client.websocket_connect("/api/web-call/ws") as ws:
        import json
        ws.send_text(json.dumps({"ticket": token}))
        msg = ws.receive_json()
        assert msg["type"] == "connected"
        assert "callId" in msg


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


def test_separate_rate_limit_counters(monkeypatch):
    """Ticket issue and WS connect have separate sliding window counters."""
    monkeypatch.setattr(webcall_gate, "RATE_LIMIT_PER_IP", 2)
    monkeypatch.setattr(webcall_gate, "WS_CONNECT_RATE_LIMIT_PER_IP", 2)
    client = TestClient(app)

    # 2 ticket issues succeed
    r1 = client.get("/api/web-call/ticket")
    assert r1.status_code == 200
    r2 = client.get("/api/web-call/ticket")
    assert r2.status_code == 200

    # 3rd ticket issue trips ticket rate limit -> 429
    r3 = client.get("/api/web-call/ticket")
    assert r3.status_code == 429

    # But WS connect rate limit has its own counter
    t1 = r1.json()["ticket"]
    with client.websocket_connect("/api/web-call/ws", subprotocols=[f"vf-ticket.{t1}"]) as ws:
        msg = ws.receive_json()
        assert msg["type"] == "connected"


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
    asyncio.run(webcall_gate.gate.release_slot("CALL-001", "127.0.0.1", 10.0))
    assert webcall_gate.gate.active_count == 0

    # Slot 2 can now be acquired
    acq2_retry = asyncio.run(webcall_gate.gate.acquire_slot("CALL-002", "127.0.0.1"))
    assert acq2_retry is True
    asyncio.run(webcall_gate.gate.release_slot("CALL-002", "127.0.0.1", 5.0))


def test_slot_released_after_handler_crash():
    """When a websocket handler encounters an unhandled exception, slot is released in finally."""
    client = TestClient(app)
    r = client.get("/api/web-call/ticket")
    token = r.json()["ticket"]

    # Mock speak to raise an unhandled exception mid-call
    with patch("webcall.speak", side_effect=RuntimeError("Unexpected audio crash")):
        with pytest.raises(RuntimeError):
            with client.websocket_connect("/api/web-call/ws", subprotocols=[f"vf-ticket.{token}"]):
                pass

    # Slot MUST be released back to 0
    assert webcall_gate.gate.active_count == 0


def test_daily_budget_exhausted_global(monkeypatch):
    """When global daily minute budget is reached, connection is rejected with 4002."""
    monkeypatch.setattr(webcall_gate, "DAILY_WEBCALL_MINUTES_BUDGET", 0.05)  # 3 seconds
    client = TestClient(app)

    # Consume 4 seconds globally
    asyncio.run(webcall_gate.gate.release_slot("C1", "127.0.0.1", 4.0))

    # Next ticket issue fails with budget error (402)
    r = client.get("/api/web-call/ticket")
    assert r.status_code == 402

    # Direct WS connect also immediately closes with 4002
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/api/web-call/ws"):
            pass
    assert exc.value.code == webcall_gate.WS_CLOSE_BUDGET_EXCEEDED


def test_daily_budget_exhausted_per_ip(monkeypatch):
    """When a single IP reaches its per-IP daily budget, it cannot drain the server."""
    monkeypatch.setattr(webcall_gate, "DAILY_WEBCALL_MINUTES_BUDGET", 100.0)
    monkeypatch.setattr(webcall_gate, "PER_IP_DAILY_MINUTES", 0.05)  # 3 seconds
    client = TestClient(app)

    # IP 127.0.0.1 uses 4 seconds
    asyncio.run(webcall_gate.gate.release_slot("C1", "testclient", 4.0))

    # IP testclient is rejected
    r = client.get("/api/web-call/ticket")
    assert r.status_code == 402


def test_duration_timeout_kills_socket(monkeypatch):
    """Call exceeding MAX_WEBCALL_DURATION_SECONDS is closed with 4010."""
    monkeypatch.setattr(webcall_gate, "MAX_WEBCALL_DURATION_SECONDS", 0.1)
    client = TestClient(app)
    r = client.get("/api/web-call/ticket")
    token = r.json()["ticket"]

    with patch("webcall.speak", new_callable=AsyncMock):
        with client.websocket_connect("/api/web-call/ws", subprotocols=[f"vf-ticket.{token}"]) as ws:
            ws.receive_json()  # connected
            time.sleep(0.2)
            # Send ping to trigger loop iteration after timeout
            ws.send_text('{"type": "ping"}')
            msg1 = ws.receive_json()
            assert msg1["type"] == "ended"
            assert msg1["reason"] == "duration_limit"
            msg2 = ws.receive()
            assert msg2["type"] == "websocket.close"
            assert msg2["code"] == webcall_gate.WS_CLOSE_DURATION_EXCEEDED


def test_idle_timeout_kills_socket(monkeypatch):
    """Call with no activity exceeding WEBCALL_IDLE_TIMEOUT_SECONDS is closed."""
    monkeypatch.setattr(webcall_gate, "WEBCALL_IDLE_TIMEOUT_SECONDS", 0.1)
    client = TestClient(app)
    r = client.get("/api/web-call/ticket")
    token = r.json()["ticket"]

    with patch("webcall.speak", new_callable=AsyncMock):
        with client.websocket_connect("/api/web-call/ws", subprotocols=[f"vf-ticket.{token}"]) as ws:
            ws.receive_json()  # connected
            # Wait for idle timeout
            msg1 = ws.receive_json()
            assert msg1["type"] == "ended"
            assert msg1["reason"] == "idle_timeout"
            msg2 = ws.receive()
            assert msg2["type"] == "websocket.close"


def test_frame_rate_cap_kills_socket(monkeypatch):
    """Flooding messages exceeding MAX_FRAME_RATE_PER_SEC closes socket."""
    monkeypatch.setattr(webcall_gate, "MAX_FRAME_RATE_PER_SEC", 3)
    client = TestClient(app)
    r = client.get("/api/web-call/ticket")
    token = r.json()["ticket"]

    with patch("webcall.speak", new_callable=AsyncMock):
        with client.websocket_connect("/api/web-call/ws", subprotocols=[f"vf-ticket.{token}"]) as ws:
            ws.receive_json()  # connected
            # Rapidly send 5 frames
            for i in range(5):
                ws.send_text('{"type": "ping"}')
            # One will be closed due to frame rate cap
            found_close = False
            while True:
                msg = ws.receive()
                if msg.get("type") == "websocket.close":
                    assert msg["code"] == webcall_gate.WS_CLOSE_FRAME_TOO_LARGE
                    found_close = True
                    break
            assert found_close


def test_websocket_oversized_frame_rejected(monkeypatch):
    monkeypatch.setattr(webcall_gate, "MAX_FRAME_SIZE_BYTES", 100)
    client = TestClient(app)
    r = client.get("/api/web-call/ticket")
    token = r.json()["ticket"]

    with patch("webcall.speak", new_callable=AsyncMock):
        with client.websocket_connect("/api/web-call/ws", subprotocols=[f"vf-ticket.{token}"]) as ws:
            ws.receive_json()  # connected
            # Send oversized audio chunk (>100 bytes)
            ws.send_bytes(b"\x00" * 500)
            msg = ws.receive()
            assert msg["type"] == "websocket.close"
            assert msg["code"] == webcall_gate.WS_CLOSE_FRAME_TOO_LARGE


def test_owner_tools_unreachable_in_customer_webcall():
    import tools
    customer_ctx = {"channel": "call", "agent_id": 1, "squad": []}

    res_send = tools.run("send_whatsapp", {"phone": "919811111111", "message": "Hi"}, customer_ctx)
    assert res_send == "Tool 'send_whatsapp' is not available."

    res_chat = tools.run("start_chat", {"phone": "919811111111", "goal": "collect"}, customer_ctx)
    assert res_chat == "Tool 'start_chat' is not available."

    res_settings = tools.run("auto_reply_settings", {"action": "on"}, customer_ctx)
    assert res_settings == "Tool 'auto_reply_settings' is not available."


def test_ticket_value_never_appears_in_logs(caplog):
    """Single-use ticket value is never written to application or access logs."""
    import logging
    client = TestClient(app)
    with caplog.at_level(logging.DEBUG):
        r = client.get("/api/web-call/ticket")
        token = r.json()["ticket"]
        with client.websocket_connect("/api/web-call/ws", subprotocols=[f"vf-ticket.{token}"]) as ws:
            ws.receive_json()

    # The token must NEVER appear anywhere in the captured logs
    assert token not in caplog.text


def test_client_ip_trusted_proxy_and_untrusted(monkeypatch):
    """X-Forwarded-For is only honored when immediate client host is in TRUSTED_PROXIES."""
    from unittest.mock import MagicMock
    monkeypatch.setattr(webcall_gate, "TRUSTED_PROXIES", {"127.0.0.1", "10.0.0.1"})

    # 1. Trusted proxy (e.g. ngrok / Caddy on localhost): client IP extracted from X-Forwarded-For
    req_trusted = MagicMock()
    req_trusted.client.host = "127.0.0.1"
    req_trusted.headers = {"x-forwarded-for": "203.0.113.195, 10.0.0.1"}
    assert webcall_gate.get_real_client_ip(req_trusted) == "203.0.113.195"

    # Forged spoofed header behind proxy: rightmost untrusted entry is picked, ignoring left forged IP
    req_spoof = MagicMock()
    req_spoof.client.host = "127.0.0.1"
    req_spoof.headers = {"x-forwarded-for": "1.2.3.4, 203.0.113.195"}
    assert webcall_gate.get_real_client_ip(req_spoof) == "203.0.113.195"

    # 2. Untrusted direct client: forged X-Forwarded-For header is IGNORED
    req_untrusted = MagicMock()
    req_untrusted.client.host = "198.51.100.2"
    req_untrusted.headers = {"x-forwarded-for": "8.8.8.8"}
    assert webcall_gate.get_real_client_ip(req_untrusted) == "198.51.100.2"

    # 3. No proxy configured: direct peer host used
    monkeypatch.setattr(webcall_gate, "TRUSTED_PROXIES", set())
    assert webcall_gate.get_real_client_ip(req_trusted) == "127.0.0.1"


def test_demo_mode_env_override(monkeypatch):
    """WEBCALL_DEMO_MODE=true raises default rate limits for live demos."""
    monkeypatch.setenv("WEBCALL_DEMO_MODE", "true")
    # Re-evaluate module defaults
    import importlib
    importlib.reload(webcall_gate)
    assert webcall_gate.WEBCALL_DEMO_MODE is True
    assert webcall_gate.RATE_LIMIT_PER_IP == 50
    assert webcall_gate.DAILY_WEBCALL_MINUTES_BUDGET == 300.0
    assert webcall_gate.PER_IP_DAILY_MINUTES == 30.0

    # Reset back to test defaults
    monkeypatch.delenv("WEBCALL_DEMO_MODE", raising=False)
    importlib.reload(webcall_gate)
    assert webcall_gate.WEBCALL_DEMO_MODE is False
    assert webcall_gate.RATE_LIMIT_PER_IP == 5
    assert webcall_gate.DAILY_WEBCALL_MINUTES_BUDGET == 120.0
    assert webcall_gate.PER_IP_DAILY_MINUTES == 15.0
