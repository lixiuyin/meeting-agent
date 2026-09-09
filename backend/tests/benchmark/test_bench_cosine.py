"""Unit tests for benchmark embedding cosine similarity helper."""

import pytest

from scripts.benchmark import _embedding_cosine, _embedding_cosine_diagnostic


def test_embedding_cosine_identity(monkeypatch):
    class _FakeEmbeddings:
        def embed_documents(self, texts):
            return [[1.0, 0.0, 0.0], [1.0, 0.0, 0.0]]

    monkeypatch.setattr("src.services.embedder.get_embeddings", lambda: _FakeEmbeddings())
    result = _embedding_cosine("a", "a")
    assert result == pytest.approx(1.0)


def test_embedding_cosine_orthogonal(monkeypatch):
    class _FakeEmbeddings:
        def embed_documents(self, texts):
            return [[1.0, 0.0], [0.0, 1.0]]

    monkeypatch.setattr("src.services.embedder.get_embeddings", lambda: _FakeEmbeddings())
    result = _embedding_cosine("a", "b")
    assert result == pytest.approx(0.0)


def test_embedding_cosine_opposite(monkeypatch):
    class _FakeEmbeddings:
        def embed_documents(self, texts):
            return [[1.0, 0.0], [-1.0, 0.0]]

    monkeypatch.setattr("src.services.embedder.get_embeddings", lambda: _FakeEmbeddings())
    result = _embedding_cosine("a", "b")
    assert result == pytest.approx(-1.0)


def test_embedding_cosine_returns_none_on_exception(monkeypatch):
    class _FakeEmbeddings:
        def embed_documents(self, texts):
            raise RuntimeError("embedder down")

    monkeypatch.setattr("src.services.embedder.get_embeddings", lambda: _FakeEmbeddings())
    result = _embedding_cosine("a", "b")
    assert result is None
    score, error = _embedding_cosine_diagnostic("a", "b")
    assert score is None
    assert error == "RuntimeError"
