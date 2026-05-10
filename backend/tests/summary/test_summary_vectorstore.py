"""Tests for the summary vector collection helpers."""

from __future__ import annotations

import pytest


class _FakeEmbeddings:
    """Deterministic 4-d embeddings driven by content keywords.

    Mirrors how Chroma persists vectors but small enough for unit tests.
    """

    embedding_dimension = 4

    def embed_documents(self, texts):
        return [self._encode(t) for t in texts]

    def embed_query(self, text):
        return self._encode(text)

    @staticmethod
    def _encode(text: str) -> list[float]:
        text_lower = text.lower()
        return [
            1.0 if "alpha" in text_lower else 0.0,
            1.0 if "beta" in text_lower else 0.0,
            1.0 if "gamma" in text_lower else 0.0,
            float(len(text)) / 1000.0,
        ]


@pytest.fixture
def fake_summary_vectorstore(monkeypatch, tmp_path):
    """Reset singleton + point Chroma at a tmp dir + use fake embeddings."""
    from src.core.config import settings
    from src.services.rag import _summary_vectorstore

    fake = _FakeEmbeddings()
    monkeypatch.setattr("src.services.embedder.get_embeddings", lambda: fake)
    monkeypatch.setattr(_summary_vectorstore, "get_embeddings", lambda: fake)
    monkeypatch.setattr(settings, "VECTOR_DB_DIR", tmp_path)
    monkeypatch.setattr(settings, "EMBEDDING_DIMENSION", 4)
    _summary_vectorstore.reset_summary_vectorstore()
    yield _summary_vectorstore
    _summary_vectorstore.reset_summary_vectorstore()


def test_upsert_and_similarity_search(fake_summary_vectorstore):
    mod = fake_summary_vectorstore
    mod.upsert_file_summary(
        1,
        "alpha topic discussion",
        meeting_id=10,
        file_name="a.pdf",
        file_type="pdf",
    )
    mod.upsert_file_summary(
        2,
        "beta unrelated",
        meeting_id=10,
        file_name="b.pdf",
        file_type="pdf",
    )

    store = mod.get_summary_vectorstore()
    results = store.similarity_search_with_score("alpha", k=2)
    assert len(results) >= 1
    top_doc, _ = results[0]
    assert top_doc.metadata["file_id"] == 1
    assert top_doc.metadata["meeting_id"] == 10


def test_empty_summary_deletes_existing_vector(fake_summary_vectorstore):
    mod = fake_summary_vectorstore
    mod.upsert_file_summary(7, "alpha", meeting_id=1)
    mod.upsert_file_summary(7, "   ", meeting_id=1)

    store = mod.get_summary_vectorstore()
    payload = store._collection.get(ids=["file_7"])
    assert payload["ids"] == []


def test_delete_meeting_summaries_clears_all_files(fake_summary_vectorstore):
    mod = fake_summary_vectorstore
    mod.upsert_file_summary(11, "alpha topic", meeting_id=42)
    mod.upsert_file_summary(12, "beta topic", meeting_id=42)
    mod.upsert_file_summary(13, "gamma topic", meeting_id=43)

    mod.delete_meeting_summaries(42)

    store = mod.get_summary_vectorstore()
    remaining = store._collection.get()
    remaining_ids = set(remaining["ids"])
    assert "file_11" not in remaining_ids
    assert "file_12" not in remaining_ids
    assert "file_13" in remaining_ids


def test_delete_file_summary_is_idempotent(fake_summary_vectorstore):
    mod = fake_summary_vectorstore
    # No-op when nothing indexed
    mod.delete_file_summary(999)
    mod.upsert_file_summary(5, "alpha", meeting_id=1)
    mod.delete_file_summary(5)
    mod.delete_file_summary(5)

    store = mod.get_summary_vectorstore()
    assert store._collection.get(ids=["file_5"])["ids"] == []


# ---------------------------------------------------------------------------
# Meeting-summary vectorstore: idempotent upsert (Phase C3)
# ---------------------------------------------------------------------------


class _CountingEmbeddings(_FakeEmbeddings):
    """Records how many times embed_documents is called."""

    def __init__(self):
        self.call_count = 0

    def embed_documents(self, texts):
        self.call_count += 1
        return super().embed_documents(texts)


@pytest.fixture
def fake_meeting_summary_vs(monkeypatch, tmp_path):
    """Fixture for the meeting-summary singleton with fake embeddings."""
    from src.core.config import settings
    from src.services.rag import _meeting_summary_vectorstore as msv

    counting = _CountingEmbeddings()
    monkeypatch.setattr("src.services.embedder.get_embeddings", lambda: counting)
    monkeypatch.setattr(msv, "get_embeddings", lambda: counting)
    monkeypatch.setattr(settings, "VECTOR_DB_DIR", tmp_path)
    monkeypatch.setattr(settings, "EMBEDDING_DIMENSION", 4)
    msv.reset_meeting_summary_vectorstore()
    yield msv, counting
    msv.reset_meeting_summary_vectorstore()


def test_summary_persist_idempotent_on_unchanged_text(fake_meeting_summary_vs):
    """Second upsert with same hash must not call embed_documents again."""
    msv, counting = fake_meeting_summary_vs

    summary_text = "alpha beta meeting summary"
    import hashlib

    content_hash = hashlib.sha256(summary_text.encode()).hexdigest()[:16]

    # First upsert — should embed
    msv.upsert_meeting_summary(
        meeting_id=1,
        summary=summary_text,
        title="Meeting 1",
        content_hash=content_hash,
    )
    assert counting.call_count == 1

    # Second upsert with identical text + same hash — should skip embed
    msv.upsert_meeting_summary(
        meeting_id=1,
        summary=summary_text,
        title="Meeting 1",
        content_hash=content_hash,
    )
    assert counting.call_count == 1  # no additional embedding call

    # Third upsert with different hash — should embed again
    new_hash = hashlib.sha256(b"different").hexdigest()[:16]
    msv.upsert_meeting_summary(
        meeting_id=1,
        summary="alpha different content",
        title="Meeting 1",
        content_hash=new_hash,
    )
    assert counting.call_count == 2
