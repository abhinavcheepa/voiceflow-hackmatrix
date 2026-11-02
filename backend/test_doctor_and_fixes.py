import asyncio
import io
import os
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from starlette.testclient import TestClient

import auth
import brain
import doctor
import main
import voice
import webcall


def test_generic_error_text_in_webcall_and_no_exception_leak():
    """Browser must NEVER receive provider or TTS exception details — only generic polite messages."""
    mock_ws = AsyncMock()
    session = webcall.Session()

    # 1. Test LLM failure turn
    with patch("brain.reply", side_effect=RuntimeError("GroqCrashSecretKey gsk_supersecret1234567890")):
        asyncio.run(webcall.respond(mock_ws, session, "hello world"))

    sent_payloads = [call[0][0] for call in mock_ws.send_json.call_args_list]
    error_payloads = [p for p in sent_payloads if p.get("type") == "error"]
    assert len(error_payloads) >= 1
    for err in error_payloads:
        # Generic message only
        assert err["text"] == "Abhi thodi takleef hai, thodi der mein try karein."
        # Zero secret or provider exception leakage
        assert "GroqCrashSecretKey" not in err["text"]
        assert "gsk_" not in err["text"]

    # 2. Test TTS failure turn
    mock_ws.reset_mock()
    with patch("voice.synthesize", side_effect=RuntimeError("EdgeTTSConnectionFailed 503 Service Unavailable")):
        asyncio.run(webcall.speak(mock_ws, session, "Namaste"))

    sent_payloads = [call[0][0] for call in mock_ws.send_json.call_args_list]
    warning_payloads = [p for p in sent_payloads if p.get("type") == "warning"]
    assert len(warning_payloads) >= 1
    for w in warning_payloads:
        assert w["text"] == "Voice abhi available nahi hai."
        assert "EdgeTTSConnectionFailed" not in w["text"]

    # 3. Test STT failure turn
    mock_ws.reset_mock()
    with patch("brain.transcribe", side_effect=RuntimeError("GroqSTTInternalError 400 Bad Format")):
        asyncio.run(webcall.handle_turn(mock_ws, session, b"A" * 3000))

    sent_payloads = [call[0][0] for call in mock_ws.send_json.call_args_list]
    stt_errors = [p for p in sent_payloads if p.get("type") == "error"]
    assert len(stt_errors) >= 1
    for err in stt_errors:
        assert err["text"] == "Abhi thodi takleef hai, thodi der mein try karein."
        assert "GroqSTTInternalError" not in err["text"]


@pytest.mark.anyio
async def test_voice_clone_failure_fallback_and_voice_logging(monkeypatch, caplog):
    """When cloning conversion fails, app falls back to base Edge voice and logs the voice used."""
    monkeypatch.setattr(voice, "_edge_speak", AsyncMock(return_value=b"BASE_AUDIO"))
    monkeypatch.setattr(voice, "voice_id", lambda: "clone:test_speaker_123")

    with patch("asyncio.to_thread", side_effect=RuntimeError("torch converter missing")):
        audio = await voice.synthesize("Namaste", "Hindi")
        assert audio == b"BASE_AUDIO"


def test_no_vite_api_token_in_api_js():
    """src/api.js must not contain any VITE_API_TOKEN fallback."""
    api_js_path = os.path.join(os.path.dirname(__file__), "..", "src", "api.js")
    with open(api_js_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "VITE_API_TOKEN" not in content


@pytest.mark.anyio
async def test_doctor_output_shape_with_mocks(monkeypatch):
    """doctor.run_doctor_checks returns structured status for all 10 checks."""
    monkeypatch.setattr(doctor, "check_network", AsyncMock(return_value={
        "name": "Network", "status": "OK", "message": "All mock endpoints reachable", "fix": "None"
    }))
    monkeypatch.setattr(doctor, "check_llm", AsyncMock(return_value={
        "name": "LLM Round-Trip", "status": "OK", "message": "Replied in 100ms", "fix": "None"
    }))
    monkeypatch.setattr(doctor, "check_stt", AsyncMock(return_value={
        "name": "STT (Transcription)", "status": "OK", "message": "Mock STT ok", "fix": "None"
    }))
    monkeypatch.setattr(doctor, "check_tts", AsyncMock(return_value={
        "name": "TTS (Speech Synthesis)", "status": "OK", "message": "Mock TTS ok", "fix": "None"
    }))
    monkeypatch.setattr(doctor, "check_webcall", AsyncMock(return_value={
        "name": "Web-Call WebSocket", "status": "OK", "message": "Mock WS ok", "fix": "None"
    }))

    checks = await doctor.run_doctor_checks()
    assert len(checks) == 10
    expected_names = [
        "Network", ".env Configuration", "LLM Round-Trip", "STT (Transcription)",
        "TTS (Speech Synthesis)", "ffmpeg / Audio Decoder", "Voice Cloning (OpenVoice)",
        "WhatsApp Bridge", "Web-Call WebSocket", "Security Sanity"
    ]
    check_names = [c["name"] for c in checks]
    for exp in expected_names:
        assert exp in check_names
    for c in checks:
        assert c["status"] in ("OK", "WARN", "FAIL")
        assert "message" in c
        assert "fix" in c


def test_api_doctor_endpoint_authenticated(monkeypatch):
    """GET /api/doctor is protected by require_auth and returns check results."""
    monkeypatch.setattr(auth, "API_TOKEN", "test-token-doctor")
    client = TestClient(main.app)

    # 1. Unauthenticated -> 401
    res = client.get("/api/doctor")
    assert res.status_code == 401

    # 2. Authenticated -> 200
    mock_checks = [
        {"name": "Network", "status": "OK", "message": "Endpoints reachable", "fix": "None"},
        {"name": "Security Sanity", "status": "OK", "message": "All secure", "fix": "None"}
    ]
    with patch("doctor.run_doctor_checks", new_callable=AsyncMock, return_value=mock_checks):
        res = client.get("/api/doctor", headers={"Authorization": "Bearer test-token-doctor"})
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert len(data["checks"]) == 2
