"""Checks for the parts that are pure logic — no keys or network needed.

    python test_brain.py

Several of these exist because the bug happened in production. Read the
docstring before "simplifying" one away.
"""

import os
import tempfile

# Point the database somewhere disposable before db is imported — these tests
# write rows, and they must never touch the real voiceflow.db.
_TMP_DB = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["DATABASE_PATH"] = _TMP_DB

import brain  # noqa: E402
import db  # noqa: E402
import whatsapp  # noqa: E402


def test_detect_language():
    cases = [
        ("Namaste, kya aaj shaam ka slot mil sakta hai?", "Hinglish"),
        ("Namaste! Aaj shaam ka slot check karke batata hoon.", "Hinglish"),
        ("Theek hai, bhej dijiye", "Hinglish"),
        ("Do you have the new collection in stock?", "English"),
        ("The main entrance is closed for repairs today.", "English"),
        ("Please confirm my appointment for tomorrow morning.", "English"),
        ("उद्या सकाळी येऊ शकतो का?", "Marathi"),
        ("क्या आज शाम का स्लॉट मिलेगा?", "Hindi"),
        ("ஆர்டர் எப்போ வரும்?", "Tamil"),
    ]
    for text, expected in cases:
        got = brain.detect_language(text)
        assert got == expected, f"{text!r} -> {got}, expected {expected}"


def test_drops_whisper_hallucinations():
    """Whisper answers silence with stock subtitle phrases; never reply to those."""
    for junk in [
        "Thank you.", "you", "Thanks for watching!", "Bye.", " okay ", "ok",
        "धन्यवाद", "a",
        "I'm going to go to the next episode of the episode of the episode of the",
    ]:
        assert brain._clean(junk) == "", f"should have dropped {junk!r}"


def test_keeps_real_speech():
    for real in [
        "kya aaj shaam ka slot khaali hai?",
        "Thank you, aur cleaning ka charge kitna hai?",   # starts like junk, isn't
        "नमस्ते, आज शाम का स्लॉट मिलेगा?",
        "Do you have an appointment tomorrow?",
    ]:
        assert brain._clean(real) == real.strip(), f"should have kept {real!r}"


def test_parse_ignores_status_receipts():
    """Delivery/read receipts arrive on the same webhook and must not reply."""
    receipts = {"entry": [{"changes": [{"value": {"statuses": [{"status": "delivered"}]}}]}]}
    assert whatsapp.parse(receipts) == []


def test_parse_text_and_voice():
    payload = {
        "entry": [{"changes": [{"value": {
            "contacts": [{"wa_id": "919911087000", "profile": {"name": "Priya"}}],
            "messages": [
                {"from": "919911087000", "type": "text", "text": {"body": "hello"}},
                {"from": "919911087000", "type": "audio", "audio": {"id": "MEDIA-1"}},
            ],
        }}]}]
    }
    text, audio = whatsapp.parse(payload)
    assert text["name"] == "Priya" and text["text"] == "hello"
    assert audio["media_id"] == "MEDIA-1" and audio["type"] == "audio"


def test_error_description_is_never_empty():
    """httpx timeouts stringify to "", which once produced the useless
    "[auto-reply failed: ]" on a customer's thread. Always name the type."""
    import httpx

    for err in [httpx.ReadTimeout(""), httpx.ConnectTimeout(""), Exception(), ValueError("")]:
        described = whatsapp.describe(err)
        assert described.strip(), f"{type(err).__name__} produced an empty description"
        assert type(err).__name__ in described

    # A real message must still survive.
    assert "boom" in whatsapp.describe(RuntimeError("boom"))


def test_unanswered_message_is_queued_then_cleared():
    """A reply runs as a background task, so a crash between "stored" and
    "sent" used to lose it silently. needs_reply is what survives that."""
    db.init()
    phone = "919000000001"

    mid = db.add_message(phone, "them", "text", "kya khula hai?", needs_reply=True)
    assert mid, "add_message should return the new row id"

    queued = [r for r in db.pending_replies() if r["phone"] == phone]
    assert len(queued) == 1, "an unanswered message must be picked up"
    assert queued[0]["body"] == "kya khula hai?"

    db.clear_needs_reply(mid)
    assert not [r for r in db.pending_replies() if r["phone"] == phone], (
        "a message must not be answered twice"
    )


def test_pending_replies_ignores_old_messages():
    """Answering yesterday's message is worse than not answering it."""
    from datetime import datetime, timedelta, timezone

    db.init()
    phone = "919000000002"
    old = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
    db.add_message(phone, "them", "text", "purana message", needs_reply=True, at=old)

    assert not [r for r in db.pending_replies() if r["phone"] == phone]


def test_reply_needs_all_three_switches():
    """Global, allowlist and per-thread must all agree before the agent speaks —
    this is what keeps a personal number from answering friends."""
    db.init()
    phone = "919000000003"
    db.add_message(phone, "them", "text", "hi")

    db.set_allowlist([])
    db.set_auto_reply_global(True)
    db.set_auto_reply_for(phone, True)
    assert db.should_auto_reply(phone), "empty allowlist means everyone"

    db.set_auto_reply_global(False)
    assert not db.should_auto_reply(phone), "global off must silence everyone"

    db.set_auto_reply_global(True)
    db.set_allowlist(["9198000000099"])
    assert not db.should_auto_reply(phone), "a number outside the allowlist must stay silent"

    db.set_allowlist([phone])
    assert db.should_auto_reply(phone), "an allowlisted number must be answered"

    db.set_auto_reply_for(phone, False)
    assert not db.should_auto_reply(phone), "per-thread off must win"

    db.set_allowlist([])  # leave the temp db tidy for the next test


def test_allowlist_matches_any_number_format():
    """The same person arrives as 9812345678, 919812345678 and +91 98123 45678
    depending on where the number came from."""
    db.init()
    db.set_auto_reply_global(True)
    db.set_allowlist(["9000000001"])
    for form in ("9000000001", "919000000001", "+91 90000 00001", "91-90000-00001"):
        assert db.allowed(form), f"{form} should match the allowlist"
    assert not db.allowed("919999999999")
    db.set_allowlist([])


def test_estimate_seconds_never_zero():
    assert whatsapp._estimate_seconds("ok") == 1
    assert whatsapp._estimate_seconds(" ".join(["word"] * 25)) == 10


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("all passed")
