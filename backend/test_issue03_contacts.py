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
