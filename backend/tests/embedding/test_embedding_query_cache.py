"""Tests for the embed_query LRU cache wrapper (S0-B)."""

import threading
import time

import pytest
from langchain_core.embeddings import Embeddings

from src.services.embedder import _QueryCachedEmbeddings


class _CountingEmbeddings(Embeddings):
    def __init__(self, delay: float = 0.0) -> None:
        self.query_calls = 0
        self.doc_calls = 0
        self._delay = delay
        self._lock = threading.Lock()

    def embed_query(self, text: str) -> list[float]:
        with self._lock:
            self.query_calls += 1
        if self._delay:
            time.sleep(self._delay)
        return [float(len(text)), 0.0, 0.0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.doc_calls += 1
        return [[float(len(t)), 0.0, 0.0] for t in texts]

    async def aembed_query(self, text: str) -> list[float]:
        return self.embed_query(text)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embed_documents(texts)


def test_embed_query_cache_returns_cached_vector_on_second_call():
    inner = _CountingEmbeddings()
    cached = _QueryCachedEmbeddings(inner, max_size=16)

    v1 = cached.embed_query("hello world")
    v2 = cached.embed_query("hello world")

    assert v1 == v2
    assert inner.query_calls == 1


def test_embed_query_cache_evicts_lru_entries():
    inner = _CountingEmbeddings()
    cached = _QueryCachedEmbeddings(inner, max_size=2)

    cached.embed_query("a")
    cached.embed_query("b")
    cached.embed_query("c")  # evicts "a"
    cached.embed_query("a")  # re-embeds

    assert inner.query_calls == 4


def test_embed_query_cache_distinguishes_texts():
    inner = _CountingEmbeddings()
    cached = _QueryCachedEmbeddings(inner, max_size=8)

    v1 = cached.embed_query("short")
    v2 = cached.embed_query("much longer query")

    assert v1 != v2
    assert inner.query_calls == 2


def test_embed_documents_caches_per_text():
    """Per-text caching on embed_documents: a second call with the same texts
    hits the cache and never reaches the inner provider.

    This matters in extraction: a batched prewarm of N texts followed by N
    single-text upsert calls produces 1 HTTP call instead of 1 + N.
    """
    inner = _CountingEmbeddings()
    cached = _QueryCachedEmbeddings(inner, max_size=8)

    cached.embed_documents(["a", "b"])
    cached.embed_documents(["a", "b"])

    assert inner.doc_calls == 1  # second call is fully served from cache


def test_embed_documents_partial_cache_hit_only_misses_make_api_call():
    """Mixed cache hits/misses in one batch: only the misses are sent to the
    inner provider, in a single batched call. Result order matches input order.
    """
    inner = _CountingEmbeddings()
    cached = _QueryCachedEmbeddings(inner, max_size=8)

    cached.embed_documents(["a", "b"])  # populates cache
    result = cached.embed_documents(["a", "c", "b"])  # 'c' is the only miss

    assert inner.doc_calls == 2
    assert len(result) == 3  # order preserved


@pytest.mark.anyio
async def test_aembed_query_cache_hits():
    inner = _CountingEmbeddings()
    cached = _QueryCachedEmbeddings(inner, max_size=4)

    r1 = await cached.aembed_query("same")
    r2 = await cached.aembed_query("same")

    assert r1 == r2
    assert inner.query_calls == 1


def test_concurrent_embed_query_coalesces_into_single_call():
    """Simulates the pipeline burst: 5 threads embed the same text concurrently.

    Without stampede protection, all 5 would make separate API calls.
    With coalescing, only 1 call is made and the other 4 wait for its result.
    """
    inner = _CountingEmbeddings(delay=0.1)
    cached = _QueryCachedEmbeddings(inner, max_size=16)
    results: list[list[float]] = [None] * 5  # type: ignore[assignment]
    barrier = threading.Barrier(5)

    def worker(idx: int) -> None:
        barrier.wait(timeout=5.0)
        results[idx] = cached.embed_query("hello world")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    expected = [float(len("hello world")), 0.0, 0.0]
    assert all(r == expected for r in results)
    assert inner.query_calls == 1


def test_concurrent_embed_query_different_texts_do_not_block():
    """Different texts should not coalesce — each gets its own API call."""
    inner = _CountingEmbeddings(delay=0.05)
    cached = _QueryCachedEmbeddings(inner, max_size=16)
    results: dict[str, list[float]] = {}
    lock = threading.Lock()

    def worker(text: str) -> None:
        result = cached.embed_query(text)
        with lock:
            results[text] = result

    texts = ["alpha", "beta", "gamma"]
    threads = [threading.Thread(target=worker, args=(t,)) for t in texts]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    assert inner.query_calls == 3
    for text in texts:
        assert results[text] == [float(len(text)), 0.0, 0.0]


class _FlakyEmbeddings(Embeddings):
    """Returns an empty-response ValueError the first ``fail_times`` calls."""

    def __init__(self, fail_times: int) -> None:
        self.fail_times = fail_times
        self.query_calls = 0
        self.doc_calls = 0

    def embed_query(self, text: str) -> list[float]:
        self.query_calls += 1
        if self.query_calls <= self.fail_times:
            raise ValueError("No embedding data received")
        return [float(len(text)), 0.0, 0.0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.doc_calls += 1
        if self.doc_calls <= self.fail_times:
            raise ValueError("No embedding data received")
        return [[float(len(t)), 0.0, 0.0] for t in texts]

    async def aembed_query(self, text: str) -> list[float]:
        return self.embed_query(text)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embed_documents(texts)


def test_embed_query_retries_on_empty_response(monkeypatch):
    """Regression: OpenRouter sometimes returns ``No embedding data received``.
    The wrapper must retry transparently for ``embed_query`` (the previous
    implementation only retried for ``embed_documents``, leaving summary scoring
    and the query pre-warm to surface the error to callers).
    """
    monkeypatch.setattr("src.services.embedder.time.sleep", lambda _s: None)
    inner = _FlakyEmbeddings(fail_times=1)
    cached = _QueryCachedEmbeddings(inner, max_size=8)
    result = cached.embed_query("hello")
    assert result == [5.0, 0.0, 0.0]
    assert inner.query_calls == 2  # one retry succeeded


def test_embed_query_propagates_after_max_retries(monkeypatch):
    monkeypatch.setattr("src.services.embedder.time.sleep", lambda _s: None)
    inner = _FlakyEmbeddings(fail_times=10)
    cached = _QueryCachedEmbeddings(inner, max_size=8)
    with pytest.raises(ValueError, match="No embedding data received"):
        cached.embed_query("hello")


def test_embed_query_does_not_retry_unrelated_value_errors(monkeypatch):
    monkeypatch.setattr("src.services.embedder.time.sleep", lambda _s: None)

    class _AlwaysBad(Embeddings):
        def __init__(self) -> None:
            self.calls = 0

        def embed_query(self, text: str) -> list[float]:
            self.calls += 1
            raise ValueError("model not found")

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            raise NotImplementedError

    inner = _AlwaysBad()
    cached = _QueryCachedEmbeddings(inner, max_size=8)
    with pytest.raises(ValueError, match="model not found"):
        cached.embed_query("hi")
    assert inner.calls == 1  # propagated immediately, no retry


def test_followers_wait_through_slow_leader_without_starting_new_call(monkeypatch):
    """Regression: the four parallel context-loading branches (memories,
    session, entity, retrieve) used to each start their own embedding API call
    when the leader's call was slower than the follower stampede-wait timeout.
    The wait must outlast a slow provider so followers actually pick up the
    cached value instead of falling through to a redundant API call.
    """
    leader_delay = 0.5
    inner = _CountingEmbeddings(delay=leader_delay)
    cached = _QueryCachedEmbeddings(inner, max_size=16, stampede_wait_s=leader_delay * 4)

    barrier = threading.Barrier(4)
    results: list[list[float]] = [None] * 4  # type: ignore[assignment]

    def worker(idx: int) -> None:
        barrier.wait(timeout=5.0)
        results[idx] = cached.embed_query("scoped query")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=leader_delay * 8)

    assert all(r is not None for r in results)
    assert inner.query_calls == 1, (
        "followers fell through stampede protection and called the API again"
    )
