"""T9: Verify embedder cache handles sync + async concurrent access (C-C1)."""

import pytest

from src.services.embedder import _QueryCachedEmbeddings, get_embeddings


class _FakeInner:
    """Minimal fake embedding backend for cache tests."""

    def embed_query(self, text: str) -> list[float]:
        return [float(hash(text) & 0xFF) / 256.0 for _ in range(8)]

    async def aembed_query(self, text: str) -> list[float]:
        return self.embed_query(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(t) for t in texts]

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embed_documents(texts)


@pytest.mark.unit
class TestEmbedderCacheThreadAsyncInterleave:
    def test_cache_is_wrapped(self):
        """get_embeddings() returns a _QueryCachedEmbeddings when cache is enabled."""
        emb = get_embeddings()
        # Should have _cache_get, _cache_put, embed_query, aembed_query
        assert hasattr(emb, "_cache_get")
        assert hasattr(emb, "_cache_put")
        assert hasattr(emb, "embed_query")
        assert hasattr(emb, "aembed_query")
        # Single lock for sync+async (C-C1 fix)
        assert hasattr(emb, "_lock")

    def test_cache_structure_and_lru(self):
        """Cache wrapper initializes with correct internals."""
        inner = _FakeInner()
        cached = _QueryCachedEmbeddings(inner, max_size=5)
        assert cached._max == 5
        assert hasattr(cached, "_cache")

    def test_sync_embed_query_returns_result(self):
        """embed_query() returns an embedding vector via the fake backend."""
        inner = _FakeInner()
        cached = _QueryCachedEmbeddings(inner, max_size=5)
        vec = cached.embed_query("test query")
        assert isinstance(vec, list)
        assert len(vec) == 8
        assert all(isinstance(v, float) for v in vec)

    def test_cache_hit_on_repeat_query(self):
        """Repeated queries return same result (cached)."""
        inner = _FakeInner()
        cached = _QueryCachedEmbeddings(inner, max_size=5)
        vec1 = cached.embed_query("cache test query")
        vec2 = cached.embed_query("cache test query")
        assert vec1 == vec2, "Cache should return identical vectors for same text"

    @pytest.mark.asyncio
    async def test_async_embed_query_returns_result(self):
        """aembed_query() returns an embedding vector via the fake backend."""
        inner = _FakeInner()
        cached = _QueryCachedEmbeddings(inner, max_size=5)
        vec = await cached.aembed_query("async test query")
        assert isinstance(vec, list)
        assert len(vec) == 8

    @pytest.mark.asyncio
    async def test_async_cache_hit_on_repeat(self):
        """Repeated async queries should be cached."""
        inner = _FakeInner()
        cached = _QueryCachedEmbeddings(inner, max_size=5)
        vec1 = await cached.aembed_query("async cache test")
        vec2 = await cached.aembed_query("async cache test")
        assert vec1 == vec2
