"""Tests for WhatsApp allowlist, rate limits, status endpoint, and assistant controls."""
import asyncio
import os
import pytest
from unittest.mock import AsyncMock, patch
from starlette.testclient import TestClient

import auth
import brain
import db
from main import app
import tools
import voice
import whatsapp


@pytest.fixture(autouse=True)
def clean_env(tmp_path, monkeypatch):
    test_db = str(tmp_path / "test_allowlist.db")
    monkeypatch.setattr(db, "DB_PATH", test_db)
    db.init()
    # Fast tests: bypass artificial typing sleep
    monkeypatch.setenv("TYPING_DELAY_MIN", "0")
    monkeypatch.setenv("TYPING_DELAY_MAX", "0")
    monkeypatch.setenv("AUTO_REPLY_MODE", "allowlist")
    whatsapp.rate_limiter.reset()
    return test_db


@pytest.fixture
def auth_headers(monkeypatch):
    token = "test_api_token_allowlist"
    monkeypatch.setattr(auth, "API_TOKEN", token)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.anyio
async def test_allowed_number_gets_reply():
    """An allowlisted number receives an automated reply."""
    phone = "919876543210"
    db.set_allowlist([phone])
    assert db.allowed(phone) is True

    msg = {
        "id": "wamid.ALLOW_01",
        "from": phone,
        "type": "text",
        "text": "Hello, is the store open today?",
    }

    with patch("brain.reply", new_callable=AsyncMock) as mock_llm, \
         patch("whatsapp.send_text", new_callable=AsyncMock) as mock_send:
        mock_llm.return_value = "Yes, we are open until 8 PM!"
        await whatsapp.handle(msg)

        assert mock_llm.called
        assert mock_send.called
        assert mock_send.call_args[0][0] == phone
        assert "Yes, we are open" in mock_send.call_args[0][1]


@pytest.mark.anyio
async def test_non_allowed_stored_with_needs_reply_no_llm_call():
    """Non-allowlisted number: message stored with needs_reply=True; NO LLM, NO send, NO TTS."""
    phone_allowed = "919876543210"
    phone_stranger = "919111122223"
    db.set_allowlist([phone_allowed])

    assert db.allowed(phone_stranger) is False
    assert db.auto_reply_block(phone_stranger) is not None

    msg = {
        "id": "wamid.STRANGER_01",
        "from": phone_stranger,
        "type": "text",
        "text": "What are your services?",
    }

    with patch("brain.reply", new_callable=AsyncMock) as mock_llm, \
         patch("whatsapp.send_text", new_callable=AsyncMock) as mock_send, \
         patch("voice.synthesize", new_callable=AsyncMock) as mock_tts:
        await whatsapp.handle(msg)

        # STRICT VERIFICATION: NO LLM, NO send, NO TTS
        assert not mock_llm.called
        assert not mock_send.called
        assert not mock_tts.called

    # Check database: message is stored for owner and marked with needs_reply=1
    with db.connect() as conn:
        row = conn.execute("""
            SELECT m.body, m.needs_reply
            FROM messages m
            JOIN conversations c ON m.conversation_id = c.id
            WHERE c.phone = ?
        """, (phone_stranger,)).fetchone()
        assert row is not None
        assert row[0] == "What are your services?"
        assert row[1] == 1


@pytest.mark.anyio
async def test_groups_broadcasts_and_status_updates_are_ignored():
    """Groups (@g.us), broadcasts (@broadcast), and status updates are never auto-replied."""
    group_msg = {
        "id": "wamid.GRP_01",
        "from": "1203630248291829@g.us",
        "type": "text",
        "text": "Hey everyone!",
    }
    status_msg = {
        "id": "wamid.STATUS_01",
        "from": "status@broadcast",
        "type": "status",
        "text": "Daily status update",
    }

    with patch("whatsapp.reply_to", new_callable=AsyncMock) as mock_reply:
        await whatsapp.handle(group_msg)
        await whatsapp.handle(status_msg)
        assert not mock_reply.called


def test_number_format_variants_match_allowlist():
    """Allowlist normalizes and matches +91, 0-prefix, space-separated numbers."""
    db.set_allowlist(["+91 98765 43210"])

    allow = db.get_allowlist()
    assert allow == ["919876543210"]

    assert db.allowed("919876543210") is True
    assert db.allowed("09876543210") is True
    assert db.allowed("+919876543210") is True
    assert db.allowed("9876543210") is True


@pytest.mark.anyio
async def test_stop_suppression_beats_allowlist():
    """STOP suppression always overrides allowlist."""
    phone = "919876543210"
    db.set_allowlist([phone])
    assert db.allowed(phone) is True

    # User opts out
    db.opt_out(phone, reason="user_stop")
    assert db.is_opted_out(phone) is True

    # auto_reply_block immediately reports opted out
    block_reason = db.auto_reply_block(phone)
    assert block_reason is not None
    assert "suppression list" in block_reason

    msg = {
        "id": "wamid.OPTOUT_MSG",
        "from": phone,
        "type": "text",
        "text": "Is this working?",
    }

    with patch("whatsapp.reply_to", new_callable=AsyncMock) as mock_reply:
        await whatsapp.handle(msg)
        assert not mock_reply.called


@pytest.mark.anyio
async def test_cooldown_and_daily_cap(monkeypatch):
    """Rate limiter enforces per-number reply cooldown and daily send cap."""
    phone = "919876543210"
    db.set_allowlist([phone])

    # 1. Per-number cooldown
    monkeypatch.setenv("REPLY_COOLDOWN_SECONDS", "10")
    assert whatsapp.rate_limiter.check(phone) is None
    whatsapp.rate_limiter.record_send(phone)

    # Immediately check again: should throttle
    err = whatsapp.rate_limiter.check(phone)
    assert err is not None
    assert "cooldown active" in err

    # Reset
    whatsapp.rate_limiter.reset()
    assert whatsapp.rate_limiter.check(phone) is None

    # 2. Daily send cap
    monkeypatch.setenv("MAX_DAILY_AUTO_REPLIES", "2")
    whatsapp.rate_limiter.record_send("919000000001")
    whatsapp.rate_limiter.record_send("919000000002")

    err_daily = whatsapp.rate_limiter.check("919000000003")
    assert err_daily is not None
    assert "daily auto-reply cap reached" in err_daily


def test_assistant_add_and_remove_with_confirmation():
    """Assistant tool handles 'Rohit ko allow karo' and 'Amit ko hata do' with confirmation."""
    db.touch_contact("919876543210", "Rohit")
    db.touch_contact("919876543211", "Amit")

    ctx = {"owner": True, "_turn": 1}

    # 1. 'Rohit ko allow karo' - Turn 1 prompts confirmation
    out1 = tools.run("auto_reply_settings", {"action": "allow", "name": "Rohit"}, ctx)
    assert "NOT SENT YET" in out1
    assert "Rohit" in out1 and "pakka" in out1
    assert "919876543210" not in db.get_allowlist()

    # Turn 2: owner says yes -> added to allowlist
    ctx["_turn"] = 2
    out2 = tools.run("auto_reply_settings", {"action": "allow", "name": "Rohit"}, ctx)
    assert "Ho gaya" in out2
    assert "919876543210" in db.get_allowlist()

    # 2. 'Amit ko allow karo' and then 'Amit ko hata do'
    db.add_to_allowlist("919876543211")
    assert "919876543211" in db.get_allowlist()

    ctx["_turn"] = 3
    out3 = tools.run("auto_reply_settings", {"action": "remove", "name": "Amit"}, ctx)
    assert "NOT SENT YET" in out3
    assert "Amit" in out3 and "pakka" in out3

    # Turn 4: owner confirms remove
    ctx["_turn"] = 4
    out4 = tools.run("auto_reply_settings", {"action": "remove", "name": "Amit"}, ctx)
    assert "Ho gaya" in out4
    assert "919876543211" not in db.get_allowlist()


@pytest.mark.anyio
async def test_outbound_by_name_does_not_add_to_allowlist():
    """Starting a chat or messaging a contact does NOT silently add them to the allowlist."""
    db.touch_contact("919888877777", "Sunita")
    db.set_allowlist(["919000000001"])

    ctx = {"owner": True, "_turn": 1}
    with patch("whatsapp.send_text", new_callable=AsyncMock), \
         patch("whatsapp.configured", return_value=True):
        # Turn 1: parks
        res1 = await tools.run("start_whatsapp_chat", {"name": "Sunita", "goal": "Consultation", "opening_message": "Namaste Sunita ji"}, ctx)
        assert "NOT SENT YET" in res1
        # Turn 2: confirms
        ctx["_turn"] = 2
        res2 = await tools.approve_pending(ctx)
        assert "Ho gaya" in res2

    # Sunita must NOT be in the allowlist
    assert "919888877777" not in db.get_allowlist()


def test_whatsapp_status_endpoint_auth_protected(auth_headers):
    """GET /api/whatsapp/status is auth-protected and returns expected structure."""
    client = TestClient(app)

    # Unauthenticated rejected with 401
    res_unauth = client.get("/api/whatsapp/status")
    assert res_unauth.status_code == 401

    # Authenticated succeeds
    res = client.get("/api/whatsapp/status", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert "provider" in data
    assert "bridge_reachable" in data
    assert "qr_state" in data
    assert "last_incoming_time" in data
