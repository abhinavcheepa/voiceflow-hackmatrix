"""Tests for ISSUE-15: WhatsApp STOP / Opt-Out compliance, suppression table,
and multi-lingual keyword handling (English, Hindi, Hinglish).
"""

import pytest
from unittest.mock import AsyncMock, patch

import db
import main
import opt_out
import tools
import whatsapp


@pytest.fixture(autouse=True)
def clean_db(tmp_path, monkeypatch):
    """Ensure clean isolated SQLite database with opt_outs table."""
    test_db = str(tmp_path / "test_optout.db")
    monkeypatch.setattr(db, "DB_PATH", test_db)
    db.init()
    return test_db


@pytest.mark.parametrize("kw", [
    "STOP", "stop", "Unsubscribe", "QUIT", "optout",
    "band karo", "mat bhejo", "message mat bhejo",
    "रुको", "बंद करो", "मत भेजो"
])
@pytest.mark.anyio
async def test_inbound_stop_keywords_trigger_opt_out(kw):
    """Inbound STOP/unsubscribe keywords across English, Hinglish, and Hindi trigger opt-out."""
    phone = "919999888801"
    assert db.is_opted_out(phone) is False

    msg = {
        "id": f"wamid.OPTOUT_{hash(kw)}",
        "from": phone,
        "type": "text",
        "text": kw,
    }

    with patch("whatsapp.send_text", new_callable=AsyncMock) as mock_send:
        await whatsapp.handle(msg)
        assert mock_send.called
        sent_text = mock_send.call_args[0][1]
        assert "unsubscribed" in sent_text.lower() or "अनसब्सक्राइब" in sent_text

    # Verify phone is recorded in suppression list
    assert db.is_opted_out(phone) is True

    # Verify conversation auto_reply is disabled
    with db.connect() as conn:
        row = conn.execute("SELECT auto_reply FROM conversations WHERE phone = ?", (phone,)).fetchone()
        assert row is not None
        assert row[0] == 0


@pytest.mark.parametrize("phrase", [
    "cancel my appointment",
    "nahi chahiye receipt",
    "rok do gaadi yaha pe",
    "please halt the process for today",
    "end of conversation, thank you"
])
@pytest.mark.anyio
async def test_ambiguous_phrases_do_not_trigger_opt_out(phrase):
    """Conversational phrases containing ambiguous words must NOT trigger opt-out."""
    phone = "919999888802"
    assert db.is_opted_out(phone) is False

    msg = {
        "id": f"wamid.MSG_{hash(phrase)}",
        "from": phone,
        "type": "text",
        "text": phrase,
    }

    with patch("whatsapp.reply_to", new_callable=AsyncMock):
        await whatsapp.handle(msg)

    # Must NOT be in suppression list
    assert db.is_opted_out(phone) is False
    assert opt_out.is_opt_out_keyword(phrase) is False


@pytest.mark.anyio
async def test_suppression_table_blocks_subsequent_auto_replies():
    """Once opted out, incoming messages are stored but auto-reply is blocked."""
    phone = "919999888803"
    db.opt_out(phone, reason="user_unsubscribed")
    assert db.is_opted_out(phone) is True

    # Check db auto_reply_block helper
    block_reason = db.auto_reply_block(phone)
    assert block_reason is not None
    assert "suppression list" in block_reason or "opted out" in block_reason

    # Send incoming normal message
    msg = {
        "id": "wamid.NORMAL_AFTER_OPTOUT",
        "from": phone,
        "type": "text",
        "text": "Hello, do you have fresh mangoes?",
    }

    with patch("whatsapp.reply_to", new_callable=AsyncMock) as mock_reply:
        await whatsapp.handle(msg)
        # reply_to must NOT be called for opted-out users
        assert not mock_reply.called

    # Message must be stored in database for human review
    history = db.messages_for(phone)
    assert any("Hello, do you have fresh mangoes?" in m for m in history)


@pytest.mark.anyio
async def test_suppression_table_skips_campaign_dialing(monkeypatch):
    """Campaign execution automatically skips opted-out contacts."""
    monkeypatch.setenv("CAMPAIGN_GAP_SECONDS", "0")
    phone_ok = "919999888804"
    phone_optout = "919999888805"
    db.opt_out(phone_optout, reason="user_unsubscribed")
    assert db.is_opted_out(phone_optout) is True

    targets = [
        {"name": "Customer 1", "phone": phone_ok},
        {"name": "Customer 2", "phone": phone_optout},
    ]
    cid = db.create_campaign("Winter Offer", None, targets)
    db.set_campaign_status(cid, "running")

    with patch("vapi.configured", return_value=True), patch("vapi.create_call", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = {"id": "call-123"}
        await main._run_campaign(cid)

        # Verify only the active contact was dialed
        assert mock_call.call_count == 1
        assert mock_call.call_args[0][0] == phone_ok

    # Target table shows opted_out status for suppressed phone
    with db.connect() as conn:
        rows = conn.execute("SELECT phone, status FROM campaign_targets WHERE campaign_id = ?", (cid,)).fetchall()
        st_map = {r[0]: r[1] for r in rows}
        assert st_map[phone_ok] == "called"
        assert st_map[phone_optout] == "opted_out"


@pytest.mark.anyio
async def test_owner_tool_send_whatsapp_refuses_opted_out_recipient():
    """Agent tool call send_whatsapp refuses sending to an opted-out recipient."""
    phone = "919999888805"
    db.opt_out(phone, reason="user_unsubscribed")

    ctx = {"owner": True, "phone": "919999999999", "_turn": 1}
    res = await tools.run("send_whatsapp", {"phone": phone, "message": "Special discount for you!"}, ctx)
    assert "recipient has opted out" in res.lower() or "suppression list" in res.lower()


@pytest.mark.parametrize("kw", [
    "START", "start", "Subscribe", "unstop",
    "shuru karo", "chalu karo", "शुरू करो", "चालू करो"
])
@pytest.mark.anyio
async def test_resubscribe_start_keyword_restores_service(kw):
    """Sending START or resubscribe keywords restores auto-reply and clears suppression."""
    phone = "919999888806"
    db.opt_out(phone, reason="initial_optout")
    assert db.is_opted_out(phone) is True

    msg = {
        "id": f"wamid.START_{hash(kw)}",
        "from": phone,
        "type": "text",
        "text": kw,
    }

    with patch("whatsapp.send_text", new_callable=AsyncMock) as mock_send:
        await whatsapp.handle(msg)
        assert mock_send.called
        sent_text = mock_send.call_args[0][1]
        assert "resubscribed" in sent_text.lower() or "सब्सक्राइब" in sent_text

    # Verify suppression table is cleared for this phone
    assert db.is_opted_out(phone) is False

    # Verify conversation auto_reply is restored to 1
    with db.connect() as conn:
        row = conn.execute("SELECT auto_reply FROM conversations WHERE phone = ?", (phone,)).fetchone()
        assert row is not None
        assert row[0] == 1


def test_opt_out_normalizes_phone_across_formats():
    """Opt-out suppression matches numbers across local, international, and @c.us variants."""
    # Stored via +91 with spaces
    db.opt_out("+91 98765 43210")

    # Only 1 normalized row must be in the DB
    with db.connect() as conn:
        rows = conn.execute("SELECT phone FROM opt_outs").fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "919876543210"

    # All variants hit the same row
    assert db.is_opted_out("09876543210") is True
    assert db.is_opted_out("+919876543210") is True
    assert db.is_opted_out("919876543210@c.us") is True
    assert db.is_opted_out("9876543210") is True
