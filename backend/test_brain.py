"""Checks for the parts that are pure logic — no keys or network needed.

    python test_brain.py
"""

import brain
import whatsapp


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


def test_estimate_seconds_never_zero():
    assert whatsapp._estimate_seconds("ok") == 1
    assert whatsapp._estimate_seconds(" ".join(["word"] * 25)) == 10


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("all passed")
