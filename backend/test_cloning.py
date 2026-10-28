"""Checks for the local voice-cloning pipeline wiring (voice.py <-> cloning.py).

The converter itself needs torch + OpenVoice; these tests swap it for a fake
so the routing — enrol, speak through the clone, fall back, delete — is checked
on any machine.

    python -m pytest test_cloning.py
"""

import asyncio
import os
import tempfile

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.pop("VOICE_GENDER", None)
os.environ["VOICE_PROVIDER"] = "edge"

import cloning  # noqa: E402
import db  # noqa: E402
import voice  # noqa: E402

db.init()

calls: dict = {}


def _fake(monkeypatch, convert_ok=True):
    calls.clear()
    monkeypatch.setattr(cloning, "missing", lambda: None)
    monkeypatch.setattr(cloning, "enroll", lambda clips: calls.setdefault("enroll", clips) and "abc123")
    monkeypatch.setattr(cloning, "delete", lambda cid: calls.setdefault("deleted", cid))

    def convert(base, cid):
        if not convert_ok:
            raise RuntimeError("torch exploded")
        calls["convert"] = (base, cid)
        return b"CLONED"

    monkeypatch.setattr(cloning, "convert", convert)

    async def edge(text, vid, locale):
        calls["edge"] = vid
        return b"BASE"

    monkeypatch.setattr(voice, "_edge_speak", edge)


def test_clone_enrols_every_clip_and_speaks_through_the_converter(monkeypatch):
    _fake(monkeypatch)
    vid = asyncio.run(voice.clone("Owner", [("a.webm", b"1", "audio/webm"),
                                            ("b.webm", b"2", "audio/webm")], gender_="male"))
    assert vid == "clone:abc123"
    assert calls["enroll"] == [b"1", b"2"]
    assert voice.profile()["cloned"] and voice.gender() == "male"

    assert asyncio.run(voice.synthesize("Namaste", "Hindi")) == b"CLONED"
    # Base voice matches the owner's gender and the caller's language.
    assert calls["edge"] == "hi-IN-MadhurNeural"
    assert calls["convert"] == (b"BASE", "abc123")
    asyncio.run(voice.synthesize("Hello", "English"))
    assert calls["edge"] == "en-IN-PrabhatNeural"


def test_a_failed_conversion_still_speaks(monkeypatch):
    _fake(monkeypatch, convert_ok=False)
    asyncio.run(voice.clone("Owner", [("a.webm", b"1", "audio/webm")]))
    assert asyncio.run(voice.synthesize("Namaste", "Hindi")) == b"BASE"


def test_deleting_the_clone_removes_its_embedding(monkeypatch):
    _fake(monkeypatch)
    asyncio.run(voice.clone("Owner", [("a.webm", b"1", "audio/webm")]))
    asyncio.run(voice.delete_clone())
    assert calls["deleted"] == "abc123"
    assert not voice.profile()["cloned"]


def test_clone_explains_what_to_install(monkeypatch):
    monkeypatch.setattr(cloning, "missing", lambda: "torch is not installed")
    try:
        asyncio.run(voice.clone("Owner", [("a.webm", b"1", "audio/webm")]))
    except RuntimeError as e:
        assert "torch is not installed" in str(e)
    else:
        raise AssertionError("must refuse with the install hint")
    assert voice.profile()["cloneMissing"] == "torch is not installed"


def test_clone_ids_cannot_escape_the_voices_folder():
    try:
        cloning._path("../../etc/passwd")
    except ValueError:
        pass
    else:
        raise AssertionError("path traversal must be rejected")
