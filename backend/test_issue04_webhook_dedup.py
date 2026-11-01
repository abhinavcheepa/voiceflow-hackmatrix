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


def test_meta_webhook_endpoint_deduplication_e2e():
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


def test_whatsapp_signature_unset_fails_open_and_set_verifies(monkeypatch):
    """When WHATSAPP_APP_SECRET is unset, signature verification fails open (returns True).
    When WHATSAPP_APP_SECRET is set, invalid signatures fail closed (returns False)."""
    import hmac
    import hashlib

    raw_body = b'{"test": "payload"}'

    # 1. Secret unset: fails open
    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
    assert whatsapp.verify_signature(raw_body, None) is True
    assert whatsapp.verify_signature(raw_body, "sha256=invalid") is True

    # 2. Secret set: fails closed on invalid signature
    secret = "my_webhook_secret_key"
    monkeypatch.setenv("WHATSAPP_APP_SECRET", secret)
    assert whatsapp.verify_signature(raw_body, None) is False
    assert whatsapp.verify_signature(raw_body, "sha256=wrong_hex") is False

    # 3. Valid signature succeeds
    valid_sig = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    assert whatsapp.verify_signature(raw_body, valid_sig) is True
