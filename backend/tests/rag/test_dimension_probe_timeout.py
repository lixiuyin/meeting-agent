import threading
import time

from src.services.rag import _vectorstore as store


def test_timeout_returns_without_waiting_for_running_worker(monkeypatch):
    release = threading.Event()
    calls = []

    class SlowEmbeddings:
        def embed_query(self, text):
            calls.append(text)
            release.wait(2)
            return [1.0, 0.0]

    monkeypatch.setattr(store, "_DIMENSION_PROBE_TIMEOUT", 0.02)
    start = time.monotonic()
    try:
        assert store._resolve_expected_dimension(SlowEmbeddings(), 7) == 7
        assert time.monotonic() - start < 0.5
        assert store._resolve_expected_dimension(SlowEmbeddings(), 7) == 7
        assert len(calls) == 1
    finally:
        release.set()
        assert store._dimension_probe_slot.acquire(timeout=2)
        store._dimension_probe_slot.release()
