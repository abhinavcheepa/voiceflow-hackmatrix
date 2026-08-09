"""SQLite storage for calls and WhatsApp conversations.

Plain sqlite3 — no ORM. One file on disk, one connection per request.
Swap the DSN for Postgres when there's a reason to; nothing here needs it yet.
"""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

DB_PATH = os.getenv("DATABASE_PATH", os.path.join(os.path.dirname(__file__), "voiceflow.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id             TEXT PRIMARY KEY,
    vapi_call_id   TEXT UNIQUE,
    name           TEXT NOT NULL DEFAULT 'Unknown',
    phone          TEXT NOT NULL,
    language       TEXT NOT NULL DEFAULT 'Hindi',
    intent         TEXT NOT NULL DEFAULT 'General enquiry',
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
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

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


def init() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


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
                name: str | None = None, language: str = "Hindi", failed: bool = False) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        conn.execute(
            """INSERT INTO conversations (name, phone, language, updated_at) VALUES (?,?,?,?)
               ON CONFLICT(phone) DO UPDATE SET updated_at = excluded.updated_at""",
            (name or phone, phone, language, now),
        )
        conv_id = conn.execute("SELECT id FROM conversations WHERE phone = ?", (phone,)).fetchone()[0]
        conn.execute(
            """INSERT INTO messages (conversation_id, sender, kind, body, seconds, failed, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (conv_id, sender, kind, body, seconds, int(failed), now),
        )


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

