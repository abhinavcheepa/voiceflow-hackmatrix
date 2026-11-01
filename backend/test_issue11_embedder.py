"""Test for ISSUE-11: KB embedder timeout, offline mode, and BM25 fallback."""

import os
import time
from unittest.mock import patch
import pytest

import kb


def test_offline_mode_skips_embedding(monkeypatch):
    """When VF_OFFLINE=1, _get_embedder returns None immediately without importing fastembed."""
    monkeypatch.setenv("VF_OFFLINE", "1")
    monkeypatch.setattr(kb, "_embedder", None)
    monkeypatch.setattr(kb, "_embed_unavailable", False)

    assert kb._get_embedder() is None
    assert kb.embeddings_ready() is False
    assert kb._embed(["test text"]) is None


def test_embedder_timeout_falls_back_to_bm25(monkeypatch):
    """When loading takes longer than timeout, it aborts, caches cooldown, and returns None."""
    monkeypatch.delenv("VF_OFFLINE", raising=False)
    monkeypatch.setattr(kb, "_embedder", None)
    monkeypatch.setattr(kb, "_embed_unavailable", False)
    monkeypatch.setattr(kb, "_embed_cooldown_until", 0)
    monkeypatch.setenv("KB_EMBED_TIMEOUT_SECONDS", "0.2")

    def slow_loader(*args, **kwargs):
        time.sleep(1.0)
        return "fake_embedder"

    # Patch fastembed.TextEmbedding or the loader
    with patch("fastembed.text.text_embedding.TextEmbedding.__init__", side_effect=slow_loader):
        t0 = time.time()
        res = kb._get_embedder()
        elapsed = time.time() - t0

        assert res is None, "Timed out loader must return None"
        assert elapsed < 0.8, f"Loader should have timed out quickly, took {elapsed}s"
        assert kb.embeddings_ready() is False


def test_embedder_failure_cooldown(monkeypatch):
    """After a failure/timeout, subsequent calls don't retry until cooldown expires."""
    monkeypatch.delenv("VF_OFFLINE", raising=False)
    monkeypatch.setattr(kb, "_embedder", None)
    monkeypatch.setattr(kb, "_embed_unavailable", False)
    monkeypatch.setattr(kb, "_embed_cooldown_until", 0)

    call_count = 0

    def failing_loader(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise RuntimeError("Network down")

    with patch("fastembed.text.text_embedding.TextEmbedding.__init__", side_effect=failing_loader):
        assert kb._get_embedder() is None
        assert call_count == 1
        # Second call should hit the cooldown without calling TextEmbedding again
        assert kb._get_embedder() is None
        assert call_count == 1

