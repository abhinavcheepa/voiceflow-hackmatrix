"""Tests for the 5 fixes:
1. Voice turn 3 paths (silence/noise -> idle, rejected -> clarify, valid -> LLM)
   Short valid words ("haan", "nahi", "ji", "ha") and Romanized Hinglish never rejected.
2. Clarification ladder (max 2 wording variants, then WhatsApp handoff; reset on valid speech).
3. Loop breaker (consecutive identical tool calls, sanitize waiting/narration).
4. TTS circuit breaker (5 failures / 30s trips OPEN, 60s probe, text-only fallback).
5. WhatsApp allowlist status reporting auto_reply_mode.
"""
import asyncio
import time
from unittest.mock import AsyncMock, patch
import pytest
from starlette.testclient import TestClient

import auth
import brain
import db
import voice
import webcall
from main import app


# ============================================================================
# 1. Voice Turn 3 Paths & Short Words / Romanized Hinglish
# ============================================================================

def test_clean_preserves_valid_short_words_and_hinglish():
    """Short valid Hindi/English words ('ji', 'ha', 'haan', 'nahi') and Romanized Hinglish are kept."""
    assert brain._clean("ji") == "ji"
    assert brain._clean("ha") == "ha"
    assert brain._clean("haan") == "haan"
    assert brain._clean("nahi") == "nahi"
    assert brain._clean("no") == "no"
    # Romanized Hinglish in Latin script
    hinglish = "bill bhejna hai shivam ko 1000 ka"
    assert brain._clean(hinglish) == hinglish
    # Pure single-character noise is dropped
    assert brain._clean(".") == ""
    assert brain._clean(" ") == ""


@pytest.mark.anyio
async def test_turn_path_a_silence_noise_returns_idle_no_llm_no_tts():
    """Silence / noise path: send idle event, NO LLM call, NO speak/TTS call."""
    session = webcall.Session(assistant=True)
    mock_ws = AsyncMock()

    with patch("brain.transcribe", new_callable=AsyncMock) as mock_stt, \
         patch("brain.reply", new_callable=AsyncMock) as mock_llm, \
         patch("voice.synthesize", new_callable=AsyncMock) as mock_tts:
        # Simulate silence returned by VAD/RMS or Whisper no_speech
        async def fake_transcribe_silence(*args, **kwargs):
            brain.last_stt_status = "silence"
            brain.last_stt_reason = "vad_gate: rms too low"
            return ""

        mock_stt.side_effect = fake_transcribe_silence

        await webcall.handle_turn(mock_ws, session, b"audio-buffer" * 100)

        # Assert no LLM call and no TTS call
        assert not mock_llm.called
        assert not mock_tts.called
        # Assert 'idle' was sent to client
        mock_ws.send_json.assert_called_with({"type": "idle"})
        # History must not contain any new message
        assert len(session.history) == 0


@pytest.mark.anyio
async def test_turn_path_c_valid_speech_resets_clarification_and_calls_llm():
    """Valid speech path: resets clarification counter and calls LLM."""
    session = webcall.Session(assistant=True)
    session.clarification_count = 2  # was previously in ladder
    mock_ws = AsyncMock()

    with patch("brain.transcribe", new_callable=AsyncMock) as mock_stt, \
         patch("brain.reply", new_callable=AsyncMock) as mock_llm, \
         patch("voice.synthesize", new_callable=AsyncMock) as mock_tts:
        mock_stt.return_value = "Haan bill bana do"
        brain.last_stt_status = "valid"
        mock_llm.return_value = "Theek hai, bill bana rahi hoon."
        mock_tts.return_value = b"agent-audio"

        await webcall.handle_turn(mock_ws, session, b"audio-buffer" * 100)

        # Counter reset
        assert session.clarification_count == 0
        # LLM called
        assert mock_llm.called
        assert len(session.history) >= 2


# ============================================================================
# 2. Clarification Ladder (max 2 then handoff)
# ============================================================================

@pytest.mark.anyio
async def test_clarification_ladder_progression_and_handoff():
    """Rejected speech executes 2 distinct clarification attempts, then handoff to WhatsApp."""
    session = webcall.Session(assistant=True)
    mock_ws = AsyncMock()

    with patch("brain.transcribe", new_callable=AsyncMock) as mock_stt, \
         patch("brain.reply", new_callable=AsyncMock) as mock_llm, \
         patch("voice.synthesize", new_callable=AsyncMock) as mock_tts:
        mock_stt.return_value = ""
        brain.last_stt_status = "rejected"
        mock_tts.return_value = b"tts-audio"

        # Turn 1: First clarification
        await webcall.handle_turn(mock_ws, session, b"audio-buffer" * 100)
        assert session.clarification_count == 1
        assert not mock_llm.called
        assert "Awaaz saaf nahi aayi" in session.history[-1]["content"]

        # Turn 2: Second clarification (different wording!)
        await webcall.handle_turn(mock_ws, session, b"audio-buffer" * 100)
        assert session.clarification_count == 2
        assert not mock_llm.called
        assert "theek se nahi sun paayi" in session.history[-1]["content"]

        # Turn 3: Handoff to WhatsApp / text
        await webcall.handle_turn(mock_ws, session, b"audio-buffer" * 100)
        assert session.clarification_count == 3
        assert not mock_llm.called
        assert "WhatsApp par message karein" in session.history[-1]["content"]
        # Client receives handoff event
        mock_ws.send_json.assert_any_call({"type": "handoff", "channel": "whatsapp"})


# ============================================================================
# 3. Loop Breaker
# ============================================================================

@pytest.mark.anyio
async def test_loop_breaker_stops_identical_tool_calls():
    """If the model repeats the identical tool call consecutively, the loop breaks."""
    call_count = 0

    async def fake_chat_once(messages, with_tools=True, is_owner=False):
        nonlocal call_count
        call_count += 1
        # Always return the exact same tool call
        return {
            "role": "assistant",
            "content": "Looking up details...",
            "tool_calls": [
                {
                    "id": f"call_{call_count}",
                    "type": "function",
                    "function": {"name": "search_knowledge", "arguments": '{"query": "timings"}'},
                }
            ],
        }

    with patch.object(brain, "_chat_once", side_effect=fake_chat_once), \
         patch("tools.run", return_value="Timings are 9am to 5pm"):
        ctx = {"owner": True, "channel": "assistant"}
        ans = await brain.reply("9876543210", "Clinic timings kya hain?", tools_ctx=ctx)
        # Should break on round 2 (consecutive duplicate) rather than cycling through all 4 rounds
        assert call_count == 2
        assert ans != ""


def test_sanitize_output_strips_waiting_and_narration():
    """Sanitizer cleans (Waiting...) and narration lines completely."""
    raw = (
        "Stationery ka item name bata dijiye.(Waiting for item details)"
        "User hasn't responded yet.Aapka jawab ka intezaar kar rahi hoon."
        "Stationery ka rate kya hai?(Waiting)..."
    )
    cleaned = brain.sanitize_output(raw, is_owner=True)
    assert "(Waiting" not in cleaned
    assert "User hasn't responded" not in cleaned
    assert "intezaar" not in cleaned


# ============================================================================
# 4. TTS Circuit Breaker
# ============================================================================

@pytest.mark.anyio
async def test_tts_circuit_breaker_trips_open_and_resets():
    """5 failures in 30s trips circuit breaker to OPEN; subsequent call raises immediately; 60s allows HALF_OPEN probe."""
    cb = voice.circuit_breaker
    cb.reset()
    assert cb.state == "CLOSED"

    # Simulate 5 failures
    for _ in range(5):
        cb.record_failure()
    assert cb.state == "OPEN"
    assert cb.can_attempt() is False

    # synthesize() raises immediately without network call
    with pytest.raises(RuntimeError, match="TTS circuit breaker is OPEN"):
        await voice.synthesize("Namaste")

    # Fast forward time past 60s
    cb.opened_at = time.time() - 65.0
    assert cb.can_attempt() is True
    assert cb.state == "HALF_OPEN"

    # Probe succeeds -> resets to CLOSED
    cb.record_success()
    assert cb.state == "CLOSED"


# ============================================================================
# 5. WhatsApp Status Auto-Reply Mode
# ============================================================================

def test_whatsapp_status_includes_auto_reply_mode(monkeypatch):
    """GET /api/whatsapp/status returns auto_reply_mode."""
    token = "test_token_mode"
    monkeypatch.setattr(auth, "API_TOKEN", token)
    monkeypatch.setenv("AUTO_REPLY_MODE", "allowlist")

    client = TestClient(app)
    res = client.get("/api/whatsapp/status", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    data = res.json()
    assert data.get("auto_reply_mode") == "allowlist"
