import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

import db
import whatsapp
import whatsapp_web
from main import app


@pytest.fixture(autouse=True)
def setup_clean_db(tmp_path, monkeypatch):
    test_db = str(tmp_path / "test_whatsapp.db")
    monkeypatch.setattr(db, "DB_PATH", test_db)
    db.init()
    # Enable global auto reply for tests
    db.set_auto_reply_global(True)
    yield


def test_meta_webhook_parse_extracts_id():
    payload = {
        "entry": [{
            "changes": [{
                "value": {
                    "contacts": [{"wa_id": "919876543210", "profile": {"name": "Rohan"}}],
                    "messages": [{
                        "id": "wamid.TEST12345",
                        "from": "919876543210",
                        "type": "text",
                        "text": {"body": "Namaste"},
                    }]
                }
            }]
        }]
    }
    messages = whatsapp.parse(payload)
    assert len(messages) == 1
    assert messages[0].get("id") == "wamid.TEST12345"


def test_meta_webhook_status_stores_nothing():
    status_payload = {
        "entry": [{
            "changes": [{
                "value": {
                    "statuses": [{
                        "id": "wamid.TEST12345",
                        "status": "delivered",
                        "recipient_id": "919876543210"
                    }]
                }
            }]
        }]
    }
    messages = whatsapp.parse(status_payload)
    assert messages == []


@pytest.mark.anyio
async def test_duplicate_webhook_payload_handled_once():
    payload = {
        "id": "wamid.UNIQUE_ID_999",
        "from": "919876543210",
        "name": "Rohan",
        "type": "text",
        "text": "Hello test deduplication",
    }

    with patch("whatsapp.reply_to", new_callable=AsyncMock) as mock_reply:
        # First delivery
        await whatsapp.handle(payload)
        assert mock_reply.call_count == 1

        # Second delivery (duplicate retry from Meta)
        await whatsapp.handle(payload)
        assert mock_reply.call_count == 1

    # Check database messages count
    with db.connect() as conn:
        rows = conn.execute("SELECT id, wa_id, body FROM messages WHERE wa_id = ?", ("wamid.UNIQUE_ID_999",)).fetchall()
        assert len(rows) == 1


@pytest.mark.anyio
async def test_whatsapp_web_parse_extracts_id():
    payload = {
        "id": "true_919876543210@c.us_3EB012345",
        "from": "919876543210",
        "name": "Rohan",
        "type": "text",
        "text": "Hello via Web Bridge",
    }
    parsed = whatsapp_web.parse(payload)
    assert parsed.get("id") == "true_919876543210@c.us_3EB012345"


def test_meta_webhook_endpoint_deduplication_e2e(monkeypatch):
    monkeypatch.setenv('ALLOW_UNSIGNED_WEBHOOKS', 'true')
    client = TestClient(app)
    raw_payload = {
        "entry": [{
            "changes": [{
                "value": {
                    "contacts": [{"wa_id": "919876543210", "profile": {"name": "Rohan"}}],
                    "messages": [{
                        "id": "wamid.E2E_DEDUP_123",
                        "from": "919876543210",
                        "type": "text",
                        "text": {"body": "Testing e2e dedup"},
                    }]
                }
            }]
        }]
    }

    with patch("whatsapp.reply_to", new_callable=AsyncMock) as mock_reply:
        # First POST
        res1 = client.post("/api/whatsapp/webhook", json=raw_payload)
        assert res1.status_code == 200
        assert mock_reply.call_count == 1

        # Second POST with identical payload
        res2 = client.post("/api/whatsapp/webhook", json=raw_payload)
        assert res2.status_code == 200
        assert mock_reply.call_count == 1

    with db.connect() as conn:
        rows = conn.execute("SELECT id, wa_id FROM messages WHERE wa_id = ?", ("wamid.E2E_DEDUP_123",)).fetchall()
        assert len(rows) == 1


def test_whatsapp_webhook_signature_lifecycle(monkeypatch):
    """WHATSAPP_APP_SECRET unset must FAIL CLOSED (HTTP 403).
    Unsigned allowed only with explicit ALLOW_UNSIGNED_WEBHOOKS=true.
    Secret set + bad sig -> 403; Secret set + good sig -> 200."""
    import json
    import hmac
    import hashlib
    from starlette.testclient import TestClient
    from main import app

    client = TestClient(app)
    raw_payload = {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "1001",
            "changes": [{
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {"phone_number_id": "12345"},
                    "messages": [{
                        "id": "wamid.TEST_SIG_LIFECYCLE",
                        "from": "919999000001",
                        "timestamp": "1700000000",
                        "type": "text",
                        "text": {"body": "test signature"}
                    }]
                },
                "field": "messages"
            }]
        }]
    }
    raw_bytes = json.dumps(raw_payload).encode("utf-8")

    # 1. Unset secret: FAILS CLOSED (verify_signature returns False, endpoint returns 403)
    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
    monkeypatch.delenv("ALLOW_UNSIGNED_WEBHOOKS", raising=False)
    assert whatsapp.verify_signature(raw_bytes, None) is False
    res_unset = client.post("/api/whatsapp/webhook", data=raw_bytes, headers={"Content-Type": "application/json"})
    assert res_unset.status_code == 403
    assert res_unset.json()["detail"] == "invalid or missing webhook signature"

    # 2. Dev flag ALLOW_UNSIGNED_WEBHOOKS=true: allowed
    monkeypatch.setenv("ALLOW_UNSIGNED_WEBHOOKS", "true")
    assert whatsapp.verify_signature(raw_bytes, None) is True
    res_flag = client.post("/api/whatsapp/webhook", data=raw_bytes, headers={"Content-Type": "application/json"})
    assert res_flag.status_code == 200
    assert res_flag.json() == {"ok": True}

    # 3. Secret set + bad signature: rejected (403)
    secret = "meta_app_secret_super_secure_123"
    monkeypatch.delenv("ALLOW_UNSIGNED_WEBHOOKS", raising=False)
    monkeypatch.setenv("WHATSAPP_APP_SECRET", secret)
    assert whatsapp.verify_signature(raw_bytes, None) is False
    assert whatsapp.verify_signature(raw_bytes, "sha256=bad_hex_code") is False
    res_bad = client.post("/api/whatsapp/webhook", data=raw_bytes,
                          headers={"Content-Type": "application/json", "X-Hub-Signature-256": "sha256=bad_hex_code"})
    assert res_bad.status_code == 403

    # 4. Secret set + good signature: accepted (200)
    good_sig = "sha256=" + hmac.new(secret.encode(), raw_bytes, hashlib.sha256).hexdigest()
    assert whatsapp.verify_signature(raw_bytes, good_sig) is True
    res_good = client.post("/api/whatsapp/webhook", data=raw_bytes,
                           headers={"Content-Type": "application/json", "X-Hub-Signature-256": good_sig})
    assert res_good.status_code == 200
    assert res_good.json() == {"ok": True}


def test_wa_bridge_incoming_token_lifecycle(monkeypatch):
    """Local wa-bridge -> backend path authentication lifecycle:
    Unset secret -> 403 (fail closed)
    ALLOW_UNSIGNED_WEBHOOKS=true -> 200
    Secret set + bad/missing token -> 403
    Secret set + valid token -> 200"""
    from starlette.testclient import TestClient
    from main import app

    client = TestClient(app)
    payload = {
        "id": "true_919999000002@c.us_BRIDGE_TOKEN_TEST",
        "from": "919999000002@c.us",
        "to": "919876543210@c.us",
        "body": "Bridge test message",
        "type": "chat",
        "timestamp": 1700000000,
        "fromMe": False,
        "_senderName": "Bridge Tester"
    }

    # 1. Unset bridge secret: FAILS CLOSED (403)
    monkeypatch.delenv("BRIDGE_SECRET", raising=False)
    monkeypatch.delenv("WA_BRIDGE_TOKEN", raising=False)
    monkeypatch.delenv("ALLOW_UNSIGNED_WEBHOOKS", raising=False)
    monkeypatch.setattr(whatsapp_web, "TOKEN", "")
    res_unset = client.post("/api/whatsapp/web/incoming", json=payload)
    assert res_unset.status_code == 401
    assert "unset" in res_unset.json()["detail"]

    # 2. Dev flag ALLOW_UNSIGNED_WEBHOOKS=true: allowed (200)
    monkeypatch.setenv("ALLOW_UNSIGNED_WEBHOOKS", "true")
    res_flag = client.post("/api/whatsapp/web/incoming", json=payload)
    assert res_flag.status_code == 200
    assert res_flag.json() == {"ok": True}

    # 3. Secret set + bad/missing token: rejected (403)
    secret = "internal_bridge_secret_999"
    monkeypatch.delenv("ALLOW_UNSIGNED_WEBHOOKS", raising=False)
    monkeypatch.setenv("BRIDGE_SECRET", secret)
    monkeypatch.setattr(whatsapp_web, "TOKEN", secret)

    # Missing header
    res_missing = client.post("/api/whatsapp/web/incoming", json=payload)
    assert res_missing.status_code == 401

    # Wrong header
    res_wrong = client.post("/api/whatsapp/web/incoming", json=payload, headers={"X-Bridge-Token": "wrong_token"})
    assert res_wrong.status_code == 401

    # 4. Secret set + valid token: accepted (200)
    res_good = client.post("/api/whatsapp/web/incoming", json=payload, headers={"X-Bridge-Token": secret})
    assert res_good.status_code == 200
    assert res_good.json() == {"ok": True}


def test_whatsapp_inbound_sender_cannot_reach_owner_tools():
    """Inbound WhatsApp messages (even from a spoofed sender claiming to be the owner)
    are treated strictly with owner=False; owner tools are completely unavailable."""
    import tools

    # Spoofed sender matching owner's personal number
    spoofed_ctx = {"phone": "919999999999", "name": "Business Owner", "channel": "whatsapp", "agent_id": "main"}
    schemas = tools.schemas(spoofed_ctx)
    schema_names = {s["function"]["name"] for s in schemas}

    # Owner-only tools must NOT be present in tool schema
    assert "send_whatsapp" not in schema_names
    assert "start_whatsapp_chat" not in schema_names
    assert "recent_activity" not in schema_names
    assert "allowlist_number" not in schema_names

    # If prompt injection tricks model into calling owner tool anyway, execution is refused
    res = tools.run("send_whatsapp", {"phone": "919811111111", "message": "Malicious outbound"}, spoofed_ctx)
    assert res == "Tool 'send_whatsapp' is not available."
