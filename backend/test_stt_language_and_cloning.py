"""Tests for STT language constraint, doctor --cloning, WhatsApp web mode failure,
and WhatsApp trace logging.
"""
import io
import os
import sys
import logging
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

sys.path.insert(0, os.path.dirname(__file__))

import brain
import doctor
import db
import voice
import whatsapp


@pytest.mark.anyio
async def test_stt_groq_outlier_language_retries_with_fallback(monkeypatch):
    """When Groq detects an outlier language (e.g. Icelandic 'is' for 'Hello'),
    it retries with the effective fallback language ('en').
    """
    calls = []

    async def mock_post(client, url, headers=None, files=None, data=None, timeout=None):
        calls.append(dict(data))
        mock_resp = MagicMock()
        if len(calls) == 1:
            # First attempt: misdetected as Icelandic 'is'
            mock_resp.json.return_value = {
                "text": "Halló!",
                "language": "is",
                "duration": 0.8,
                "segments": [{"no_speech_prob": 0.05, "text": "Halló!"}],
            }
        else:
            # Second attempt: forced fallback to English
            mock_resp.json.return_value = {
                "text": "Hello!",
                "language": "en",
                "duration": 0.8,
                "segments": [{"no_speech_prob": 0.05, "text": "Hello!"}],
            }
        return mock_resp

    monkeypatch.setattr(brain, "_post", mock_post)
    monkeypatch.setattr(brain, "stt_backend", lambda: "groq")
    monkeypatch.setattr(brain, "GROQ_KEY", "gsk_dummy_test_key")

    # Audio sample (WAV header)
    audio = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00D\xac\x00\x00\x88X\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00"

    result = await brain.transcribe(audio, "test.wav", fallback_language="en")
    assert result == "Hello!"
    assert len(calls) == 2
    assert "language" not in calls[0] or calls[0]["language"] is None
    assert calls[1]["language"] == "en"


@pytest.mark.anyio
async def test_stt_language_hindi_english_hinglish_hints(monkeypatch):
    """Bilingual Hindi/English/Hinglish hints are passed in STT prompt."""
    captured_prompts = []

    async def mock_post(client, url, headers=None, files=None, data=None, timeout=None):
        captured_prompts.append(data.get("prompt", ""))
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "text": "नमस्ते",
            "language": "hi",
            "duration": 2.5,
            "segments": [{"no_speech_prob": 0.01}],
        }
        return mock_resp

    monkeypatch.setattr(brain, "_post", mock_post)
    monkeypatch.setattr(brain, "stt_backend", lambda: "groq")
    monkeypatch.setattr(brain, "GROQ_KEY", "gsk_dummy_test_key")

    audio = b"RIFF" + b"\x00" * 40
    res = await brain.transcribe(audio, "hindi.wav", vocab="Kanishk")
    assert "Hindi, English, Hinglish" in captured_prompts[0]
    assert "Kanishk" in captured_prompts[0]
    assert res == "नमस्ते"


def test_doctor_whatsapp_web_mode_fails_offline_or_empty_allowlist(monkeypatch):
    """In WhatsApp web mode, bridge Offline or allowlist empty must be FAIL."""
    monkeypatch.setenv("WHATSAPP_PROVIDER", "web")
    monkeypatch.setenv("AUTO_REPLY_MODE", "allowlist")
    monkeypatch.setattr(db, "get_allowlist", lambda: [])

    # Case 1: Bridge unreachable + empty allowlist -> FAIL
    res = doctor.check_whatsapp()
    assert res["status"] == "FAIL"
    assert "Start wa-bridge" in res["fix"]
    assert "allowlist" in res["fix"]


def test_doctor_cloning_deep_check():
    """check_cloning_deep returns structured verification of all cloning parts."""
    res = doctor.check_cloning_deep()
    assert res["name"] == "Voice Cloning (--cloning)"
    assert res["status"] in ("OK", "FAIL")
    assert "checkpoints_v2" in res["message"]
    assert "PyTorch" in res["message"]


@pytest.mark.anyio
async def test_whatsapp_trace_logs_emitted(caplog):
    """WhatsApp message handling emits structured [whatsapp:trace] log lines."""
    caplog.set_level(logging.INFO)

    import time
    msg = {
        "id": f"trace-test-{time.time()}",
        "from": "919876543210",
        "name": "Rohan",
        "type": "text",
        "text": "Hello, need appointment",
    }

    with patch("whatsapp.reply_to", new_callable=AsyncMock) as mock_reply:
        with patch("db.auto_reply_block", return_value=""):
            with patch("db.is_opted_out", return_value=False):
                with patch("db.auto_reply_global", return_value=True):
                    with patch("db.get_setting", return_value="on"):
                        await whatsapp.handle(msg)

    logs = [rec.message for rec in caplog.records if "[whatsapp:trace]" in rec.message]
    assert any("stored:" in l for l in logs)
    assert any("handle() decision:" in l for l in logs)


@pytest.mark.anyio
async def test_voice_logging_actual_voice_used(caplog):
    """Synthesize logs the exact voice used on every turn."""
    caplog.set_level(logging.INFO)

    with patch("voice._edge_speak", new_callable=AsyncMock, return_value=b"fake-mp3"):
        await voice.synthesize("Namaste", language="Hindi", as_voice="hi-IN-SwaraNeural")

    logs = [rec.message for rec in caplog.records if "Reply synthesized using Edge voice" in rec.message]
    assert len(logs) >= 1
    assert "hi-IN-SwaraNeural" in logs[0]
