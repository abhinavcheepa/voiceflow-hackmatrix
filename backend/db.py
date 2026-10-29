"""SQLite storage for calls and WhatsApp conversations.

Plain sqlite3 — no ORM. One file on disk, one connection per request.
Swap the DSN for Postgres when there's a reason to; nothing here needs it yet.
"""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

DB_PATH = os.getenv("DB_PATH") or os.getenv("DATABASE_PATH") or os.path.join(os.path.dirname(__file__), "voiceflow.db")
if os.path.dirname(DB_PATH):
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id             TEXT PRIMARY KEY,
    vapi_call_id   TEXT UNIQUE,
    name           TEXT NOT NULL DEFAULT 'Unknown',
    phone          TEXT NOT NULL,
    language       TEXT NOT NULL DEFAULT 'Hindi',
    intent         TEXT NOT NULL DEFAULT 'General enquiry',
    agent_id       INTEGER,
    duration_sec   INTEGER NOT NULL DEFAULT 0,
    outcome        TEXT NOT NULL DEFAULT 'Answered',
    status         TEXT NOT NULL DEFAULT 'ended',
    transcript     TEXT,
    recording_url  TEXT,
    started_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    phone      TEXT NOT NULL UNIQUE,
    language   TEXT NOT NULL DEFAULT 'Hindi',
    auto_reply INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id),
    sender          TEXT NOT NULL CHECK (sender IN ('us', 'them')),
    kind            TEXT NOT NULL CHECK (kind IN ('text', 'voice')),
    body            TEXT NOT NULL,
    seconds         INTEGER,
    read            INTEGER NOT NULL DEFAULT 0,
    failed          INTEGER NOT NULL DEFAULT 0,
    needs_reply     INTEGER NOT NULL DEFAULT 0,
    wa_id           TEXT UNIQUE,
    created_at      TEXT NOT NULL
);


CREATE TABLE IF NOT EXISTS cloned_audios (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    clone_id    TEXT NOT NULL,
    text        TEXT NOT NULL,
    filename    TEXT NOT NULL,
    filepath    TEXT NOT NULL,
    size_bytes  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    prompt     TEXT NOT NULL,
    greeting   TEXT NOT NULL,
    language   TEXT NOT NULL DEFAULT 'Hindi',
    voice_id   TEXT,
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contacts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT,
    phone        TEXT NOT NULL UNIQUE,
    email        TEXT,
    status       TEXT NOT NULL DEFAULT 'New',
    notes        TEXT,
    created_at   TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS campaigns (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    agent_id   INTEGER REFERENCES agents(id),
    status     TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS campaign_targets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
    name        TEXT,
    phone       TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    call_id     TEXT,
    error       TEXT
);

CREATE INDEX IF NOT EXISTS idx_targets_campaign ON campaign_targets(campaign_id, status);
CREATE INDEX IF NOT EXISTS idx_contacts_seen    ON contacts(last_seen_at);

CREATE INDEX IF NOT EXISTS idx_calls_started  ON calls(started_at);
CREATE INDEX IF NOT EXISTS idx_msgs_conv      ON messages(conversation_id, created_at);

-- Knowledge base: uploaded reference files the agent answers from. agent_id
-- NULL means the document applies to every agent.
CREATE TABLE IF NOT EXISTS kb_docs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id   INTEGER,
    filename   TEXT NOT NULL,
    chunks     INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kb_chunks (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id   INTEGER NOT NULL REFERENCES kb_docs(id) ON DELETE CASCADE,
    agent_id INTEGER,
    text     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kb_chunks_agent ON kb_chunks(agent_id);

-- Bookings the agent actually recorded mid-conversation via a tool call. This
-- is the difference between "we'll confirm shortly" and a real entry a human
-- can act on. status: new | confirmed | cancelled.
CREATE TABLE IF NOT EXISTS bookings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    phone      TEXT,
    name       TEXT,
    item       TEXT NOT NULL,
    when_text  TEXT,
    notes      TEXT,
    channel    TEXT NOT NULL DEFAULT 'call',
    status     TEXT NOT NULL DEFAULT 'new',
    escalated  INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bookings_created ON bookings(created_at);

-- Adaptive learning: Q&A pairs the agent extracts from real conversations.
-- Owner approves before they influence future replies.
CREATE TABLE IF NOT EXISTS learned_answers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id    INTEGER REFERENCES agents(id),
    question    TEXT NOT NULL,
    answer      TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'conversation',
    confidence  REAL NOT NULL DEFAULT 0.5,
    status      TEXT NOT NULL DEFAULT 'pending',
    use_count   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    approved_at TEXT
);
-- Evals: scripted or simulated test conversations run against an agent, each
-- judged against a rubric. Runs are dry — tools describe, never write.
CREATE TABLE IF NOT EXISTS eval_cases (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    agent_id   INTEGER REFERENCES agents(id),
    mode       TEXT NOT NULL DEFAULT 'script' CHECK (mode IN ('script', 'simulate')),
    turns      TEXT,              -- script: caller lines, one per line
    persona    TEXT,              -- simulate: who the AI caller is and what they want
    max_turns  INTEGER NOT NULL DEFAULT 4,
    rubric     TEXT NOT NULL,     -- what a pass looks like, judged by the model
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS eval_runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id    INTEGER NOT NULL REFERENCES eval_cases(id),
    passed     INTEGER NOT NULL,
    reason     TEXT,
    transcript TEXT,
    tools      TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_eval_runs_case ON eval_runs(case_id, id);
CREATE INDEX IF NOT EXISTS idx_learned_agent  ON learned_answers(agent_id, status);
CREATE INDEX IF NOT EXISTS idx_learned_status ON learned_answers(status);

-- Stock inventory — items the business sells or tracks.
CREATE TABLE IF NOT EXISTS stock (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id    INTEGER,
    name        TEXT NOT NULL,
    category    TEXT,
    quantity    REAL NOT NULL DEFAULT 0,
    unit        TEXT NOT NULL DEFAULT 'pcs',
    rate        REAL NOT NULL DEFAULT 0,
    gst_percent REAL NOT NULL DEFAULT 0,
    min_stock   REAL NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stock_name ON stock(name);

-- Every stock movement — in, out, or manual adjustment.
CREATE TABLE IF NOT EXISTS stock_entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id    INTEGER NOT NULL REFERENCES stock(id),
    entry_type  TEXT NOT NULL CHECK (entry_type IN ('in', 'out', 'adjust')),
    quantity    REAL NOT NULL,
    notes       TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stock_entries_stock ON stock_entries(stock_id);

-- Bills generated during voice conversations or manually.
CREATE TABLE IF NOT EXISTS bills (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_number     TEXT NOT NULL UNIQUE,
    customer_name   TEXT,
    customer_phone  TEXT,
    agent_id        INTEGER,
    channel         TEXT NOT NULL DEFAULT 'call',
    subtotal        REAL NOT NULL DEFAULT 0,
    gst_total       REAL NOT NULL DEFAULT 0,
    total           REAL NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'draft',
    notes           TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bills_created ON bills(created_at);

-- Line items on a bill.
CREATE TABLE IF NOT EXISTS bill_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id     INTEGER NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
    stock_id    INTEGER,
    item_name   TEXT NOT NULL,
    quantity    REAL NOT NULL DEFAULT 1,
    rate        REAL NOT NULL,
    gst_percent REAL NOT NULL DEFAULT 0,
    amount      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bill_items_bill ON bill_items(bill_id);

-- Web Push subscriptions — the browser sends its PushSubscription object.
CREATE TABLE IF NOT EXISTS push_subscriptions (
    endpoint         TEXT PRIMARY KEY,
    subscription_json TEXT NOT NULL,
    created_at       TEXT NOT NULL
);

-- Scheduled / queued background jobs. Atomic claiming:
--   UPDATE ... WHERE status='pending' AND run_at<=now LIMIT 1
-- makes this safe even if two processes read the table at the same moment.
CREATE TABLE IF NOT EXISTS scheduled_jobs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type   TEXT NOT NULL,
    payload    TEXT NOT NULL DEFAULT '{}',
    status     TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','done','failed')),
    run_at     TEXT NOT NULL,
    claimed_at TEXT,
    finished_at TEXT,
    attempts   INTEGER NOT NULL DEFAULT 0,
    error      TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_pending ON scheduled_jobs(status, run_at);
"""

# Outcomes that count toward the success rate on the dashboard.
SUCCESS_OUTCOMES = ("Booked", "Lead captured", "Answered")


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def fmt_duration(seconds: int) -> str:
    return f"{seconds // 60}m {seconds % 60:02d}s"


# Columns added after the first release. CREATE TABLE IF NOT EXISTS won't add
# them to a database that already exists, so they're applied separately.
MIGRATIONS = [
    ("messages", "wa_id", "TEXT"),
    ("calls", "agent_id", "INTEGER"),
    ("conversations", "auto_reply", "INTEGER NOT NULL DEFAULT 1"),
    ("messages", "needs_reply", "INTEGER NOT NULL DEFAULT 0"),
    # Hard business facts — prices, timings, address, catalogue — kept apart from
    # the persona so the prompt can say "quote ONLY from here", which is what
    # stops the agent inventing a price it was never given.
    ("agents", "facts", "TEXT"),
    # Post-call analysis (our own, not Vapi's): a one-line summary and a 0/1
    # success flag the model assigns after reading the transcript.
    ("calls", "summary", "TEXT"),
    ("calls", "success", "INTEGER"),
    # Semantic search: each chunk's embedding vector (float32 bytes). NULL for
    # chunks stored before embeddings were added — search falls back to BM25
    # for those, so nothing breaks during the transition.
    ("kb_chunks", "embedding", "BLOB"),
    # Web-call memory: a browser-held caller id links one person's calls.
    ("calls", "caller_key", "TEXT"),
    # Download link for the plain-text record (recording_url holds the audio).
    ("calls", "record_url", "TEXT"),
    # A WhatsApp thread the owner started through the assistant: which agent
    # answers it and what it is trying to achieve there.
    ("conversations", "agent_id", "INTEGER"),
    ("conversations", "goal", "TEXT"),
    ("scheduled_jobs", "attempts", "INTEGER NOT NULL DEFAULT 0"),
]


def _migrate(conn) -> None:
    for table, column, coltype in MIGRATIONS:
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
    # UNIQUE can't be added by ALTER, so the dedupe index is created here.
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_msgs_wa_id ON messages(wa_id)")


def init() -> None:
    with connect() as conn:
        # WAL mode: allows concurrent readers while a writer holds the lock.
        # Critical for a single-worker uvicorn serving both API reads and the
        # background worker writing jobs — without it, readers block on writes.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        _migrate(conn)
    ensure_default_agent()


def get_setting(key: str, default: str = "") -> str:
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_setting(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute(
            """INSERT INTO settings (key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (key, value),
        )


# --- reads --------------------------------------------------------------

def get_stats() -> dict:
    with connect() as conn:
        row = conn.execute(
            f"""SELECT COUNT(*) AS total,
                       COALESCE(AVG(duration_sec), 0) AS avg_sec,
                       COALESCE(AVG(outcome IN {SUCCESS_OUTCOMES}) * 100, 0) AS success
                FROM calls WHERE status = 'ended'"""
        ).fetchone()

        # A conversation counts as "responded" once we've sent at least one
        # message that actually went out — a logged failure is not a response.
        answered = conn.execute(
            """SELECT COUNT(DISTINCT conversation_id) FROM messages
               WHERE sender = 'us' AND failed = 0"""
        ).fetchone()[0]
        total_convs = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]

    return {
        "totalCalls": row["total"],
        "avgDuration": fmt_duration(round(row["avg_sec"])),
        "successRate": round(row["success"], 1),
        "whatsappResponseRate": round(answered / total_convs * 100, 1) if total_convs else 0.0,
    }


def get_calls_by_hour() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT strftime('%H', started_at) AS hour, COUNT(*) AS calls
               FROM calls GROUP BY hour ORDER BY hour"""
        ).fetchall()
    return [{"hour": r["hour"], "calls": r["calls"]} for r in rows]


# Fixed colours so a language keeps the same swatch across renders.
LANG_COLORS = {
    "Hindi": "#7c5cff",
    "English": "#2ee6a8",
    "Hinglish": "#ff8a3d",
    "Marathi": "#4fa8ff",
    "Tamil": "#ff5c8a",
}


def get_languages() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT language, COUNT(*) AS n FROM calls
               GROUP BY language ORDER BY n DESC"""
        ).fetchall()
    total = sum(r["n"] for r in rows) or 1
    return [
        {
            "name": r["language"],
            "share": round(r["n"] / total * 100),
            "color": LANG_COLORS.get(r["language"], "#8b8b9e"),
        }
        for r in rows
    ]


def get_calls(limit: int = 50) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT * FROM calls ORDER BY started_at DESC LIMIT ?""", (limit,)
        ).fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "phone": r["phone"],
            "language": r["language"],
            "intent": r["intent"],
            "duration": fmt_duration(r["duration_sec"]),
            "outcome": r["outcome"],
            "summary": (r["summary"] if "summary" in r.keys() else None) or "",
            "success": (r["success"] if "success" in r.keys() else None),
            "recordingUrl": r["recording_url"],
            "recordUrl": r["record_url"],
            "time": _ago(r["started_at"]),
        }
        for r in rows
    ]


def past_calls_for(caller_key: str, limit: int = 3) -> list[dict]:
    """This caller's earlier calls that have a summary, newest first."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT name, summary, started_at FROM calls
               WHERE caller_key = ? AND summary IS NOT NULL AND summary != ''
               ORDER BY started_at DESC LIMIT ?""",
            (caller_key, limit),
        ).fetchall()
    return [{"name": r["name"], "summary": r["summary"], "time": _ago(r["started_at"])}
            for r in rows]


def get_conversations() -> list[dict]:
    with connect() as conn:
        convs = conn.execute("SELECT * FROM conversations ORDER BY updated_at DESC").fetchall()
        out = []
        for c in convs:
            msgs = conn.execute(
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at", (c["id"],)
            ).fetchall()
            unread = sum(1 for m in msgs if m["sender"] == "them" and not m["read"])
            last = msgs[-1] if msgs else None
            out.append(
                {
                    "id": c["id"],
                    "name": c["name"],
                    "phone": c["phone"],
                    "language": c["language"],
                    "unread": unread,
                    "autoReply": bool(c["auto_reply"]),
                    "last": _preview(last),
                    "time": _ago(c["updated_at"], short=True),
                    "messages": [
                        {
                            "from": m["sender"],
                            "type": m["kind"],
                            "text": m["body"],
                            "seconds": m["seconds"],
                            "failed": bool(m["failed"]),
                            "time": _clock(m["created_at"]),
                        }
                        for m in msgs
                    ],
                }
            )
    return out


# --- writes -------------------------------------------------------------

def upsert_call(vapi_call_id: str, **fields) -> None:
    """Insert or update a call keyed by its Vapi id.

    Updates run as a plain UPDATE rather than INSERT .. ON CONFLICT. SQLite
    builds the candidate INSERT row *before* the conflict clause can redirect
    it, so a partial update — a call ending, which carries no phone — died on
    `calls.phone NOT NULL`. Thrown from a WebSocket `finally`, that exception
    went nowhere, and every web call stayed 'active' with no duration and no
    transcript. The dashboard read zero calls for the same reason.
    """
    with connect() as conn:
        known = conn.execute(
            "SELECT 1 FROM calls WHERE vapi_call_id = ?", (vapi_call_id,)
        ).fetchone()

        if known:
            # id is the primary key and started_at belongs to the insert — an
            # update that reset started_at would date every call to its end.
            changes = {c: v for c, v in fields.items() if c != "id"}
            if changes:
                conn.execute(
                    f"UPDATE calls SET {','.join(f'{c}=?' for c in changes)} "
                    "WHERE vapi_call_id = ?",
                    [*changes.values(), vapi_call_id],
                )
        else:
            fields.setdefault("started_at", datetime.now(timezone.utc).isoformat())
            fields.setdefault("id", f"CL-{vapi_call_id[:8]}")
            cols = ["vapi_call_id", *fields.keys()]
            conn.execute(
                f"INSERT INTO calls ({','.join(cols)}) "
                f"VALUES ({','.join('?' * len(cols))})",
                [vapi_call_id, *fields.values()],
            )

    # The CRM fills itself from real activity rather than manual entry.
    if fields.get("phone"):
        touch_contact(fields["phone"], fields.get("name"))


def pending_replies(max_age_minutes: int = 20) -> list[dict]:
    """Incoming messages that were accepted but never answered.

    A reply runs as an in-process background task, so a restart or crash between
    "message stored" and "reply sent" used to lose it silently — the customer
    just never heard back. These are picked up on the next startup.

    Age-capped on purpose: answering a message from yesterday is worse than not
    answering it.
    """
    since = (datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)).isoformat()
    with connect() as conn:
        rows = conn.execute(
            """SELECT m.id, m.body, m.kind, v.phone, v.name
               FROM messages m JOIN conversations v ON v.id = m.conversation_id
               WHERE m.needs_reply = 1 AND m.sender = 'them' AND m.created_at >= ?
               ORDER BY m.created_at""",
            (since,),
        ).fetchall()
    return [dict(r) for r in rows]


def clear_needs_reply(message_id: int) -> None:
    with connect() as conn:
        conn.execute("UPDATE messages SET needs_reply = 0 WHERE id = ?", (message_id,))


def get_history(phone: str, limit: int = 12) -> list[dict]:
    """Recent turns for one number, oldest first — fed to the LLM as context."""
    with connect() as conn:
        rows = conn.execute(
            # failed = 0 matters: a failure leaves an "[auto-reply failed: …]"
            # note in the thread. Without this the model is handed its own error
            # text as something it said, and answers around it.
            """SELECT m.sender, m.body FROM messages m
               JOIN conversations c ON c.id = m.conversation_id
               WHERE c.phone = ? AND m.failed = 0
               ORDER BY m.created_at DESC LIMIT ?""",
            (phone, limit),
        ).fetchall()
    return [
        {"role": "assistant" if r["sender"] == "us" else "user", "content": r["body"]}
        for r in reversed(rows)
    ]


def add_message(phone: str, sender: str, kind: str, body: str, seconds: int | None = None,
                name: str | None = None, language: str = "Hindi", failed: bool = False,
                at: str | None = None, wa_id: str | None = None,
                needs_reply: bool = False) -> int | None:
    """Append a message.

    `at` preserves the original timestamp when importing history — without it
    every imported message would claim to be from right now. `wa_id` is
    WhatsApp's own message id, which makes re-running an import a no-op instead
    of duplicating the thread.
    """
    now = at or datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        conn.execute(
            """INSERT INTO conversations (name, phone, language, updated_at) VALUES (?,?,?,?)
               ON CONFLICT(phone) DO UPDATE SET
                   updated_at = MAX(conversations.updated_at, excluded.updated_at),
                   name = COALESCE(NULLIF(excluded.name,''), conversations.name)""",
            (name or phone, phone, language, now),
        )
        conv_id = conn.execute("SELECT id FROM conversations WHERE phone = ?", (phone,)).fetchone()[0]
        cur = conn.execute(
            """INSERT OR IGNORE INTO messages
                   (conversation_id, sender, kind, body, seconds, failed, needs_reply,
                    wa_id, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (conv_id, sender, kind, body, seconds, int(failed), int(needs_reply), wa_id, now),
        )
        message_id = cur.lastrowid if cur.rowcount else None
    touch_contact(phone, name)
    return message_id


# --- auto-reply switches ------------------------------------------------

AUTO_REPLY_KEY = "auto_reply"
ALLOWLIST_KEY = "auto_reply_allowlist"


def _digits(s: str) -> str:
    # unicodedata.digit, not the char itself: speech-to-text may write a Hindi
    # number in Devanagari numerals (९८११...), which WhatsApp can't dial.
    import unicodedata

    return "".join(str(unicodedata.digit(ch)) for ch in s if ch.isdigit())


def get_allowlist() -> list[str]:
    raw = get_setting(ALLOWLIST_KEY, "")
    return [n for n in (_digits(p) for p in raw.split(",")) if n]


def set_allowlist(numbers: list[str]) -> list[str]:
    cleaned = [n for n in (_digits(p) for p in numbers) if n]
    set_setting(ALLOWLIST_KEY, ",".join(cleaned))
    return cleaned


def allowed(phone: str) -> bool:
    """Empty allowlist means everyone — normal for a business number.

    Set it when the agent runs on a number that also carries personal chats,
    so friends don't get answered by a bot.
    """
    allow = get_allowlist()
    if not allow:
        return True
    # Compare on the last 10 digits: the same person appears as 9812345678,
    # 919812345678 and +91 98123 45678 depending on where the number came from.
    tail = _digits(phone)[-10:]
    return any(a[-10:] == tail for a in allow)


def auto_reply_global() -> bool:
    """Master switch. Off means the agent answers nobody."""
    return get_setting(AUTO_REPLY_KEY, "on") == "on"


def set_auto_reply_global(enabled: bool) -> None:
    set_setting(AUTO_REPLY_KEY, "on" if enabled else "off")


def set_auto_reply_for(phone: str, enabled: bool) -> None:
    """Per-thread override — the owner taking one conversation over by hand."""
    with connect() as conn:
        conn.execute(
            "UPDATE conversations SET auto_reply = ? WHERE phone = ?", (int(enabled), phone)
        )


def auto_reply_block(phone: str) -> str | None:
    """Why this number gets no auto-reply, or None if it does.

    Three switches can each stop a reply, and for a long time all three failed
    the same silent way — "it isn't replying" with nothing in the log to say
    which one. Returning the reason is what makes that diagnosable.

    An unknown number defaults to on: a first-time customer should get an
    answer, not silence.
    """
    if get_setting("automation_enabled", "on") != "on":
        return "automation is disabled globally"
    if not auto_reply_global():
        return "auto-reply is switched off globally"
    if not allowed(phone):
        return f"{phone} is not on the allowlist ({', '.join(get_allowlist()) or 'empty'})"
    with connect() as conn:
        row = conn.execute(
            "SELECT auto_reply FROM conversations WHERE phone = ?", (phone,)
        ).fetchone()
    if row and not row[0]:
        return "auto-reply is switched off for this conversation"
    return None


def should_auto_reply(phone: str) -> bool:
    """Both switches must be on. Incoming messages are always stored either way."""
    return auto_reply_block(phone) is None


# --- agents -------------------------------------------------------------

DEFAULT_AGENT = {
    "name": "Front desk",
    "prompt": (
        "You answer for the business. Be warm and efficient, like a good shop "
        "owner — not a call-centre script. Answer questions about timings, "
        "pricing and availability, and take bookings. Never invent prices, "
        "stock or appointment slots you were not given."
    ),
    "greeting": "Namaste! Main aapki kya madad kar sakti hoon?",
    "language": "Hindi",
}


def _row_to_agent(r) -> dict:
    return {
        "id": r["id"],
        "name": r["name"],
        "prompt": r["prompt"],
        "greeting": r["greeting"],
        "language": r["language"],
        "voiceId": r["voice_id"] or "",
        "facts": (r["facts"] if "facts" in r.keys() else None) or "",
        "isDefault": bool(r["is_default"]),
    }


def ensure_default_agent() -> None:
    """Every install needs one agent, or calls have no persona to run."""
    with connect() as conn:
        if conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0]:
            return
        conn.execute(
            """INSERT INTO agents (name, prompt, greeting, language, is_default, created_at)
               VALUES (?,?,?,?,1,?)""",
            (DEFAULT_AGENT["name"], DEFAULT_AGENT["prompt"], DEFAULT_AGENT["greeting"],
             DEFAULT_AGENT["language"], datetime.now(timezone.utc).isoformat()),
        )


def get_agents() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM agents ORDER BY is_default DESC, name").fetchall()
    return [_row_to_agent(r) for r in rows]


def get_agent(agent_id: int | None = None) -> dict | None:
    """A specific agent, or the default one when no id is given."""
    with connect() as conn:
        if agent_id:
            r = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        else:
            r = conn.execute(
                "SELECT * FROM agents ORDER BY is_default DESC, id LIMIT 1"
            ).fetchone()
    return _row_to_agent(r) if r else None


def save_agent(agent_id: int | None, **fields) -> dict:
    with connect() as conn:
        if fields.pop("is_default", False):
            conn.execute("UPDATE agents SET is_default = 0")
            fields["is_default"] = 1
        if agent_id:
            sets = ",".join(f"{k}=?" for k in fields)
            conn.execute(f"UPDATE agents SET {sets} WHERE id = ?", [*fields.values(), agent_id])
        else:
            fields["created_at"] = datetime.now(timezone.utc).isoformat()
            cols = ",".join(fields)
            conn.execute(
                f"INSERT INTO agents ({cols}) VALUES ({','.join('?' * len(fields))})",
                list(fields.values()),
            )
            agent_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return get_agent(agent_id)


def delete_agent(agent_id: int) -> None:
    with connect() as conn:
        # Never delete the last one — calls would have no persona to fall back to.
        if conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0] <= 1:
            raise ValueError("Cannot delete the only agent")
        conn.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
        if not conn.execute("SELECT 1 FROM agents WHERE is_default = 1").fetchone():
            conn.execute("UPDATE agents SET is_default = 1 WHERE id = (SELECT MIN(id) FROM agents)")


# --- contacts -----------------------------------------------------------

def touch_contact(phone: str, name: str | None = None) -> None:
    """Called on every call and message, so the CRM fills itself.

    An existing name is never overwritten by a blank one — WhatsApp gives us a
    profile name, phone calls usually don't.
    """
    if not phone or phone == "unknown":
        return
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        conn.execute(
            """INSERT INTO contacts (name, phone, created_at, last_seen_at) VALUES (?,?,?,?)
               ON CONFLICT(phone) DO UPDATE SET
                   last_seen_at = excluded.last_seen_at,
                   name = COALESCE(NULLIF(excluded.name,''), contacts.name)""",
            (name, phone, now, now),
        )


def get_contacts(limit: int = 200) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT c.*,
                      (SELECT COUNT(*) FROM calls WHERE calls.phone = c.phone)      AS calls,
                      (SELECT COUNT(*) FROM messages m
                         JOIN conversations v ON v.id = m.conversation_id
                        WHERE v.phone = c.phone)                                    AS messages
               FROM contacts c ORDER BY c.last_seen_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"] or "Unknown",
            "phone": r["phone"],
            "email": r["email"] or "",
            "status": r["status"],
            "notes": r["notes"] or "",
            "calls": r["calls"],
            "messages": r["messages"],
            "lastSeen": _ago(r["last_seen_at"]),
        }
        for r in rows
    ]


def get_contact(contact_id: int) -> dict | None:
    """One contact with calls and messages merged into a single timeline.

    This is the 'one dashboard, not five tools' promise — both channels for one
    customer, newest first.
    """
    with connect() as conn:
        c = conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
        if not c:
            return None
        calls = conn.execute(
            "SELECT * FROM calls WHERE phone = ? ORDER BY started_at DESC", (c["phone"],)
        ).fetchall()
        msgs = conn.execute(
            """SELECT m.* FROM messages m
               JOIN conversations v ON v.id = m.conversation_id
               WHERE v.phone = ? ORDER BY m.created_at DESC""",
            (c["phone"],),
        ).fetchall()

    timeline = [
        {
            "kind": "call",
            "at": r["started_at"],
            "when": _ago(r["started_at"]),
            "title": r["intent"],
            "detail": f"{fmt_duration(r['duration_sec'])} · {r['language']}",
            "outcome": r["outcome"],
        }
        for r in calls
    ] + [
        {
            "kind": "message",
            "at": r["created_at"],
            "when": _ago(r["created_at"]),
            "title": "You" if r["sender"] == "us" else "Customer",
            "detail": r["body"],
            "outcome": "voice note" if r["kind"] == "voice" else "text",
        }
        for r in msgs
    ]
    timeline.sort(key=lambda x: x["at"], reverse=True)

    return {
        "id": c["id"],
        "name": c["name"] or "Unknown",
        "phone": c["phone"],
        "email": c["email"] or "",
        "status": c["status"],
        "notes": c["notes"] or "",
        "timeline": timeline,
    }


def update_contact(contact_id: int, **fields) -> dict | None:
    if fields:
        with connect() as conn:
            sets = ",".join(f"{k}=?" for k in fields)
            conn.execute(
                f"UPDATE contacts SET {sets} WHERE id = ?", [*fields.values(), contact_id]
            )
    return get_contact(contact_id)


# --- campaigns ----------------------------------------------------------

def create_campaign(name: str, agent_id: int | None, targets: list[dict]) -> int:
    with connect() as conn:
        conn.execute(
            "INSERT INTO campaigns (name, agent_id, created_at) VALUES (?,?,?)",
            (name, agent_id, datetime.now(timezone.utc).isoformat()),
        )
        cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.executemany(
            "INSERT INTO campaign_targets (campaign_id, name, phone) VALUES (?,?,?)",
            [(cid, t.get("name"), t["phone"]) for t in targets],
        )
    return cid


def get_campaigns() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT c.*, a.name AS agent_name,
                      (SELECT COUNT(*) FROM campaign_targets t WHERE t.campaign_id = c.id) AS total,
                      (SELECT COUNT(*) FROM campaign_targets t
                        WHERE t.campaign_id = c.id AND t.status = 'called')                AS called,
                      (SELECT COUNT(*) FROM campaign_targets t
                        WHERE t.campaign_id = c.id AND t.status = 'failed')                AS failed
               FROM campaigns c LEFT JOIN agents a ON a.id = c.agent_id
               ORDER BY c.created_at DESC"""
        ).fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "agentName": r["agent_name"] or "Default",
            "status": r["status"],
            "total": r["total"],
            "called": r["called"],
            "failed": r["failed"],
            "created": _ago(r["created_at"]),
        }
        for r in rows
    ]


def get_campaign_targets(campaign_id: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM campaign_targets WHERE campaign_id = ? ORDER BY id", (campaign_id,)
        ).fetchall()
    return [
        {"id": r["id"], "name": r["name"] or "", "phone": r["phone"],
         "status": r["status"], "error": r["error"] or ""}
        for r in rows
    ]


def next_pending_target(campaign_id: int) -> dict | None:
    with connect() as conn:
        r = conn.execute(
            "SELECT * FROM campaign_targets WHERE campaign_id = ? AND status = 'pending' LIMIT 1",
            (campaign_id,),
        ).fetchone()
    return dict(r) if r else None


def set_target_status(target_id: int, status: str, call_id: str = "", error: str = "") -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE campaign_targets SET status = ?, call_id = ?, error = ? WHERE id = ?",
            (status, call_id, error, target_id),
        )


def set_campaign_status(campaign_id: int, status: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE campaigns SET status = ? WHERE id = ?", (status, campaign_id))


# --- analytics ----------------------------------------------------------

def get_analytics(days: int = 30) -> dict:
    """Everything the analytics page needs, in one round trip."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with connect() as conn:
        daily = conn.execute(
            """SELECT date(started_at) AS day, COUNT(*) AS calls,
                      COALESCE(AVG(duration_sec), 0) AS avg_sec
               FROM calls WHERE started_at >= ? GROUP BY day ORDER BY day""",
            (since,),
        ).fetchall()
        outcomes = conn.execute(
            """SELECT outcome, COUNT(*) AS n FROM calls WHERE started_at >= ?
               GROUP BY outcome ORDER BY n DESC""",
            (since,),
        ).fetchall()
        by_agent = conn.execute(
            """SELECT COALESCE(a.name, 'Unassigned') AS agent, COUNT(*) AS calls,
                      COALESCE(AVG(c.duration_sec), 0) AS avg_sec
               FROM calls c LEFT JOIN agents a ON a.id = c.agent_id
               WHERE c.started_at >= ? GROUP BY agent ORDER BY calls DESC""",
            (since,),
        ).fetchall()
        msgs = conn.execute(
            """SELECT date(created_at) AS day,
                      SUM(sender = 'them') AS inbound,
                      SUM(sender = 'us' AND failed = 0) AS replies
               FROM messages WHERE created_at >= ? GROUP BY day ORDER BY day""",
            (since,),
        ).fetchall()

    return {
        "days": days,
        "daily": [
            {"day": r["day"], "calls": r["calls"], "avgSec": round(r["avg_sec"])} for r in daily
        ],
        "outcomes": [{"outcome": r["outcome"], "count": r["n"]} for r in outcomes],
        "byAgent": [
            {"agent": r["agent"], "calls": r["calls"], "avgDuration": fmt_duration(round(r["avg_sec"]))}
            for r in by_agent
        ],
        "whatsapp": [
            {"day": r["day"], "inbound": r["inbound"] or 0, "replies": r["replies"] or 0}
            for r in msgs
        ],
    }


# --- helpers ------------------------------------------------------------

def _parse(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _ago(ts: str, short: bool = False) -> str:
    mins = max(0, int((datetime.now(timezone.utc) - _parse(ts)).total_seconds() // 60))
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{mins}m" if short else f"{mins} min ago"
    hours = mins // 60
    if hours < 24:
        return f"{hours}h" if short else f"{hours} hr ago"
    days = hours // 24
    return f"{days}d" if short else f"{days} days ago"


def _clock(ts: str) -> str:
    return _parse(ts).strftime("%I:%M %p").lstrip("0")


def _preview(msg) -> str:
    if msg is None:
        return ""
    if msg["kind"] == "voice":
        return f"🎤 Voice note · 0:{msg['seconds']:02d}"
    return msg["body"]



# --- knowledge base -----------------------------------------------------

def add_kb_document(agent_id: int | None, filename: str, chunks: list[str],
                    embeddings: list[bytes] | None = None) -> dict:
    """Store one uploaded reference file, its text chunks, and (optionally) each
    chunk's embedding vector for semantic search. `embeddings` aligns 1:1 with
    `chunks`; pass None to store text only (BM25-only)."""
    embs = embeddings if embeddings is not None else [None] * len(chunks)
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO kb_docs (agent_id, filename, chunks, created_at) VALUES (?,?,?,?)",
            (agent_id, filename, len(chunks), datetime.now(timezone.utc).isoformat()),
        )
        doc_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO kb_chunks (doc_id, agent_id, text, embedding) VALUES (?,?,?,?)",
            [(doc_id, agent_id, c, e) for c, e in zip(chunks, embs)],
        )
    return {"id": doc_id, "filename": filename, "chunks": len(chunks), "agentId": agent_id}


def list_kb_docs(agent_id: int | None = None) -> list[dict]:
    """Documents visible to an agent: its own plus the global (agent_id IS NULL)
    ones. Pass None to list only the global set."""
    with connect() as conn:
        if agent_id is None:
            rows = conn.execute(
                "SELECT * FROM kb_docs WHERE agent_id IS NULL ORDER BY id DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM kb_docs WHERE agent_id = ? OR agent_id IS NULL ORDER BY id DESC",
                (agent_id,),
            ).fetchall()
    return [
        {"id": r["id"], "filename": r["filename"], "chunks": r["chunks"],
         "agentId": r["agent_id"], "global": r["agent_id"] is None}
        for r in rows
    ]


def delete_kb_doc(doc_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM kb_chunks WHERE doc_id = ?", (doc_id,))
        conn.execute("DELETE FROM kb_docs WHERE id = ?", (doc_id,))


def kb_chunks_for(agent_id: int | None) -> list[str]:
    """Every chunk an agent may draw on: its own documents plus the global ones."""
    with connect() as conn:
        if agent_id is None:
            rows = conn.execute("SELECT text FROM kb_chunks WHERE agent_id IS NULL").fetchall()
        else:
            rows = conn.execute(
                "SELECT text FROM kb_chunks WHERE agent_id = ? OR agent_id IS NULL", (agent_id,)
            ).fetchall()
    return [r["text"] for r in rows]


def kb_chunks_with_embeddings(agent_id: int | None) -> list[tuple[str, bytes | None]]:
    """(text, embedding-bytes) for every chunk an agent may draw on. Embedding is
    NULL for chunks stored before semantic search — the caller falls back to BM25."""
    with connect() as conn:
        if agent_id is None:
            rows = conn.execute(
                "SELECT text, embedding FROM kb_chunks WHERE agent_id IS NULL"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT text, embedding FROM kb_chunks WHERE agent_id = ? OR agent_id IS NULL",
                (agent_id,),
            ).fetchall()
    return [(r["text"], r["embedding"]) for r in rows]


# --- bookings (recorded by agent tool calls) -----------------------------

def add_booking(item: str, name: str | None = None, phone: str | None = None,
                when_text: str | None = None, notes: str | None = None,
                channel: str = "call", escalated: bool = False) -> dict:
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO bookings (phone, name, item, when_text, notes, channel,
                                     escalated, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (phone, name, item, when_text, notes, channel, int(escalated),
             datetime.now(timezone.utc).isoformat()),
        )
        booking_id = cur.lastrowid
    # A booking is real CRM activity — surface the caller as a contact too.
    if phone:
        touch_contact(phone, name)
    return {"id": booking_id, "item": item, "name": name, "when": when_text}


def get_bookings(limit: int = 50) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM bookings ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [
        {"id": r["id"], "name": r["name"] or "—", "phone": r["phone"] or "—",
         "item": r["item"], "when": r["when_text"] or "—", "notes": r["notes"] or "",
         "channel": r["channel"], "status": r["status"],
         "escalated": bool(r["escalated"]), "time": _ago(r["created_at"])}
        for r in rows
    ]


def delete_booking(booking_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM bookings WHERE id = ?", (booking_id,))


def delete_contact(contact_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))


# --- adaptive learning ---------------------------------------------------

def add_learned_answer(agent_id: int | None, question: str, answer: str,
                       source: str = "conversation", confidence: float = 0.5) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO learned_answers
                   (agent_id, question, answer, source, confidence, created_at)
               VALUES (?,?,?,?,?,?)""",
            (agent_id, question, answer, source, confidence, now),
        )
        lid = cur.lastrowid
    return {"id": lid, "question": question, "answer": answer, "status": "pending",
            "source": source, "confidence": confidence}


def get_learned_answers(agent_id: int | None = None, status: str | None = None,
                        limit: int = 100) -> list[dict]:
    with connect() as conn:
        clauses, params = [], []
        if agent_id is not None:
            clauses.append("(agent_id = ? OR agent_id IS NULL)")
            params.append(agent_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM learned_answers {where} ORDER BY id DESC LIMIT ?",
            [*params, limit],
        ).fetchall()
    return [
        {"id": r["id"], "agentId": r["agent_id"], "question": r["question"],
         "answer": r["answer"], "source": r["source"],
         "confidence": r["confidence"], "status": r["status"],
         "useCount": r["use_count"], "time": _ago(r["created_at"])}
        for r in rows
    ]


def approved_answers_for(agent_id: int | None) -> list[dict]:
    """Only approved answers, for injection into the reply prompt."""
    with connect() as conn:
        if agent_id is None:
            rows = conn.execute(
                "SELECT question, answer FROM learned_answers WHERE status = 'approved' AND agent_id IS NULL"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT question, answer FROM learned_answers WHERE status = 'approved' AND (agent_id = ? OR agent_id IS NULL)",
                (agent_id,),
            ).fetchall()
    return [{"question": r["question"], "answer": r["answer"]} for r in rows]


def update_learned_status(learned_id: int, status: str) -> dict | None:
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        conn.execute(
            "UPDATE learned_answers SET status = ?, approved_at = ? WHERE id = ?",
            (status, now if status == "approved" else None, learned_id),
        )
        r = conn.execute("SELECT * FROM learned_answers WHERE id = ?", (learned_id,)).fetchone()
    if not r:
        return None
    return {"id": r["id"], "status": r["status"], "question": r["question"], "answer": r["answer"]}


def increment_learned_use(learned_id: int) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE learned_answers SET use_count = use_count + 1 WHERE id = ?",
            (learned_id,),
        )


def delete_learned(learned_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM learned_answers WHERE id = ?", (learned_id,))


# --- stock ---------------------------------------------------------------

def add_stock_item(name: str, category: str | None = None, quantity: float = 0,
                   unit: str = "pcs", rate: float = 0, gst_percent: float = 0,
                   min_stock: float = 0, agent_id: int | None = None) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO stock
                   (agent_id, name, category, quantity, unit, rate, gst_percent,
                    min_stock, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (agent_id, name, category, quantity, unit, rate, gst_percent,
             min_stock, now, now),
        )
        sid = cur.lastrowid
    return get_stock_item(sid) or {"id": sid, "name": name}


def get_stock_items(agent_id: int | None = None, limit: int = 200) -> list[dict]:
    with connect() as conn:
        if agent_id is not None:
            rows = conn.execute(
                "SELECT * FROM stock WHERE agent_id = ? OR agent_id IS NULL ORDER BY name LIMIT ?",
                (agent_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM stock ORDER BY name LIMIT ?", (limit,)
            ).fetchall()
    return [_stock_row(r) for r in rows]


def get_stock_item(stock_id: int) -> dict | None:
    with connect() as conn:
        r = conn.execute("SELECT * FROM stock WHERE id = ?", (stock_id,)).fetchone()
    return _stock_row(r) if r else None


def _stock_row(r) -> dict:
    return {
        "id": r["id"], "agentId": r["agent_id"], "name": r["name"],
        "category": r["category"] or "", "quantity": r["quantity"],
        "unit": r["unit"], "rate": r["rate"], "gstPercent": r["gst_percent"],
        "minStock": r["min_stock"],
        "lowStock": r["quantity"] <= r["min_stock"] and r["min_stock"] > 0,
        "updated": _ago(r["updated_at"]),
    }


def update_stock_item(stock_id: int, **fields) -> dict | None:
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        sets = ",".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE stock SET {sets} WHERE id = ?", [*fields.values(), stock_id])
    return get_stock_item(stock_id)


def delete_stock_item(stock_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM stock_entries WHERE stock_id = ?", (stock_id,))
        conn.execute("DELETE FROM stock WHERE id = ?", (stock_id,))


def search_stock(query: str, agent_id: int | None = None) -> list[dict]:
    """Fuzzy name search for voice commands like 'check stock of cement'."""
    q = f"%{query.strip()}%"
    with connect() as conn:
        if agent_id is not None:
            rows = conn.execute(
                "SELECT * FROM stock WHERE (agent_id = ? OR agent_id IS NULL) AND name LIKE ? ORDER BY name LIMIT 10",
                (agent_id, q),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM stock WHERE name LIKE ? ORDER BY name LIMIT 10", (q,)
            ).fetchall()
    return [_stock_row(r) for r in rows]


def add_stock_entry(stock_id: int, entry_type: str, quantity: float,
                    notes: str | None = None) -> dict:
    """Record a stock movement and update the running total."""
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        conn.execute(
            "INSERT INTO stock_entries (stock_id, entry_type, quantity, notes, created_at) VALUES (?,?,?,?,?)",
            (stock_id, entry_type, quantity, notes, now),
        )
        # Update running total
        if entry_type == "in":
            conn.execute("UPDATE stock SET quantity = quantity + ?, updated_at = ? WHERE id = ?",
                         (quantity, now, stock_id))
        elif entry_type == "out":
            conn.execute("UPDATE stock SET quantity = quantity - ?, updated_at = ? WHERE id = ?",
                         (quantity, now, stock_id))
        else:  # adjust — set absolute
            conn.execute("UPDATE stock SET quantity = ?, updated_at = ? WHERE id = ?",
                         (quantity, now, stock_id))
        item = get_stock_item(stock_id)
    return {"id": stock_id, "entry": entry_type, "quantity": quantity,
            "newQuantity": item["quantity"] if item else quantity}


def get_stock_entries(stock_id: int, limit: int = 50) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM stock_entries WHERE stock_id = ? ORDER BY id DESC LIMIT ?",
            (stock_id, limit),
        ).fetchall()
    return [
        {"id": r["id"], "type": r["entry_type"], "quantity": r["quantity"],
         "notes": r["notes"] or "", "time": _ago(r["created_at"])}
        for r in rows
    ]


# --- bills ---------------------------------------------------------------

def _next_bill_number() -> str:
    """Sequential bill numbers: BILL-0001, BILL-0002, etc."""
    with connect() as conn:
        row = conn.execute("SELECT COUNT(*) FROM bills").fetchone()
    return f"BILL-{(row[0] + 1):04d}"


def create_bill(customer_name: str | None = None, customer_phone: str | None = None,
                agent_id: int | None = None, channel: str = "call") -> dict:
    now = datetime.now(timezone.utc).isoformat()
    bill_number = _next_bill_number()
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO bills
                   (bill_number, customer_name, customer_phone, agent_id, channel, created_at)
               VALUES (?,?,?,?,?,?)""",
            (bill_number, customer_name, customer_phone, agent_id, channel, now),
        )
        bill_id = cur.lastrowid
    return {"id": bill_id, "billNumber": bill_number, "customerName": customer_name,
            "status": "draft", "subtotal": 0, "gstTotal": 0, "total": 0}


def add_bill_item(bill_id: int, item_name: str, quantity: float, rate: float,
                  gst_percent: float = 0, stock_id: int | None = None) -> dict:
    """Add a line to the bill, update the bill totals, and deduct stock if linked."""
    base = round(quantity * rate, 2)
    gst = round(base * gst_percent / 100, 2)
    amount = round(base + gst, 2)
    with connect() as conn:
        conn.execute(
            """INSERT INTO bill_items
                   (bill_id, stock_id, item_name, quantity, rate, gst_percent, amount)
               VALUES (?,?,?,?,?,?,?)""",
            (bill_id, stock_id, item_name, quantity, rate, gst_percent, amount),
        )
        # Recalculate bill totals
        conn.execute(
            """UPDATE bills SET
                   subtotal = (SELECT COALESCE(SUM(quantity * rate), 0) FROM bill_items WHERE bill_id = ?),
                   gst_total = (SELECT COALESCE(SUM(quantity * rate * gst_percent / 100), 0) FROM bill_items WHERE bill_id = ?),
                   total = (SELECT COALESCE(SUM(amount), 0) FROM bill_items WHERE bill_id = ?)
               WHERE id = ?""",
            (bill_id, bill_id, bill_id, bill_id),
        )
    # Deduct from stock if linked
    if stock_id:
        try:
            add_stock_entry(stock_id, "out", quantity, notes=f"Bill item: {item_name}")
        except Exception:
            pass  # stock deduction is best-effort
    return {"itemName": item_name, "quantity": quantity, "rate": rate,
            "gst": gst, "amount": amount}


def finalize_bill(bill_id: int) -> dict | None:
    with connect() as conn:
        conn.execute("UPDATE bills SET status = 'final' WHERE id = ?", (bill_id,))
    return get_bill(bill_id)


def get_bill(bill_id: int) -> dict | None:
    with connect() as conn:
        r = conn.execute("SELECT * FROM bills WHERE id = ?", (bill_id,)).fetchone()
        if not r:
            return None
        items = conn.execute(
            "SELECT * FROM bill_items WHERE bill_id = ? ORDER BY id", (bill_id,)
        ).fetchall()
    return {
        "id": r["id"], "billNumber": r["bill_number"],
        "customerName": r["customer_name"] or "—",
        "customerPhone": r["customer_phone"] or "—",
        "channel": r["channel"], "status": r["status"],
        "subtotal": r["subtotal"], "gstTotal": r["gst_total"], "total": r["total"],
        "notes": r["notes"] or "", "time": _ago(r["created_at"]),
        "items": [
            {"id": i["id"], "name": i["item_name"], "quantity": i["quantity"],
             "rate": i["rate"], "gst": i["gst_percent"], "amount": i["amount"]}
            for i in items
        ],
    }


def get_bills(limit: int = 50) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM bills ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [
        {"id": r["id"], "billNumber": r["bill_number"],
         "customerName": r["customer_name"] or "—",
         "channel": r["channel"], "status": r["status"],
         "total": r["total"], "time": _ago(r["created_at"])}
        for r in rows
    ]


def delete_bill(bill_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM bill_items WHERE bill_id = ?", (bill_id,))
        conn.execute("DELETE FROM bills WHERE id = ?", (bill_id,))


def get_active_bill(channel: str = "call") -> dict | None:
    """The most recent draft bill on this channel — for continuing a bill mid-conversation."""
    with connect() as conn:
        r = conn.execute(
            "SELECT id FROM bills WHERE status = 'draft' AND channel = ? ORDER BY id DESC LIMIT 1",
            (channel,),
        ).fetchone()
    return get_bill(r["id"]) if r else None


# --- caller-scoped lookups for the agent's search tool --------------------
# Always filtered by the caller's own number: the agent may look up THIS
# customer's past bookings, bills and messages, never another customer's.

def bookings_for(phone: str, limit: int = 10) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM bookings WHERE phone = ? ORDER BY id DESC LIMIT ?",
            (phone, limit),
        ).fetchall()
    return [{"id": r["id"], "item": r["item"], "when": r["when_text"] or "",
             "status": r["status"], "escalated": bool(r["escalated"]),
             "time": _ago(r["created_at"])} for r in rows]


def bills_for(phone: str, limit: int = 10) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id FROM bills WHERE customer_phone = ? ORDER BY id DESC LIMIT ?",
            (phone, limit),
        ).fetchall()
    return [b for r in rows if (b := get_bill(r["id"]))]


def messages_for(phone: str, limit: int = 300) -> list[str]:
    """This number's whole thread (beyond the short prompt window), newest first."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT m.sender, m.body, m.created_at FROM messages m
               JOIN conversations c ON c.id = m.conversation_id
               WHERE c.phone = ? AND m.failed = 0
               ORDER BY m.created_at DESC LIMIT ?""",
            (phone, limit),
        ).fetchall()
    return [f"[{_ago(r['created_at'])}] {'Customer' if r['sender'] == 'them' else 'Us'}: {r['body']}"
            for r in rows]


# --- evals ----------------------------------------------------------------

def add_eval_case(name: str, agent_id: int | None, mode: str, turns: str, persona: str,
                  max_turns: int, rubric: str) -> int:
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO eval_cases (name, agent_id, mode, turns, persona, max_turns,
                                       rubric, created_at) VALUES (?,?,?,?,?,?,?,?)""",
            (name, agent_id, mode, turns, persona, max_turns, rubric,
             datetime.now(timezone.utc).isoformat()),
        )
        return cur.lastrowid


def _eval_run_row(r) -> dict:
    import json

    return {"id": r["id"], "passed": bool(r["passed"]), "reason": r["reason"] or "",
            "transcript": r["transcript"] or "", "tools": json.loads(r["tools"] or "[]"),
            "time": _ago(r["created_at"])}


def get_eval_case(case_id: int) -> dict | None:
    with connect() as conn:
        r = conn.execute("SELECT * FROM eval_cases WHERE id = ?", (case_id,)).fetchone()
    return dict(r) if r else None


def get_eval_cases() -> list[dict]:
    with connect() as conn:
        cases = conn.execute("SELECT * FROM eval_cases ORDER BY id DESC").fetchall()
        out = []
        for c in cases:
            last = conn.execute(
                "SELECT * FROM eval_runs WHERE case_id = ? ORDER BY id DESC LIMIT 1", (c["id"],)
            ).fetchone()
            out.append({
                "id": c["id"], "name": c["name"], "agentId": c["agent_id"], "mode": c["mode"],
                "turns": c["turns"] or "", "persona": c["persona"] or "",
                "maxTurns": c["max_turns"], "rubric": c["rubric"],
                "lastRun": _eval_run_row(last) if last else None,
            })
    return out


def add_eval_run(case_id: int, passed: bool, reason: str, transcript: str,
                 tools: list[dict]) -> dict:
    import json

    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO eval_runs (case_id, passed, reason, transcript, tools, created_at)
               VALUES (?,?,?,?,?,?)""",
            (case_id, int(passed), reason, transcript, json.dumps(tools, ensure_ascii=False),
             datetime.now(timezone.utc).isoformat()),
        )
        r = conn.execute("SELECT * FROM eval_runs WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _eval_run_row(r)


def delete_eval_case(case_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM eval_runs WHERE case_id = ?", (case_id,))
        conn.execute("DELETE FROM eval_cases WHERE id = ?", (case_id,))


# --- owner-started WhatsApp threads ---------------------------------------

def normalize_phone(raw: str) -> str:
    """Spoken/typed number -> the digits WhatsApp uses ("98123 45678" -> 919812345678).

    Incoming messages arrive keyed as country code + number, so an outgoing
    thread must use the same form or the reply lands in a different thread.
    """
    d = _digits(raw or "")
    if len(d) == 11 and d.startswith("0"):
        d = d[1:]
    if len(d) == 10:
        d = "91" + d
    return d


def assign_conversation(phone: str, agent_id: int | None, goal: str, name: str | None = None) -> None:
    """Hand a WhatsApp thread to an agent with a goal, and make sure it will
    actually auto-reply there — an allowlist or a per-thread "off" would
    otherwise silently stop the agent the owner just asked to talk."""
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        conn.execute(
            """INSERT INTO conversations (name, phone, updated_at) VALUES (?,?,?)
               ON CONFLICT(phone) DO NOTHING""",
            (name or phone, phone, now),
        )
        conn.execute(
            "UPDATE conversations SET agent_id = ?, goal = ?, auto_reply = 1 WHERE phone = ?",
            (agent_id, goal or None, phone),
        )
    if get_allowlist() and not allowed(phone):
        set_allowlist([*get_allowlist(), phone])


def conversation_brief(phone: str) -> dict:
    """The agent and goal the owner set for this thread, if any."""
    with connect() as conn:
        r = conn.execute(
            "SELECT agent_id, goal FROM conversations WHERE phone = ?", (phone,)
        ).fetchone()
    return {"agent_id": r["agent_id"], "goal": r["goal"]} if r else {"agent_id": None, "goal": None}


def set_thread_auto_reply(phone: str, enabled: bool) -> None:
    """Per-number auto-reply, creating the thread if this number never wrote yet
    (set_auto_reply_for only touches existing threads)."""
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        conn.execute(
            """INSERT INTO conversations (name, phone, updated_at) VALUES (?,?,?)
               ON CONFLICT(phone) DO NOTHING""",
            (phone, phone, now),
        )
        conn.execute("UPDATE conversations SET auto_reply = ? WHERE phone = ?", (int(enabled), phone))

# --- push subscriptions ---------------------------------------------------

def upsert_push_subscription(endpoint: str, subscription_json: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        conn.execute(
            """INSERT INTO push_subscriptions (endpoint, subscription_json, created_at)
               VALUES (?,?,?) ON CONFLICT(endpoint) DO UPDATE SET subscription_json = excluded.subscription_json""",
            (endpoint, subscription_json, now),
        )


def delete_push_subscription(endpoint: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))


def get_push_subscriptions() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT endpoint, subscription_json FROM push_subscriptions").fetchall()
    return [dict(r) for r in rows]


# --- scheduled jobs -------------------------------------------------------

def add_job(job_type: str, payload: dict, run_at: str) -> int:
    import json
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO scheduled_jobs (job_type, payload, run_at, created_at)
               VALUES (?,?,?,?)""",
            (job_type, json.dumps(payload), run_at, now),
        )
        return cur.lastrowid


def claim_next_job() -> dict | None:
    """Atomically claim one pending job whose run_at has passed.

    Uses UPDATE ... WHERE status='pending' and verifies rowcount == 1 — only one
    caller gets the row even if multiple processes/workers race on the same table.
    """
    import json
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        row = conn.execute(
            """SELECT id FROM scheduled_jobs
               WHERE status = 'pending' AND run_at <= ?
               ORDER BY run_at LIMIT 1""",
            (now,),
        ).fetchone()
        if not row:
            return None
        cur = conn.execute(
            """UPDATE scheduled_jobs
               SET status = 'running', claimed_at = ?, attempts = attempts + 1
               WHERE id = ? AND status = 'pending'""",
            (now, row["id"]),
        )
        if cur.rowcount != 1:
            return None  # another worker claimed it, safe concurrency guard
        job = conn.execute("SELECT * FROM scheduled_jobs WHERE id = ?", (row["id"],)).fetchone()
    if not job or job["status"] != "running":
        return None
    result = dict(job)
    result["payload"] = json.loads(result["payload"])
    return result


def recover_stuck_jobs(max_attempts: int = 3, timeout_minutes: int = 5) -> int:
    """Crash recovery: jobs stuck in 'running' for >5 minutes go back to 'pending'.

    If attempts >= max_attempts, they are marked as 'failed' with an error message.
    """
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)).isoformat()
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        conn.execute(
            """UPDATE scheduled_jobs
               SET status = 'failed', finished_at = ?, error = 'Max retry attempts exceeded (worker timed out)'
               WHERE status = 'running' AND claimed_at <= ? AND attempts >= ?""",
            (now, cutoff, max_attempts),
        )
        cur = conn.execute(
            """UPDATE scheduled_jobs
               SET status = 'pending', claimed_at = NULL
               WHERE status = 'running' AND claimed_at <= ?""",
            (cutoff,),
        )
        return cur.rowcount


def complete_job(job_id: int, status: str, error: str | None = None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        conn.execute(
            "UPDATE scheduled_jobs SET status = ?, finished_at = ?, error = ? WHERE id = ?",
            (status, now, error, job_id),
        )


def get_pending_jobs_count() -> int:
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM scheduled_jobs WHERE status = 'pending'").fetchone()[0]


def get_recent_jobs(limit: int = 20) -> list[dict]:
    import json
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM scheduled_jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["payload"] = json.loads(d["payload"])
        except Exception:
            pass
        out.append(d)
    return out


# ── Cloned Audios ────────────────────────────────────────────────────────

def save_cloned_audio(clone_id: str, text: str, filename: str, filepath: str, size_bytes: int = 0) -> int:
    with connect() as db:
        cur = db.execute(
            """INSERT INTO cloned_audios (clone_id, text, filename, filepath, size_bytes, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (clone_id, text, filename, filepath, size_bytes, datetime.now(timezone.utc).isoformat()),
        )
        return cur.lastrowid


def list_cloned_audios(limit: int = 50) -> list[dict]:
    with connect() as db:
        rows = db.execute(
            """SELECT id, clone_id, text, filename, filepath, size_bytes, created_at
               FROM cloned_audios ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def delete_cloned_audio(audio_id: int) -> str | None:
    with connect() as db:
        row = db.execute("""SELECT filepath FROM cloned_audios WHERE id = ?""", (audio_id,)).fetchone()
        if not row:
            return None
        db.execute("""DELETE FROM cloned_audios WHERE id = ?""", (audio_id,))
        return row["filepath"]
