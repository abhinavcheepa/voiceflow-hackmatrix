"""Tests for ISSUE-01: voice profile endpoint and cloning.missing() function."""

from starlette.testclient import TestClient
import auth
import cloning
import voice
from main import app

client = TestClient(app)


def _auth_headers():
    return {"Authorization": f"Bearer {auth.API_TOKEN}"} if auth.API_TOKEN else {}


def test_cloning_missing_defined():
    """cloning.missing() must be defined and return a string or None."""
    res = cloning.missing()
    assert res is None or isinstance(res, str)


def test_voice_profile_endpoint_with_cloning_available(monkeypatch):
    """GET /api/voice/profile returns 200 with canClone=True when cloning is available."""
    monkeypatch.setattr(cloning, "missing", lambda: None)
    monkeypatch.setattr(voice, "PROVIDER", "edge")

    resp = client.get("/api/voice/profile", headers=_auth_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert data["canClone"] is True
    assert data["cloneMissing"] is None


def test_voice_profile_endpoint_with_cloning_missing(monkeypatch):
    """GET /api/voice/profile returns 200 with canClone=False and reason when cloning deps missing."""
    monkeypatch.setattr(cloning, "missing", lambda: "torch is not installed")
    monkeypatch.setattr(voice, "PROVIDER", "edge")

    resp = client.get("/api/voice/profile", headers=_auth_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert data["canClone"] is False
    assert data["cloneMissing"] == "torch is not installed"


def test_missing_returns_none_when_all_present(monkeypatch, tmp_path):
    """cloning.missing() returns None when torch is available and checkpoint exists, package-independent."""
    import sys
    from unittest.mock import MagicMock
    fake_ckpt = tmp_path / "converter" / "checkpoint.pth"
    fake_ckpt.parent.mkdir(parents=True)
    fake_ckpt.write_text("fake-weights")

    monkeypatch.setattr(cloning, "_CHECKPOINTS_DIR", tmp_path)
    monkeypatch.setitem(sys.modules, "torch", MagicMock())

    assert cloning.missing() is None


def test_missing_returns_reason_when_weights_missing(monkeypatch, tmp_path):
    """cloning.missing() returns descriptive error when checkpoint is absent."""
    import sys
    from unittest.mock import MagicMock
    monkeypatch.setattr(cloning, "_CHECKPOINTS_DIR", tmp_path / "nonexistent")
    monkeypatch.setitem(sys.modules, "torch", MagicMock())

    res = cloning.missing()
    assert res is not None
    assert "OpenVoice v2 checkpoint not found" in res
