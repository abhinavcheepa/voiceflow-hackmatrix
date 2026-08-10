"""SQLite storage for calls and WhatsApp conversations.

Plain sqlite3 — no ORM. One file on disk, one connection per request.
Swap the DSN for Postgres when there's a reason to; nothing here needs it yet.
"""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

DB_PATH = os.getenv("DATABASE_PATH", os.path.join(os.path.dirname(__file__), "voiceflow.db"))

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
    wa_id           TEXT UNIQUE,
    created_at      TEXT NOT NULL
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
"""

# Outcomes that count toward the success rate on the dashboard.
SUCCESS_OUTCOMES = ("Booked", "Lead captured", "Answered")


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
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
            "time": _ago(r["started_at"]),
        }
        for r in rows
    ]


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
    """Insert or update a call keyed by its Vapi id. Used by the webhook."""
    fields.setdefault("started_at", datetime.now(timezone.utc).isoformat())
    fields.setdefault("id", f"CL-{vapi_call_id[:8]}")
    cols = ["vapi_call_id", *fields.keys()]
    with connect() as conn:
        conn.execute(
            f"""INSERT INTO calls ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})
                ON CONFLICT(vapi_call_id) DO UPDATE SET
                {','.join(f'{c}=excluded.{c}' for c in fields if c != 'id')}""",
            [vapi_call_id, *fields.values()],
        )
    # The CRM fills itself from real activity rather than manual entry.
    if fields.get("phone"):
        touch_contact(fields["phone"], fields.get("name"))


def get_history(phone: str, limit: int = 12) -> list[dict]:
    """Recent turns for one number, oldest first — fed to the LLM as context."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT m.sender, m.body FROM messages m
               JOIN conversations c ON c.id = m.conversation_id
               WHERE c.phone = ? ORDER BY m.created_at DESC LIMIT ?""",
            (phone, limit),
        ).fetchall()
    return [
        {"role": "assistant" if r["sender"] == "us" else "user", "content": r["body"]}
        for r in reversed(rows)
    ]


def add_message(phone: str, sender: str, kind: str, body: str, seconds: int | None = None,
                name: str | None = None, language: str = "Hindi", failed: bool = False,
                at: str | None = None, wa_id: str | None = None) -> None:
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
        conn.execute(
            """INSERT OR IGNORE INTO messages
                   (conversation_id, sender, kind, body, seconds, failed, wa_id, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (conv_id, sender, kind, body, seconds, int(failed), wa_id, now),
        )
    touch_contact(phone, name)


# --- auto-reply switches ------------------------------------------------

AUTO_REPLY_KEY = "auto_reply"
ALLOWLIST_KEY = "auto_reply_allowlist"


def _digits(s: str) -> str:
    return "".join(ch for ch in s if ch.isdigit())


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


def should_auto_reply(phone: str) -> bool:
    """Both switches must be on. Incoming messages are always stored either way.

    An unknown number defaults to on: a first-time customer should get an
    answer, not silence.
    """
    if not auto_reply_global() or not allowed(phone):
        return False
    with connect() as conn:
        row = conn.execute(
            "SELECT auto_reply FROM conversations WHERE phone = ?", (phone,)
        ).fetchone()
    return bool(row[0]) if row else True


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

