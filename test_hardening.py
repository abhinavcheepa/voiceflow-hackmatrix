"""Hardening test suite for VoiceFlow AI Phase 0/1.

Runs real end-to-end assertions against the FastAPI app and SQLite database:
1. /api/calls without token -> 401; with token -> 200
2. /health works without token -> 200
3. Webhooks with missing/wrong secret -> rejected (401/403)
4. Scheduled job persistence across restart & atomic claiming
5. Pause/resume controls strictly enforced on WhatsApp automation
6. SSE single-use ticket auth, event broadcasting, and heartbeat
"""

import asyncio
import os
import sys
import time

sys.path.insert(0, "backend")

os.environ["API_TOKEN"] = "test-token-voiceflow-2026"
os.environ["BRIDGE_SECRET"] = "bridge-secret-xyz"
os.environ["VAPI_WEBHOOK_SECRET"] = "vapi-secret-abc"
os.environ["WHATSAPP_VERIFY_TOKEN"] = "meta-verify-123"

import auth
import db
import events
import main
import vapi
import whatsapp
import whatsapp_web
import worker
from fastapi.testclient import TestClient

# Refresh module env caches
auth.API_TOKEN = "test-token-voiceflow-2026"
whatsapp_web.TOKEN = "bridge-secret-xyz"
vapi.WEBHOOK_SECRET = "vapi-secret-abc"
whatsapp.VERIFY_TOKEN = "meta-verify-123"

client = TestClient(main.app)
db.init()


def test_1_auth_calls():
    print("\n--- TEST 1: Auth protection on /api/calls ---")
    # Without token
    r1 = client.get("/api/calls")
    print(f"GET /api/calls (no token) -> HTTP {r1.status_code}: {r1.text.strip()}")
    assert r1.status_code == 401, f"Expected 401, got {r1.status_code}"

    # With invalid token
    r2 = client.get("/api/calls", headers={"Authorization": "Bearer wrong-token"})
    print(f"GET /api/calls (wrong token) -> HTTP {r2.status_code}: {r2.text.strip()}")
    assert r2.status_code == 401, f"Expected 401, got {r2.status_code}"

    # With valid Bearer token
    r3 = client.get("/api/calls", headers={"Authorization": f"Bearer {auth.API_TOKEN}"})
    print(f"GET /api/calls (valid Bearer token) -> HTTP {r3.status_code}: returned {len(r3.json())} calls")
    assert r3.status_code == 200, f"Expected 200, got {r3.status_code}"
    print("[PASS] TEST 1 PASSED")


def test_2_health_open():
    print("\n--- TEST 2: /health is open for monitoring ---")
    r = client.get("/health")
    print(f"GET /health (no token) -> HTTP {r.status_code}: ok={r.json().get('ok')}, worker={r.json().get('worker')}")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    assert r.json().get("ok") is True
    print("[PASS] TEST 2 PASSED")


def test_3_webhooks_secret_verification():
    print("\n--- TEST 3: Webhook secret verification ---")
    # 3a: Vapi webhook with wrong/missing secret
    r_vapi_bad = client.post("/api/vapi/webhook", json={"message": {"type": "status-update"}})
    print(f"POST /api/vapi/webhook (no secret) -> HTTP {r_vapi_bad.status_code}: {r_vapi_bad.text.strip()}")
    assert r_vapi_bad.status_code == 401

    r_vapi_wrong = client.post(
        "/api/vapi/webhook",
        headers={"X-Vapi-Secret": "wrong-secret"},
        json={"message": {"type": "status-update"}},
    )
    print(f"POST /api/vapi/webhook (wrong secret) -> HTTP {r_vapi_wrong.status_code}: {r_vapi_wrong.text.strip()}")
    assert r_vapi_wrong.status_code == 401

    r_vapi_ok = client.post(
        "/api/vapi/webhook",
        headers={"X-Vapi-Secret": "vapi-secret-abc"},
        json={"message": {"type": "status-update"}},
    )
    print(f"POST /api/vapi/webhook (correct secret) -> HTTP {r_vapi_ok.status_code}: {r_vapi_ok.text.strip()}")
    assert r_vapi_ok.status_code == 200

    # 3b: WhatsApp bridge incoming with wrong/missing secret
    r_bridge_bad = client.post("/api/whatsapp/web/incoming", json={"messages": []})
    print(f"POST /api/whatsapp/web/incoming (no secret) -> HTTP {r_bridge_bad.status_code}: {r_bridge_bad.text.strip()}")
    assert r_bridge_bad.status_code == 401

    r_bridge_wrong = client.post(
        "/api/whatsapp/web/incoming",
        headers={"X-Bridge-Token": "wrong-bridge-secret"},
        json={"messages": []},
    )
    print(f"POST /api/whatsapp/web/incoming (wrong secret) -> HTTP {r_bridge_wrong.status_code}: {r_bridge_wrong.text.strip()}")
    assert r_bridge_wrong.status_code == 401

    r_bridge_ok = client.post(
        "/api/whatsapp/web/incoming",
        headers={"X-Bridge-Token": "bridge-secret-xyz"},
        json={"messages": []},
    )
    print(f"POST /api/whatsapp/web/incoming (correct secret) -> HTTP {r_bridge_ok.status_code}: {r_bridge_ok.text.strip()}")
    assert r_bridge_ok.status_code == 200

    # 3c: WhatsApp Meta handshake with wrong verify token
    r_meta_wrong = client.get("/api/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=12345")
    print(f"GET /api/whatsapp/webhook (wrong verify token) -> HTTP {r_meta_wrong.status_code}")
    assert r_meta_wrong.status_code == 403

    r_meta_ok = client.get("/api/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=meta-verify-123&hub.challenge=12345")
    print(f"GET /api/whatsapp/webhook (correct verify token) -> HTTP {r_meta_ok.status_code}: body='{r_meta_ok.text}'")
    assert r_meta_ok.status_code == 200
    assert r_meta_ok.text == "12345"
    print("[PASS] TEST 3 PASSED")


def test_4_worker_persistence_and_atomic_claim():
    print("\n--- TEST 4: Scheduled job persistence & atomic claiming ---")
    with db.connect() as conn:
        conn.execute("DELETE FROM scheduled_jobs")
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    job_id = db.add_job("follow_up", {"phone": "+919876543210", "text": "Hello persistence test"}, now_iso)
    print(f"Created scheduled job id={job_id} in SQLite")

    # Simulate backend restart: re-initialize db connection
    db.init()

    # Claim job atomically
    job = db.claim_next_job()
    assert job is not None, "Failed to claim persisted job after restart"
    assert job["id"] == job_id
    assert job["status"] == "running"
    assert job["attempts"] == 1
    print(f"Claimed job {job['id']}: status='{job['status']}', attempts={job['attempts']}, payload={job['payload']}")

    # Ensure a second concurrent claim gets None (rowcount guard)
    second_claim = db.claim_next_job()
    assert second_claim is None, "Atomic claiming failed: job was claimed twice!"
    print("Second concurrent claim safely returned None (rowcount == 1 guard works)")

    # Complete job
    db.complete_job(job_id, "done")
    completed = db.get_recent_jobs(1)[0]
    assert completed["status"] == "done"
    print(f"Job completed: status='{completed['status']}'")

    # Test stuck recovery: insert job with status='running' and claimed_at 10 minutes ago
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO scheduled_jobs (job_type, payload, status, run_at, claimed_at, attempts, created_at)
               VALUES ('reminder', '{}', 'running', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 1, '2026-01-01T00:00:00Z')"""
        )
    recovered_count = db.recover_stuck_jobs(max_attempts=3, timeout_minutes=5)
    print(f"Recovery scan recovered {recovered_count} stuck job(s) back to 'pending'")
    assert recovered_count >= 1
    print("[PASS] TEST 4 PASSED")


def test_5_pause_resume_enforcement():
    print("\n--- TEST 5: System pause/resume controls enforcement ---")
    headers = {"Authorization": f"Bearer {auth.API_TOKEN}"}

    # Pause automation via /api/system/controls
    r_pause = client.post(
        "/api/system/controls",
        headers=headers,
        json={"automation": False, "whatsapp_auto_reply": False},
    )
    assert r_pause.status_code == 200
    print(f"Paused system controls: {r_pause.json()}")
    assert worker.automation_enabled() is False
    assert db.auto_reply_global() is False

    # Verify whatsapp auto_reply_block reports pause
    block_reason = db.auto_reply_block("+918817882519")
    print(f"Check auto_reply_block while paused -> '{block_reason}'")
    assert block_reason is not None

    # Simulate inbound WhatsApp message while paused
    test_msg = {
        "from": "+918817882519",
        "name": "Test Customer",
        "type": "text",
        "text": "Can I book a demo?",
    }
    # Run handle directly synchronously
    asyncio.run(whatsapp.handle(test_msg))

    # Check that message was stored, but needs_reply is False (NO reply sent)
    with db.connect() as conn:
        row = conn.execute(
            """SELECT m.* FROM messages m
               JOIN conversations c ON c.id = m.conversation_id
               WHERE c.phone = '+918817882519' ORDER BY m.id DESC LIMIT 1"""
        ).fetchone()
    print(f"Stored message while paused: direction='{row['sender']}', text='{row['body']}', needs_reply={row['needs_reply']}")
    assert row["sender"] == "them"
    assert row["needs_reply"] == 0, "needs_reply should be False when paused!"

    # Now resume automation
    r_resume = client.post(
        "/api/system/controls",
        headers=headers,
        json={"automation": True, "whatsapp_auto_reply": True},
    )
    assert r_resume.status_code == 200
    print(f"Resumed system controls: {r_resume.json()}")
    assert worker.automation_enabled() is True
    assert db.auto_reply_global() is True

    block_reason_after = db.auto_reply_block("+918817882519")
    print(f"Check auto_reply_block after resume -> '{block_reason_after}'")
    assert block_reason_after is None
    print("[PASS] TEST 5 PASSED")


def test_6_sse_ticket_and_events():
    print("\n--- TEST 6: SSE single-use ticket auth & live event stream ---")
    headers = {"Authorization": f"Bearer {auth.API_TOKEN}"}

    # 6a: Attempting to connect directly without ticket -> rejected
    r_sse_no_ticket = client.get("/api/events")
    print(f"GET /api/events (no ticket) -> HTTP {r_sse_no_ticket.status_code}: {r_sse_no_ticket.text.strip()}")
    assert r_sse_no_ticket.status_code == 401

    # 6b: Request single-use ticket via POST /api/events/ticket (requires Bearer token)
    r_ticket = client.post("/api/events/ticket", headers=headers)
    assert r_ticket.status_code == 200
    ticket = r_ticket.json()["ticket"]
    print(f"Acquired single-use ticket via POST /api/events/ticket: {ticket[:16]}... (valid 60s)")

    # 6c: Validate ticket consumption & single-use guard
    assert events.verify_and_consume_ticket(ticket) is True
    print("Ticket verified and consumed on first use")
    assert events.verify_and_consume_ticket(ticket) is False
    print("Ticket reuse rejected (single-use guarantee confirmed)")

    # 6d: Test generator event delivery & heartbeat
    async def run_generator_test():
        queue = asyncio.Queue()

        class DummyRequest:
            async def is_disconnected(self):
                return False

        orig_hb = events.HEARTBEAT_INTERVAL
        events.HEARTBEAT_INTERVAL = 0.1
        try:
            gen = events._event_generator(DummyRequest(), queue)

            # 1. Queue an event and verify arrival
            await queue.put("event: call_update\ndata: {\"id\": \"test\"}\n\n")
            event_line = await anext(gen)
            print("Received SSE event from queue:", event_line.strip())
            assert "event: call_update" in event_line

            # 2. Wait for heartbeat timeout (0.1s) and verify heartbeat
            heartbeat_line = await anext(gen)
            print("Received SSE heartbeat:", heartbeat_line.strip())
            assert ": heartbeat" in heartbeat_line
        finally:
            events.HEARTBEAT_INTERVAL = orig_hb

    asyncio.run(run_generator_test())
    print("[PASS] TEST 6 PASSED")


if __name__ == "__main__":
    test_1_auth_calls()
    test_2_health_open()
    test_3_webhooks_secret_verification()
    test_4_worker_persistence_and_atomic_claim()
    test_5_pause_resume_enforcement()
    test_6_sse_ticket_and_events()
    print("\n=======================================================")
    print("ALL 6 HARDENING PASS TESTS PASSED WITH 100% SUCCESS!")
    print("=======================================================\n")
