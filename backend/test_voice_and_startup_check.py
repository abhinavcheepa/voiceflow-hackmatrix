import pytest
import os
from unittest.mock import patch, MagicMock
from starlette.testclient import TestClient
import main
import brain
import voice
import recording
import whatsapp


def test_stt_status_structure():
    """stt_status returns structured details of current transcription backend."""
    status = brain.stt_status()
    assert "status" in status
    assert status["backend"] in ("local", "groq")
    if status["backend"] == "local":
        assert "model" in status
        assert "compute" in status


def test_tts_status_structure():
    """tts_status returns structured details of current TTS provider."""
    status = voice.tts_status()
    assert "status" in status
    assert "provider" in status
    assert status["status"] in ("ok", "fail", "unknown")


def test_ffmpeg_status_structure():
    """ffmpeg_status reports whether ffmpeg executable is available."""
    status = recording.ffmpeg_status()
    assert "status" in status
    assert status["status"] in ("ok", "fail")
    if status["status"] == "ok":
        assert os.path.exists(status["path"])


def test_llm_status_structure():
    """llm_status reports whether chat LLM credentials are configured."""
    status = brain.llm_status()
    assert "status" in status
    assert status["status"] in ("ok", "fail")


@pytest.mark.anyio
async def test_startup_self_check_returns_all_components():
    """run_startup_self_check inspects STT, TTS, ffmpeg, LLM, and WhatsApp."""
    chk = await main.run_startup_self_check()
    assert "stt" in chk
    assert "tts" in chk
    assert "ffmpeg" in chk
    assert "llm" in chk
    assert "whatsapp" in chk


def test_health_endpoint_exposes_self_check():
    """GET /health exposes ffmpeg availability and structured self_check."""
    client = TestClient(main.app)
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert "ffmpeg" in data
    assert "self_check" in data
    assert "stt" in data["self_check"]
    assert "tts" in data["self_check"]
    assert "ffmpeg" in data["self_check"]
    assert "llm" in data["self_check"]


def test_whisper_model_env_configurable(monkeypatch):
    """WHISPER_MODEL and WHISPER_COMPUTE configure the model and compute type."""
    monkeypatch.setenv("WHISPER_MODEL", "base")
    monkeypatch.setenv("WHISPER_COMPUTE", "int8")
    status = brain.stt_status()
    if status["backend"] == "local":
        assert status["model"] == "base"
        assert status["compute"] == "int8"


@pytest.mark.anyio
async def test_edge_tts_failure_provides_clear_error_message(monkeypatch):
    """When edge_tts fails with connection error, synthesize raises clear error message."""
    monkeypatch.setattr(voice, "PROVIDER", "edge")
    with patch("voice._edge_speak", side_effect=RuntimeError("Edge TTS failed (no internet / speech.platform.bing.com unreachable)")):
        with pytest.raises(RuntimeError) as exc_info:
            await voice.synthesize("Hello")
        assert "no internet" in str(exc_info.value) or "unreachable" in str(exc_info.value)



def test_mask_error_masks_sensitive_tokens():
    """mask_error prevents API keys and bearer tokens from leaking into logs or errors."""
    import webcall
    err_str = "Error: Groq request failed with key gsk_abcdef1234567890 and Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    masked = webcall.mask_error(err_str)
    assert "gsk_abcdef1234567890" not in masked
    assert "gsk_***" in masked
    assert "Bearer ***" in masked


@pytest.mark.anyio
async def test_webcall_llm_failure_does_not_blame_mic(monkeypatch):
    """When LLM provider fails, webcall does NOT blame the caller's mic (sun nahi paayi)

    and instead emits a distinct server-issue message, masking any raw tokens in logs.
    """
    from unittest.mock import AsyncMock
    import webcall

    mock_ws = AsyncMock()
    session = webcall.Session()

    spoken_texts = []
    async def mock_speak(ws, sess, text):
        spoken_texts.append(text)

    monkeypatch.setattr(webcall, "speak", mock_speak)
    with patch("brain.reply", side_effect=RuntimeError("Groq 500 server error: gsk_testsecret123456")):
        await webcall.respond(mock_ws, session, "kya haal hai")

    # Verify no mic blaming
    for spoken in spoken_texts:
        assert "sun nahi paayi" not in spoken
        assert "sun nahi paaya" not in spoken
        assert "takleef" in spoken

    # Verify error sent to client is generic (Fix B.1: browser never sees provider exception text)
    error_sent = False
    for call in mock_ws.send_json.call_args_list:
        payload = call[0][0]
        if payload.get("type") == "error":
            error_sent = True
            assert "gsk_testsecret123456" not in payload.get("text", "")
            assert "Groq 500" not in payload.get("text", "")
            assert payload.get("text") == "Abhi thodi takleef hai, thodi der mein try karein."
    assert error_sent
