import asyncio
import io
import math
import struct
import wave
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import brain
import db
import tools
import webcall


@pytest.fixture(autouse=True)
def clean_db(tmp_path, monkeypatch):
    """Use an isolated SQLite database for each test."""
    db_file = tmp_path / "test_voice_rel.db"
    orig_path = db.DB_PATH
    monkeypatch.setattr(db, "DB_PATH", db_file)
    db.init()
    yield
    monkeypatch.setattr(db, "DB_PATH", orig_path)


# ============================================================================
# 1. LLM 429 & 413 Handling Tests
# ============================================================================


def test_llm_429_retry_with_backoff_success(monkeypatch):
    """Mock 429 on first call, honoring Retry-After, succeeds on retry."""
    monkeypatch.setattr(brain, "CHAT_API_KEY", "test-key")
    attempts = 0

    class FakeResponse:
        def __init__(self, status_code, data, headers=None):
            self.status_code = status_code
            self._data = data
            self.headers = headers or {}
            self.text = str(data)

        def json(self):
            return self._data

    async def fake_post(url, headers=None, json=None, timeout=None):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return FakeResponse(429, {"error": "rate limit"}, headers={"Retry-After": "0.01"})
        return FakeResponse(200, {
            "choices": [{"message": {"role": "assistant", "content": "Bill ready."}}],
            "usage": {"prompt_tokens": 42},
        })

    with patch("brain.http") as mock_http, patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        mock_client = MagicMock()
        mock_client.post = fake_post
        mock_http.return_value = mock_client

        msg = asyncio.run(brain._chat_once([{"role": "user", "content": "hello"}], with_tools=False))

    assert attempts == 2
    assert msg["content"] == "Bill ready."
    assert mock_sleep.called
    assert brain.last_turn_stats["prompt_tokens"] == 42
    assert "200" in brain.last_turn_stats["llm_status"]


def test_llm_413_trimmed_retry_success(monkeypatch):
    """Mock 413 payload too large triggers message trimming and succeeds on retry."""
    monkeypatch.setattr(brain, "CHAT_API_KEY", "test-key")
    attempts = 0
    payloads_sent = []

    class FakeResponse:
        def __init__(self, status_code, data):
            self.status_code = status_code
            self._data = data
            self.headers = {}
            self.text = str(data)

        def json(self):
            return self._data

    async def fake_post(url, headers=None, json=None, timeout=None):
        nonlocal attempts
        attempts += 1
        payloads_sent.append(json)
        if attempts == 1:
            return FakeResponse(413, {"error": "payload too large"})
        return FakeResponse(200, {
            "choices": [{"message": {"role": "assistant", "content": "Trimmed reply."}}],
            "usage": {"prompt_tokens": 20},
        })

    long_history = [
        {"role": "system", "content": "System prompt"},
        {"role": "user", "content": "Turn 1" * 50},
        {"role": "assistant", "content": "Reply 1" * 50},
        {"role": "user", "content": "Turn 2" * 50},
        {"role": "assistant", "content": "Reply 2" * 50},
        {"role": "user", "content": "Turn 3 latest"},
    ]

    with patch("brain.http") as mock_http:
        mock_client = MagicMock()
        mock_client.post = fake_post
        mock_http.return_value = mock_client

        msg = asyncio.run(brain._chat_once(long_history, with_tools=False))

    assert attempts == 2
    assert msg["content"] == "Trimmed reply."
    # The retried payload must have trimmed the messages
    assert len(payloads_sent[1]["messages"]) < len(payloads_sent[0]["messages"])


# ============================================================================
# 2. STT VAD Gate & Hallucination Guard Tests
# ============================================================================


def test_vad_silence_wav_rejected_without_llm():
    """Silence WAV audio fails RMS/VAD gate and returns empty transcript."""
    silence_buf = io.BytesIO()
    with wave.open(silence_buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000)  # 1.0s pure silence

    audio_bytes = silence_buf.getvalue()
    ok, reason = brain.check_audio_loudness(audio_bytes)
    assert not ok
    assert "too quiet" in reason

    # transcribe() rejects it before making any external call
    with patch("brain._transcribe_groq") as mock_groq:
        result = asyncio.run(brain.transcribe(audio_bytes))
        assert result == ""
        assert not mock_groq.called


def test_vad_short_audio_rejected():
    """Audio clips under minimum duration are rejected before STT."""
    short_buf = io.BytesIO()
    with wave.open(short_buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x01\x00" * 1000)  # ~0.06s

    ok, reason = brain.check_audio_loudness(short_buf.getvalue())
    assert not ok
    assert "too short" in reason


def test_white_noise_stt_segments_dropped(monkeypatch):
    """White noise returns high no_speech_prob/low logprob segments dropped by verbose_json filter."""
    monkeypatch.setattr(brain, "GROQ_KEY", "gsk_dummy_test_key")
    monkeypatch.setattr(brain, "stt_backend", lambda: "groq")

    async def mock_post(client, url, headers=None, files=None, data=None, timeout=None):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "text": "shhh static noise",
            "language": "en",
            "duration": 1.2,
            "segments": [
                {"text": "shhh static", "no_speech_prob": 0.85, "avg_logprob": -1.4, "compression_ratio": 1.1}
            ],
        }
        return mock_resp

    monkeypatch.setattr(brain, "_post", mock_post)
    noise_buf = io.BytesIO()
    with wave.open(noise_buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack("<16000h", *([1000, -1000] * 8000)))

    res = asyncio.run(brain.transcribe(noise_buf.getvalue(), "noise.wav"))
    assert res == ""


def test_degenerate_hallucination_strings_dropped():
    """Known hallucinated phrases, name dumps, and degenerate patterns are rejected."""
    hallucinations = [
        "END CALL Hello No comment What!",
        "Video of silence. Halka, Vmy, Ibu, Sikhi, Desi, Akshat, Parm and Yash Jain...",
        "Thank you for watching",
        "Thanks for watching!",
        "Please subscribe to my channel",
        "Akshat, Parm and Yash Jain",
        "Halka, Vmy, Ibu, Sikhi, Desi",
    ]
    for text in hallucinations:
        cleaned = brain._clean(text)
        assert cleaned == "", f"Expected '{text}' to be dropped as hallucination, got '{cleaned}'"

    # Real spoken input must not be dropped
    valid_speech = "Bill 1000 rupaye ka Shivam ko bhej do"
    assert brain._clean(valid_speech) == valid_speech


def test_rejected_stt_prompts_user_without_llm_call():
    """When audio is rejected, the assistant replies 'Awaaz saaf nahi aayi' with NO LLM call."""
    session = webcall.Session(assistant=True)
    mock_ws = AsyncMock()

    # Pass audio that transcribe returns empty for
    with patch("brain.transcribe", new_callable=AsyncMock) as mock_stt, patch("brain.reply") as mock_llm, patch("voice.synthesize", new_callable=AsyncMock) as mock_tts:
        mock_stt.return_value = ""  # rejected / silence
        mock_tts.return_value = b"dummy audio"

        asyncio.run(webcall.handle_turn(mock_ws, session, b"small-audio-buffer" * 200))

        assert not mock_llm.called
        # Verify it said "Awaaz saaf nahi aayi, dobara bolein."
        last_msg = session.history[-1]
        assert "Awaaz saaf nahi aayi" in last_msg["content"]


# ============================================================================
# 3. Bill Flow: Mismatch & Contact Resolution
# ============================================================================


def test_bill_flow_total_mismatch_asks_one_question():
    """When items sum to 9,000 (4,000 + 5,000) and total is 10,000, ask ONE question."""
    ctx = {"owner": True, "channel": "assistant"}

    # 1. Start bill
    tools.run("create_bill", {"customer_name": "Ravi"}, ctx)
    tools.run("add_bill_item", {"item_name": "Item A", "quantity": 1, "rate": 4000}, ctx)
    tools.run("add_bill_item", {"item_name": "Item B", "quantity": 1, "rate": 5000}, ctx)

    # 2. Update total to 10,000 without explanation -> triggers mismatch question
    res = tools.run("update_bill", {"total": 10000}, ctx)
    assert "Items ka jod 9,000 hai, total 10,000 — 1,000 aur kis cheez ke?" in res

    # 3. Confirmation with "other charges" accepts it and finalizes bill
    res2 = tools.run("update_bill", {"total": 10000, "item_name": "Other charges"}, ctx)
    assert "finalized" in res2
    assert "10000" in res2

    bill = db.get_bill(ctx["_active_bill_id"])
    assert bill["total"] == 10000
    assert any("Other charges" in i["name"] for i in bill["items"])


def test_customer_name_resolution_not_found():
    """If customer is not in contacts, state '{name} naam ka contact nahi mila...'."""
    ctx = {"owner": True, "channel": "assistant"}
    # On send_bill
    res = asyncio.run(tools.run("send_bill", {"name": "Akshat Soni"}, ctx))
    assert "Akshat Soni naam ka contact nahi mila, number bataiye ya contact add karein" in res

    # On update_bill with an active draft
    tools.run("create_bill", {"customer_name": "Ravi", "total": 500}, ctx)
    res2 = tools.run("update_bill", {"customer_name": "Akshat Soni"}, ctx)
    assert "Akshat Soni naam ka contact nahi mila, number bataiye ya contact add karein" in res2


# ============================================================================
# 4. Phone Number Inquiry & Canned Send Guard
# ============================================================================


def test_phone_number_inquiry_states_last_4_digits_only():
    """Phone number question states last 4 digits only, never full number by voice."""
    db.touch_contact("919876541234", "Akshat Soni")

    ans = webcall.check_phone_number_query("Akshat ka phone number kya hai?")
    assert ans is not None
    assert "Akshat Soni ka number 91 XXXXX 1234 par khatam hota hai" in ans
    assert "dashboard mein hai" in ans
    assert "9876541234" not in ans  # Full number must NOT be in spoken output


def test_phone_number_inquiry_not_found():
    ans = webcall.check_phone_number_query("Unknown person ka number kya hai?")
    assert ans is not None
    assert "Unknown person naam ka contact nahi mila, number bataiye ya contact add karein" in ans


def test_canned_not_sent_only_triggers_on_send_turns():
    """Inquiries like phone questions are not overwritten by 'Abhi kuch bheja nahi gaya hai...'."""
    session = webcall.Session(assistant=True)
    db.touch_contact("919876541234", "Akshat Soni")
    mock_ws = AsyncMock()

    with patch("voice.synthesize", new_callable=AsyncMock) as mock_tts:
        mock_tts.return_value = b"audio"

        asyncio.run(webcall.respond(mock_ws, session, "Akshat ka phone number kya hai?"))

        last_spoken = session.history[-1]["content"]
        assert "Abhi kuch bheja nahi gaya hai" not in last_spoken
        assert "1234 par khatam hota hai" in last_spoken
