"""Tests for ISSUE-03 and ISSUE-07: Contact lookup matching algorithm and scalability."""

import pytest
import db
import tools


@pytest.fixture(autouse=True)
def seed_contacts_for_test(monkeypatch):
    """Seed clean contacts in a temp DB for each test."""
    import tempfile, os
    temp_dir = tempfile.mkdtemp()
    test_db = os.path.join(temp_dir, "test_contacts.db")
    monkeypatch.setattr(db, "DB_PATH", test_db)
    db.init()


def test_rohit_sharma_does_not_resolve_to_amit_sharma():
    """Query 'Rohit Sharma' must NOT match saved 'Amit Sharma' through surname alone."""
    db.touch_contact("919811111111", name="Amit Sharma")

    hits = tools.find_contacts("Rohit Sharma")
    # Must NOT return Amit Sharma
    matched_names = [name for _, name, _ in hits]
    assert "Amit Sharma" not in matched_names, f"Wrong contact selected: {hits}"


def test_first_name_alone_resolves():
    """Query 'Amit' alone resolves to 'Amit Sharma'."""
    db.touch_contact("919811111111", name="Amit Sharma")

    hits = tools.find_contacts("Amit")
    assert hits, "Must find Amit Sharma"
    assert hits[0][1] == "Amit Sharma"


def test_two_matching_contacts_prompts_disambiguation():
    """When two Amits exist, difference < 0.05 triggers 'More than one contact matches'."""
    db.touch_contact("919811111111", name="Amit Sharma")
    db.touch_contact("919822222222", name="Amit Verma")

    phone, who, error = tools._resolve({"name": "Amit"}, {})
    assert phone == ""
    assert error is not None
    assert "More than one contact matches" in error


def test_devanagari_and_roman_spellings_match():
    """Devanagari spoken name matches Latin saved contact."""
    db.touch_contact("919833333333", name="Kanishk")

    # "कनिष्क" should romanize to kanishk and match
    hits = tools.find_contacts("कनिष्क")
    assert hits, "Devanagari query must match Latin contact"
    assert hits[0][1] == "Kanishk"


def test_scalability_beyond_2000_contacts():
    """Contacts beyond the old 2000 limit are searchable and found."""
    # Seed 2500 contacts
    now_ts = "2026-01-01T00:00:00+00:00"
    with db.connect() as conn:
        for i in range(2500):
            conn.execute(
                "INSERT INTO contacts (name, phone, created_at, last_seen_at) VALUES (?, ?, ?, ?)",
                (f"Contact {i}", f"91980000{i:04d}", now_ts, now_ts),
            )
        # Put target at index 2499
        conn.execute(
            "INSERT INTO contacts (name, phone, created_at, last_seen_at) VALUES ('Target Person', '919999888877', ?, ?)",
            (now_ts, now_ts),
        )

    hits = tools.find_contacts("Target Person")
    assert hits, "Contact beyond index 2000 must be found"
    assert hits[0][1] == "Target Person"
    assert hits[0][2] == "919999888877"
@pytest.mark.anyio
async def test_candidate_changes_between_readback_and_yes():
    """If owner switches contact before saying yes, pending action is replaced."""
    ctx = {"owner": True, "_turn": 1}
    # Turn 1: Propose send to Contact A
    res1 = tools._confirm(ctx, "send_whatsapp", {"phone": "919811111111", "message": "Hi A"}, "wa:919811111111:Hi A", "Send to A?")
    assert "NOT SENT YET" in res1
    assert ctx["_pending"]["args"]["phone"] == "919811111111"

    # Turn 2: Owner changes mind to Contact B before confirming
    ctx["_turn"] = 2
    res2 = tools._confirm(ctx, "send_whatsapp", {"phone": "919822222222", "message": "Hi B"}, "wa:919822222222:Hi B", "Send to B?")
    assert "NOT SENT YET" in res2
    assert ctx["_pending"]["args"]["phone"] == "919822222222"

    # Now owner says yes on turn 3
    ctx["_turn"] = 3
    from unittest.mock import AsyncMock, patch
    with patch("tools._deliver", new_callable=AsyncMock) as mock_del:
        with patch("tools._whatsapp_ready", return_value=None):
            await tools.approve_pending(ctx)
            # Must deliver to Contact B, NEVER Contact A
            assert mock_del.call_count == 1
            assert mock_del.call_args[0][0] == "919822222222"


@pytest.mark.anyio
async def test_stale_pending_send_expiry():
    """A pending send parked many turns ago or long ago expires and is not sent."""
    ctx = {"owner": True, "_turn": 1}
    tools._confirm(ctx, "send_whatsapp", {"phone": "919811111111", "message": "Hi"}, "wa:919811111111:Hi A", "Send?")

    # Turn jumps past MAX_PENDING_TURNS (e.g. turn 4 > 1 + 2)
    ctx["_turn"] = 5
    result = await tools.approve_pending(ctx)
    assert result is None
    assert "_pending" not in ctx


@pytest.mark.anyio
async def test_yes_after_different_contact_resolved():
    """Resolving a new contact and saying yes delivers strictly to the resolved contact."""
    db.touch_contact("919811111111", name="Rohan Sharma")
    db.touch_contact("919822222222", name="Kunal Verma")

    ctx = {"owner": True, "_turn": 1}
    phone, who, _ = tools._resolve({"name": "Kunal"}, ctx)
    tools._confirm(ctx, "send_whatsapp", {"phone": phone, "message": "Invoice"}, f"wa:{phone}:Invoice", f"Send to {who}?")

    ctx["_turn"] = 2
    from unittest.mock import AsyncMock, patch
    with patch("tools._deliver", new_callable=AsyncMock) as mock_del:
        with patch("tools._whatsapp_ready", return_value=None):
            await tools.approve_pending(ctx)
            assert mock_del.call_count == 1
            assert mock_del.call_args[0][0] == "919822222222"
