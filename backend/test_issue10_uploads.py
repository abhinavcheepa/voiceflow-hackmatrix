"""Tests for ISSUE-10: File upload limits, magic-byte inspection, DoS prevention,
and Caddy/Docker owner verification.
"""

import io
import asyncio
import pytest
from fastapi import HTTPException
from starlette.testclient import TestClient
from unittest.mock import patch, MagicMock

import assistant_auth
import main
from main import app
import upload_security
import whatsapp


@pytest.fixture
def auth_headers(monkeypatch):
    import auth
    token = "test_api_token_issue10"
    monkeypatch.setattr(auth, "API_TOKEN", token)
    return {"Authorization": f"Bearer {token}"}

def test_kb_upload_oversized_rejected_413(auth_headers):
    """Uploading a document exceeding MAX_KB_FILE_BYTES returns HTTP 413."""
    client = TestClient(app)
    # 5MB + 1KB
    huge_data = b"A" * (upload_security.MAX_KB_FILE_BYTES + 1024)
    files = {"file": ("large_doc.txt", io.BytesIO(huge_data), "text/plain")}
    res = client.post("/api/kb", files=files, headers=auth_headers)
    assert res.status_code == 413
    assert "exceeded" in res.json()["detail"].lower() or "exceeds" in res.json()["detail"].lower()


def test_kb_upload_wrong_magic_bytes_rejected(auth_headers):
    """Documents with deceptive extensions but invalid magic bytes (EXE, ZIP, corrupted PDF) are rejected (HTTP 400)."""
    client = TestClient(app)

    # 1. Executable masquerading as PDF
    exe_as_pdf = b"MZ\x90\x00\x03\x00\x00\x00fake_windows_binary"
    res1 = client.post("/api/kb", files={"file": ("report.pdf", io.BytesIO(exe_as_pdf), "application/pdf")}, headers=auth_headers)
    assert res1.status_code == 400
    assert "disallowed" in res1.json()["detail"].lower() or "invalid" in res1.json()["detail"].lower()

    # 2. ZIP archive / zip bomb masquerading as text document
    zip_as_txt = b"PK\x03\x04\x14\x00\x00\x00fake_zip_bomb_header"
    res2 = client.post("/api/kb", files={"file": ("notes.txt", io.BytesIO(zip_as_txt), "text/plain")}, headers=auth_headers)
    assert res2.status_code == 400
    assert "disallowed" in res2.json()["detail"].lower() or "zip archive" in res2.json()["detail"].lower()

    # 3. Text file masquerading as PDF without %PDF- magic bytes
    txt_as_pdf = b"Just plain text without the required PDF magic bytes header."
    res3 = client.post("/api/kb", files={"file": ("invoice.pdf", io.BytesIO(txt_as_pdf), "application/pdf")}, headers=auth_headers)
    assert res3.status_code == 400
    assert "missing %pdf-" in res3.json()["detail"].lower() or "not a valid pdf" in res3.json()["detail"].lower()

    # 4. Binary file with null bytes masquerading as TXT
    binary_as_txt = b"Some initial text\x00\x01\x02hidden binary payload"
    res4 = client.post("/api/kb", files={"file": ("readme.txt", io.BytesIO(binary_as_txt), "text/plain")}, headers=auth_headers)
    assert res4.status_code == 400
    assert "binary" in res4.json()["detail"].lower()


def test_audio_upload_oversized_rejected_413(auth_headers):
    """Uploading an audio sample exceeding MAX_AUDIO_SAMPLE_BYTES returns HTTP 413."""
    client = TestClient(app)
    # Exceeds 15MB
    huge_sample = b"RIFF" + b"\x00" * 4 + b"WAVE" + b"X" * (upload_security.MAX_AUDIO_SAMPLE_BYTES + 2048)
    files = [("samples", ("sample.wav", io.BytesIO(huge_sample), "audio/wav"))]
    res = client.post("/api/voice/clone", data={"name": "OversizedSpeaker"}, files=files, headers=auth_headers)
    assert res.status_code == 413
    assert "exceeded" in res.json()["detail"].lower() or "exceeds" in res.json()["detail"].lower()


def test_audio_upload_wrong_magic_bytes_rejected(auth_headers):
    """Audio files without valid audio magic bytes are rejected (HTTP 400)."""
    client = TestClient(app)

    # 1. Plain text masquerading as WAV
    fake_wav = b"This is not a real audio file, just text bytes."
    files1 = [("samples", ("voice.wav", io.BytesIO(fake_wav), "audio/wav"))]
    res1 = client.post("/api/voice/clone", data={"name": "FakeSpeaker"}, files=files1, headers=auth_headers)
    assert res1.status_code == 400
    assert "invalid audio format" in res1.json()["detail"].lower() or "magic bytes" in res1.json()["detail"].lower()

    # 2. Executable masquerading as MP3
    fake_mp3 = b"MZ\x90\x00\x03\x00\x00\x00executable_bytes"
    files2 = [("samples", ("voice.mp3", io.BytesIO(fake_mp3), "audio/mpeg"))]
    res2 = client.post("/api/voice/clone", data={"name": "ExeSpeaker"}, files=files2, headers=auth_headers)
    assert res2.status_code == 400
    assert "dangerous" in res2.json()["detail"].lower()


def test_upload_filename_traversal_sanitized():
    """Malicious paths with directory traversal and control characters are safely sanitized."""
    assert upload_security.sanitize_filename("../../../../etc/passwd") == "passwd"
    assert upload_security.sanitize_filename("..\\..\\windows\\system32\\cmd.exe") == "cmd.exe"
    assert upload_security.sanitize_filename("folder/sub/sample.wav") == "sample.wav"
    assert upload_security.sanitize_filename("\x00weird\x1fname.pdf") == "weirdname.pdf"
    assert upload_security.sanitize_filename("...") == "sanitized_file"
    assert upload_security.sanitize_filename("") == "unnamed_upload"


def test_decompression_bomb_or_huge_pdf_rejected_or_timed_out():
    """A PDF with more than MAX_PDF_PAGES (100) is rejected."""
    mock_reader = MagicMock()
    mock_reader.pages = [MagicMock()] * 105  # 105 pages exceeds 100 page limit
    with patch("pypdf.PdfReader", return_value=mock_reader):
        with pytest.raises(ValueError, match="PDF exceeds page limit"):
            import kb
            kb.extract_text("huge.pdf", b"%PDF-1.4\n...")


@pytest.mark.anyio
async def test_concurrent_uploads_quota_enforced():
    """Exceeding MAX_CONCURRENT_UPLOADS_PER_IP raises HTTP 429."""
    manager = upload_security.UploadQuotaManager(max_concurrent=2)
    ip = "203.0.113.88"

    await manager.acquire(ip)
    await manager.acquire(ip)

    # 3rd upload must be rejected with 429
    with pytest.raises(HTTPException) as exc:
        await manager.acquire(ip)
    assert exc.value.status_code == 429

    # Releasing slot allows subsequent acquire
    await manager.release(ip)
    await manager.acquire(ip)  # Should succeed now


def test_caddy_peer_docker_ip_cannot_grant_owner_without_session_token(monkeypatch):
    """A request passing through Caddy (socket peer in 172.18.x, XFF is a public IP)
    must NOT get owner mode without a valid session token."""
    client = TestClient(app)
    monkeypatch.setenv("TRUSTED_PROXIES", "172.16.0.0/12, 127.0.0.1/32")
    import importlib
    import webcall_gate
    importlib.reload(webcall_gate)

    headers_caddy = {
        "X-Forwarded-For": "203.0.113.5",  # Public remote client IP
    }

    # 1. Requesting mode=assistant without token from Caddy proxy -> 403 Forbidden
    res_no_token = client.post(
        "/api/web-call/ticket?mode=assistant",
        headers=headers_caddy
    )
    assert res_no_token.status_code == 403
    assert "session token" in res_no_token.json()["detail"].lower()

    # 2. Requesting mode=assistant with invalid token -> 403 Forbidden
    res_bad_token = client.post(
        "/api/web-call/ticket?mode=assistant",
        headers={**headers_caddy, "Authorization": "Bearer invalid_session_token"}
    )
    assert res_bad_token.status_code == 403

    # 3. Requesting mode=assistant with valid session token -> 200 OK
    monkeypatch.setenv("ASSISTANT_PIN", "ValidPasscode123")
    token = assistant_auth.auth_manager.issue_session_token(ip="203.0.113.5")
    res_valid_token = client.post(
        "/api/web-call/ticket?mode=assistant",
        headers={**headers_caddy, "Authorization": f"Bearer {token}"}
    )
    assert res_valid_token.status_code == 200
    assert "ticket" in res_valid_token.json()


def test_allow_unsigned_webhooks_ignored_in_production(monkeypatch):
    """When ENV=production, ALLOW_UNSIGNED_WEBHOOKS=true is strictly ignored (fails closed 403)."""
    client = TestClient(app)
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("ALLOW_UNSIGNED_WEBHOOKS", "true")
    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)

    raw_body = b'{"object": "whatsapp_business_account"}'
    assert whatsapp.verify_signature(raw_body, None) is False

    res = client.post("/api/whatsapp/webhook", data=raw_body, headers={"Content-Type": "application/json"})
    assert res.status_code == 403
