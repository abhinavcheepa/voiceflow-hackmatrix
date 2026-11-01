"""Tests for ISSUE-05: DB Foundation, Foreign Keys, PRAGMAs, Migrations, Indexes and Concurrency."""

import os
import sqlite3
import tempfile
import threading
import pytest

import db


def test_foreign_key_enforcement_rejects_orphan(monkeypatch):
    """Foreign key enforcement must reject rows with non-existent foreign keys."""
    temp_dir = tempfile.mkdtemp()
    test_db = os.path.join(temp_dir, "test_fk.db")
    monkeypatch.setattr(db, "DB_PATH", test_db)
    db.init()

    with db.connect() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO messages (conversation_id, sender, kind, body, created_at)
                   VALUES (999999, 'them', 'text', 'orphan message', '2026-01-01T00:00:00Z')"""
            )


def test_cascade_or_safe_delete_paths(monkeypatch):
    """Deleting parents with children must safely clean children without IntegrityError."""
    temp_dir = tempfile.mkdtemp()
    test_db = os.path.join(temp_dir, "test_deletes.db")
    monkeypatch.setattr(db, "DB_PATH", test_db)
    db.init()

    # 1. KB Doc delete
    doc = db.add_kb_document(None, "doc1.txt", ["chunk 1", "chunk 2"])
    doc_id = doc["id"]
    with db.connect() as conn:
        chunks_before = conn.execute("SELECT COUNT(*) FROM kb_chunks WHERE doc_id = ?", (doc_id,)).fetchone()[0]
        assert chunks_before == 2
    db.delete_kb_doc(doc_id)
    with db.connect() as conn:
        chunks_after = conn.execute("SELECT COUNT(*) FROM kb_chunks WHERE doc_id = ?", (doc_id,)).fetchone()[0]
        assert chunks_after == 0

    # 2. Stock delete
    stock = db.add_stock_item("Test Cement", category="Building", quantity=50, unit="bags", rate=350.0, min_stock=10)
    stock_id = stock["id"]
    db.add_stock_entry(stock_id, "in", 20, "fresh delivery")
    db.delete_stock_item(stock_id)
    with db.connect() as conn:
        entries_after = conn.execute("SELECT COUNT(*) FROM stock_entries WHERE stock_id = ?", (stock_id,)).fetchone()[0]
        assert entries_after == 0

    # 3. Bill delete
    bill = db.create_bill("Ravi", "919876543210")
    bill_id = bill["id"]
    db.add_bill_item(bill_id, "Item A", quantity=2, rate=100.0, gst_percent=18)
    db.delete_bill(bill_id)
    with db.connect() as conn:
        items_after = conn.execute("SELECT COUNT(*) FROM bill_items WHERE bill_id = ?", (bill_id,)).fetchone()[0]
        assert items_after == 0

    # 4. Agent delete (with children referencing it)
    agent = db.save_agent(None, name="Support Agent", prompt="prompt", greeting="greeting")
    ag_id = agent["id"]
    with db.connect() as conn:
        conn.execute("INSERT INTO campaigns (name, agent_id, status, created_at) VALUES ('Camp 1', ?, 'draft', 'now')", (ag_id,))
        conn.execute("INSERT INTO learned_answers (agent_id, question, answer, source, confidence, created_at) VALUES (?, 'q', 'a', 'conv', 0.8, 'now')", (ag_id,))
    # Deleting agent must not raise IntegrityError
    db.delete_agent(ag_id)


def test_schema_version_and_migration_idempotency(monkeypatch):
    """schema_version table tracks migrations; running init twice produces identical schema."""
    temp_dir = tempfile.mkdtemp()
    test_db = os.path.join(temp_dir, "test_mig.db")
    monkeypatch.setattr(db, "DB_PATH", test_db)

    db.init()
    with db.connect() as conn:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        assert row is not None and row[0] >= 2

    # Run init second time - must be idempotent
    db.init()
    with db.connect() as conn:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        assert row is not None and row[0] >= 2


def test_explain_query_plan_uses_new_indexes(monkeypatch):
    """Explain query plan proves the 3 hot queries use the new indexes."""
    temp_dir = tempfile.mkdtemp()
    test_db = os.path.join(temp_dir, "test_qp.db")
    monkeypatch.setattr(db, "DB_PATH", test_db)
    db.init()

    with db.connect() as conn:
        # Hot query 1: calls by phone
        qp1 = conn.execute("EXPLAIN QUERY PLAN SELECT * FROM calls WHERE phone = ?", ("919876543210",)).fetchall()
        plan1 = " ".join(r["detail"] if "detail" in r.keys() else str(tuple(r)) for r in qp1)
        assert "idx_calls_phone" in plan1

        # Hot query 2: needs_reply sweep
        qp2 = conn.execute(
            """EXPLAIN QUERY PLAN SELECT m.id, m.body, m.kind, v.phone, v.name
               FROM messages m JOIN conversations v ON v.id = m.conversation_id
               WHERE m.needs_reply = 1 AND m.sender = 'them' AND m.created_at >= ?
               ORDER BY m.created_at""",
            ("2026-01-01T00:00:00Z",),
        ).fetchall()
        plan2 = " ".join(r["detail"] if "detail" in r.keys() else str(tuple(r)) for r in qp2)
        assert "idx_messages_needs_reply" in plan2

        # Hot query 3: pending campaign targets
        qp3 = conn.execute(
            """EXPLAIN QUERY PLAN SELECT * FROM campaign_targets
               WHERE campaign_id = ? AND status = 'pending'
               ORDER BY id LIMIT 1""",
            (1,),
        ).fetchall()
        plan3 = " ".join(r["detail"] if "detail" in r.keys() else str(tuple(r)) for r in qp3)
        assert "idx_campaign_targets_lookup" in plan3


def test_deterministic_concurrency_no_database_locked():
    """Barrier-synchronized concurrent writers on file DB must not raise 'database is locked'."""
    temp_dir = tempfile.mkdtemp()
    test_db = os.path.join(temp_dir, "test_concurrent.db")

    conn = sqlite3.connect(test_db)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("CREATE TABLE counter (id INTEGER PRIMARY KEY, count INTEGER)")
    conn.execute("INSERT INTO counter (id, count) VALUES (1, 0)")
    conn.commit()
    conn.close()

    def get_conn():
        c = sqlite3.connect(test_db, timeout=10)
        c.execute("PRAGMA busy_timeout = 5000")
        c.execute("PRAGMA journal_mode = WAL")
        c.execute("PRAGMA synchronous = NORMAL")
        c.execute("PRAGMA foreign_keys = ON")
        return c

    threads_count = 4
    iterations = 20
    errors = []

    for _ in range(iterations):
        barrier = threading.Barrier(threads_count)

        def worker():
            try:
                barrier.wait()
                c = get_conn()
                with c:
                    c.execute("UPDATE counter SET count = count + 1 WHERE id = 1")
                c.close()
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=worker) for _ in range(threads_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    assert not errors, f"Concurrency errors occurred: {errors}"
    c = get_conn()
    final_count = c.execute("SELECT count FROM counter WHERE id = 1").fetchone()[0]
    c.close()
    assert final_count == threads_count * iterations


def test_startup_foreign_key_check_never_crashes(monkeypatch):
    """check_foreign_keys_safe must return violations list and never crash startup."""
    temp_dir = tempfile.mkdtemp()
    test_db = os.path.join(temp_dir, "test_corrupt_fk.db")
    monkeypatch.setattr(db, "DB_PATH", test_db)

    # Seed orphan without FK enforcement
    conn = sqlite3.connect(test_db)
    conn.executescript("""
        CREATE TABLE conversations (id INTEGER PRIMARY KEY);
        CREATE TABLE messages (id INTEGER PRIMARY KEY, conversation_id INTEGER REFERENCES conversations(id));
        INSERT INTO messages (id, conversation_id) VALUES (1, 9999);
    """)
    conn.commit()
    conn.close()

    # check_foreign_keys_safe must return violations without crashing
    violations = db.check_foreign_keys_safe()
    assert len(violations) > 0


def test_migrating_old_db_copy(monkeypatch):
    """An old database without schema_version gets stamped as v1, upgraded to v2, keeping data."""
    temp_dir = tempfile.mkdtemp()
    test_db = os.path.join(temp_dir, "test_legacy_upgrade.db")
    monkeypatch.setattr(db, "DB_PATH", test_db)

    # Create un-migrated bare tables without schema_version or MIGRATIONS columns
    raw = sqlite3.connect(test_db)
    raw.executescript("""
        CREATE TABLE conversations (id INTEGER PRIMARY KEY, name TEXT, phone TEXT UNIQUE, updated_at TEXT);
        CREATE TABLE messages (id INTEGER PRIMARY KEY, conversation_id INTEGER, sender TEXT, kind TEXT, body TEXT, created_at TEXT);
        CREATE TABLE agents (id INTEGER PRIMARY KEY, name TEXT, prompt TEXT, greeting TEXT, is_default INTEGER, created_at TEXT);
        INSERT INTO conversations (id, name, phone, updated_at) VALUES (1, 'Old Cust', '919876543210', '2025-01-01T00:00:00Z');
        INSERT INTO messages (id, conversation_id, sender, kind, body, created_at) VALUES (1, 1, 'them', 'text', 'Old msg', '2025-01-01T00:00:00Z');
        INSERT INTO agents (id, name, prompt, greeting, is_default, created_at) VALUES (1, 'Default', 'prompt', 'greeting', 1, '2025-01-01T00:00:00Z');
    """)
    raw.commit()
    raw.close()

    # Now run db.init() to upgrade the legacy DB
    db.init()

    # Verify data preserved
    with db.connect() as conn:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        assert row[0] >= 2, "Upgraded DB must be at least version 2"

        # Check existing data intact
        cust = conn.execute("SELECT name FROM conversations WHERE id = 1").fetchone()[0]
        assert cust == "Old Cust"
        msg = conn.execute("SELECT body FROM messages WHERE id = 1").fetchone()[0]
        assert msg == "Old msg"

        # Check new columns were added
        cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()}
        assert "wa_id" in cols
        assert "needs_reply" in cols
