import sqlite3
import pytest
import db
import migrate_phone_normalization as mpn


@pytest.fixture(autouse=True)
def setup_clean_db(tmp_path, monkeypatch):
    test_db = str(tmp_path / "test_phones.db")
    monkeypatch.setattr(db, "DB_PATH", test_db)
    db.init()
    yield test_db


def test_normalize_phone_variations():
    assert db.normalize_phone("9876543210") == "919876543210"
    assert db.normalize_phone("09876543210") == "919876543210"
    assert db.normalize_phone("+91 98765 43210") == "919876543210"
    assert db.normalize_phone("00919876543210") == "919876543210"
    assert db.normalize_phone("919876543210") == "919876543210"
    # Special strings
    assert db.normalize_phone("Web call") == "Web call"
    assert db.normalize_phone("unknown") == "unknown"
    assert db.normalize_phone("") == ""
    assert db.normalize_phone(None) == ""


def test_writes_and_lookups_are_normalized():
    # Write contact with unnormalized format
    db.touch_contact("+91 98765 43210", "Amit")
    contacts = db.get_contacts()
    assert len(contacts) == 1
    assert contacts[0]["phone"] == "919876543210"

    # Message added with leading 0 format
    msg_id = db.add_message("09876543210", "them", "text", "Hello VoiceFlow")
    assert msg_id is not None

    # Conversation phone in DB should be normalized
    with db.connect() as conn:
        conv = conn.execute("SELECT phone FROM conversations").fetchone()
        assert conv["phone"] == "919876543210"

    # History lookup with +91 format finds the message
    hist = db.get_history("+919876543210")
    assert len(hist) == 1
    assert hist[0]["content"] == "Hello VoiceFlow"


def test_migration_dry_run_does_not_modify_db(tmp_path):
    migration_db = str(tmp_path / "dry_run.db")
    conn = sqlite3.connect(migration_db)
    conn.execute("CREATE TABLE contacts (id INTEGER PRIMARY KEY, name TEXT, phone TEXT UNIQUE, created_at TEXT, last_seen_at TEXT)")
    conn.execute("CREATE TABLE conversations (id INTEGER PRIMARY KEY, name TEXT, phone TEXT UNIQUE, language TEXT, updated_at TEXT)")
    conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, conversation_id INTEGER, sender TEXT, kind TEXT, body TEXT, created_at TEXT)")
    conn.execute("CREATE TABLE calls (id TEXT PRIMARY KEY, phone TEXT)")
    # Insert unnormalized contact & conversation
    conn.execute("INSERT INTO contacts VALUES (1, 'Rohan', '+919876543210', '2026-01-01', '2026-01-02')")
    conn.execute("INSERT INTO conversations VALUES (10, 'Rohan', '+919876543210', 'Hindi', '2026-01-01')")
    conn.commit()
    conn.close()

    actions = mpn.plan_and_migrate(migration_db, dry_run=True)
    assert len(actions["contacts_updated"]) == 1
    assert actions["contacts_updated"][0]["new_phone"] == mpn.mask_phone("919876543210")
    assert len(actions["conversations_updated"]) == 1

    # Verify dry-run did not alter the database
    conn = sqlite3.connect(migration_db)
    row = conn.execute("SELECT phone FROM contacts WHERE id = 1").fetchone()
    assert row[0] == "+919876543210"
    conn.close()


def test_migration_apply_merges_duplicates_and_repoints_messages(tmp_path):
    migration_db = str(tmp_path / "apply_merge.db")
    conn = sqlite3.connect(migration_db)
    conn.execute("CREATE TABLE contacts (id INTEGER PRIMARY KEY, name TEXT, phone TEXT UNIQUE, created_at TEXT, last_seen_at TEXT)")
    conn.execute("CREATE TABLE conversations (id INTEGER PRIMARY KEY, name TEXT, phone TEXT UNIQUE, language TEXT, updated_at TEXT)")
    conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, conversation_id INTEGER, sender TEXT, kind TEXT, body TEXT, created_at TEXT)")
    conn.execute("CREATE TABLE calls (id TEXT PRIMARY KEY, phone TEXT)")

    # 2 duplicate contacts: #1 '+919876543210', #2 '09876543210'
    conn.execute("INSERT INTO contacts VALUES (1, 'Rohan Early', '+919876543210', '2026-01-01', '2026-01-02')")
    conn.execute("INSERT INTO contacts VALUES (2, 'Rohan Late', '09876543210', '2026-01-03', '2026-01-04')")

    # 2 duplicate conversations with messages
    conn.execute("INSERT INTO conversations VALUES (10, 'Rohan Early', '+919876543210', 'Hindi', '2026-01-01')")
    conn.execute("INSERT INTO conversations VALUES (20, 'Rohan Late', '09876543210', 'Hindi', '2026-01-05')")
    conn.execute("INSERT INTO messages VALUES (101, 10, 'them', 'text', 'msg from early conv', '2026-01-01')")
    conn.execute("INSERT INTO messages VALUES (102, 20, 'them', 'text', 'msg from late conv', '2026-01-05')")
    conn.commit()
    conn.close()

    actions = mpn.plan_and_migrate(migration_db, dry_run=False)
    assert len(actions["contacts_merged"]) == 1
    assert len(actions["conversations_merged"]) == 1
    assert len(actions["messages_repointed"]) == 1
    assert actions["messages_repointed"][0]["count"] == 1

    # Verify result in DB
    conn = sqlite3.connect(migration_db)
    contacts = conn.execute("SELECT id, name, phone, last_seen_at FROM contacts").fetchall()
    assert len(contacts) == 1
    assert contacts[0][0] == 1
    assert contacts[0][2] == "919876543210"
    assert contacts[0][3] == "2026-01-04"  # merged latest seen

    convs = conn.execute("SELECT id, phone, updated_at FROM conversations").fetchall()
    assert len(convs) == 1
    assert convs[0][0] == 10
    assert convs[0][1] == "919876543210"
    assert convs[0][2] == "2026-01-05"  # merged latest updated

    # Both messages must now point to conv 10
    messages = conn.execute("SELECT id, conversation_id FROM messages ORDER BY id").fetchall()
    assert len(messages) == 2
    assert messages[0][1] == 10
    assert messages[1][1] == 10
    conn.close()

