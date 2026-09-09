"""T5: Verify RRF dedup works with legacy and new BM25 metadata (R-H2)."""

import pytest

from src.services.rag._rrf import _rrf_dedup_key, _rrf_merge


@pytest.mark.unit
class TestRRFLegacyMetadataDedup:
    def test_dedup_key_with_chunk_id(self):
        """New metadata with chunk_id uses it as key."""
        doc = {"metadata": {"chunk_id": "meeting_1_file_10_chunk_5"}}
        key = _rrf_dedup_key(doc)
        assert key == "meeting_1_file_10_chunk_5"

    def test_dedup_key_without_chunk_id_falls_back_to_hash(self):
        """Without chunk_id, falls back to normalized content hash (RRF-1)."""
        doc = {"content": "some content", "metadata": {"meeting_id": 1, "chunk_index": 5}}
        key = _rrf_dedup_key(doc)
        assert len(key) == 32  # sha256 hex[:32]

    def test_dedup_key_content_hash_fallback(self):
        """No metadata at all → content hash."""
        doc = {"content": "unique text here"}
        key = _rrf_dedup_key(doc)
        assert len(key) == 32
        assert all(c in "0123456789abcdef" for c in key)

    def test_vector_and_bm25_same_chunk_unify(self):
        """Vector and BM25 results for the same chunk should get the same key."""
        vector_doc = {
            "content": "hello world",
            "metadata": {"chunk_id": "meeting_1_file_1_chunk_0", "meeting_id": 1, "chunk_index": 0},
        }
        bm25_doc = {
            "content": "hello world",
            "metadata": {"chunk_id": "meeting_1_file_1_chunk_0", "meeting_id": 1, "chunk_index": 0},
        }
        assert _rrf_dedup_key(vector_doc) == _rrf_dedup_key(bm25_doc)

    def test_rrf_merge_deduplicates_across_paths(self):
        """Same chunk from vector and BM25 paths should merge, not duplicate."""
        doc_a = {
            "content": "same content",
            "metadata": {"chunk_id": "meeting_1_file_1_chunk_0", "meeting_id": 1},
        }
        doc_b = {
            "content": "same content",
            "metadata": {"chunk_id": "meeting_1_file_1_chunk_0", "meeting_id": 1},
        }
        merged = _rrf_merge(
            [{**doc_a, "score": 0.9}],
            [{**doc_b, "score": 0.8}],
            top_k=5,
        )
        # Should have 1 result, not 2 (deduplicated)
        assert len(merged) == 1

    def test_synthetic_file_router_doc_does_not_warn(self, caplog):
        """File-level routing placeholders carry only ``file_id`` by design.
        They legitimately have no chunk_id; the chunk-staleness warning would
        be misleading. Regression for the noisy
        ``content_prefix=file_2`` warning seen in production logs.
        """
        doc = {"content": "file_42", "metadata": {"file_id": 42}}
        with caplog.at_level("WARNING", logger="src.services.rag._rrf"):
            key = _rrf_dedup_key(doc)
        assert key  # still produces a stable key
        assert "BM25 index may be stale" not in caplog.text

    def test_real_chunk_with_missing_chunk_id_still_warns(self, caplog, monkeypatch):
        """Preserve diagnostic value: a doc that has chunk-level metadata but
        is missing ``chunk_id`` is genuinely abnormal and should still warn.
        """
        # Other tests may legitimately exercise the process-wide warn-once
        # path first. Reset that state here so this test remains independent
        # of collection order while still asserting the production behavior.
        monkeypatch.setattr("src.services.rag._rrf._missing_chunk_warning_emitted", False)
        doc = {
            "content": "real chunk content",
            "metadata": {"meeting_id": 1, "chunk_index": 5, "page_number": 3},
        }
        with caplog.at_level("WARNING", logger="src.services.rag._rrf"):
            _rrf_dedup_key(doc)
        assert "BM25 index may be stale" in caplog.text
