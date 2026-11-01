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
    "STOP", "stop", "Unsubscribe", "cancel", "QUIT", "halt",
    "band karo", "mat bhejo", "rok do", "message mat bhejo", "nahi chahiye",
    "रुको", "बंद करो", "मत भेजो", "रोक दो"
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


@pytest.mark.anyio
async def test_suppression_table_blocks_subsequent_auto_replies():
    """Once opted out, incoming messages are stored but auto-reply is blocked."""
    phone = "919999888802"
    db.opt_out(phone, reason="unit_test")
    assert db.is_opted_out(phone) is True

    # Verify auto_reply_block diagnoses the suppression list
    block_reason = db.auto_reply_block(phone)
    assert block_reason is not None
    assert "opted out" in block_reason.lower() or "suppression" in block_reason.lower()

    msg = {
        "id": "wamid.AFTER_OPTOUT_1",
        "from": phone,
        "type": "text",
        "text": "What are your store hours?",
    }

    with patch("whatsapp.reply_to", new_callable=AsyncMock) as mock_reply:
        await whatsapp.handle(msg)
        # reply_to must NOT be called for suppressed sender
        mock_reply.assert_not_called()

    # Message must still be recorded in conversation history for the human owner
    with db.connect() as conn:
        rows = conn.execute("SELECT body FROM messages WHERE conversation_id = (SELECT id FROM conversations WHERE phone = ?)", (phone,)).fetchall()
        assert any("What are your store hours?" in r[0] for r in rows)


@pytest.mark.anyio
async def test_suppression_table_skips_campaign_dialing():
    """Campaign targets matching opted-out numbers in suppression list are skipped without dialing."""
    opted_out_phone = "919999888803"
    valid_phone = "919999888804"

    db.opt_out(opted_out_phone, reason="pre_optout")

    cid = db.create_campaign("Promo Campaign", None, [
        {"name": "Suppressed Lead", "phone": opted_out_phone},
        {"name": "Valid Lead", "phone": valid_phone},
    ])
    db.set_campaign_status(cid, "running")

    with patch("vapi.configured", return_value=True), patch("vapi.create_call", new_callable=AsyncMock) as mock_vapi:
        mock_vapi.return_value = {"id": "call_12345"}
        # Run campaign
        await main._run_campaign(cid)

        # Only the valid target should have been dialed
        assert mock_vapi.call_count == 1
        dialed_phone = mock_vapi.call_args[0][0]
        assert dialed_phone == valid_phone

    targets = db.get_campaign_targets(cid)
    t_opt = next(t for t in targets if t["phone"] == opted_out_phone)
    t_val = next(t for t in targets if t["phone"] == valid_phone)

    assert t_opt["status"] == "opted_out"
    assert "suppression" in t_opt["error"].lower() or "opted out" in t_opt["error"].lower()
    assert t_val["status"] == "called"


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
    """Opt-out suppression matches numbers across local, international, and spaced formats."""
    # Opt out with spaced format
    db.opt_out("09876543210")
    # Must match canonical 919876543210 and with + prefix
    assert db.is_opted_out("919876543210") is True
    assert db.is_opted_out("+91 98765 43210") is True
    assert db.is_opted_out("9876543210") is True
