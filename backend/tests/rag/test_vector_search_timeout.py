"""Tests for vector_search timeout + BM25 fallback (S1-A)."""

import time
from unittest.mock import MagicMock, patch

from src.services.rag import _retriever


def _slow_vectorstore(delay: float):
    """Fake vectorstore whose similarity_search_with_score blocks for ``delay`` seconds."""
    vs = MagicMock()

    def _blocking_search(*a, **kw):
        time.sleep(delay)
        return []

    vs.similarity_search_with_score.side_effect = _blocking_search
    return vs


def test_run_with_timeout_returns_quickly_on_hang():
    """The timeout should surface well before the call completes."""

    def _sleep_long():
        time.sleep(5)
        return "done"

    start = time.monotonic()
    try:
        _retriever._run_with_timeout(_sleep_long, timeout=0.3)
        raise AssertionError("expected TimeoutError")
    except Exception as exc:  # concurrent.futures.TimeoutError
        assert "TimeoutError" in type(exc).__name__
    assert time.monotonic() - start < 1.0  # returned quickly


def test_run_with_timeout_passthrough_when_disabled():
    def _fast():
        return 42

    assert _retriever._run_with_timeout(_fast, timeout=0) == 42
    assert _retriever._run_with_timeout(_fast, timeout=None) == 42


def test_vector_retrieve_falls_back_to_bm25_on_timeout(monkeypatch):
    """A hanging vectorstore should trigger the BM25 fallback path."""
    monkeypatch.setattr(_retriever.settings, "VECTOR_SEARCH_TIMEOUT_S", 0.2)

    slow_vs = _slow_vectorstore(delay=2.0)
    bm25_hits = []

    def _fake_bm25(query, meeting_ids, file_ids, k, speaker_names=None, user_id=None):
        bm25_hits.append(query)
        return [{"content": "from bm25", "metadata": {"meeting_id": 1}, "score": 0.5}]

    with (
        patch("src.services.rag._retriever.get_vectorstore", return_value=slow_vs),
        patch("src.services.rag._retriever._bm25_retrieve", _fake_bm25),
    ):
        start = time.monotonic()
        out = _retriever._vector_retrieve("hello", filters={}, k=5, threshold=None)
        elapsed = time.monotonic() - start

    assert bm25_hits == ["hello"]
    assert len(out) == 1 and out[0]["content"] == "from bm25"
    assert elapsed < 1.5  # bailed out well before the 2s sleep completed
