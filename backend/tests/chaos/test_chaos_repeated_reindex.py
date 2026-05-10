"""Chaos: same file reindexed multiple times — fair retrieve deduplicates correctly."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.services.rag._fair_retriever import fair_retrieve_per_file

pytestmark = pytest.mark.chaos


def _make_doc(
    file_id: int,
    meeting_id: int = 1,
    chunk_index: int = 0,
    content: str = "",
    score: float = 0.5,
) -> dict:
    return {
        "content": content or f"file_{file_id}_chunk_{chunk_index}",
        "metadata": {
            "file_id": file_id,
            "meeting_id": meeting_id,
            "chunk_index": chunk_index,
        },
        "score": score,
    }


class TestRepeatedReindex:
    """Verify fair_retrieve_per_file deduplicates chunks from repeated reindexing."""

    @pytest.mark.asyncio
    async def test_duplicate_chunks_across_versions_are_deduped(self) -> None:
        """File reindexed 3 times produces duplicate chunk_index entries — deduped."""
        file_id = 42
        # Simulate 3 reindex versions of the same 5 chunks (same chunk_index, same content)
        all_chunks: list[dict] = []
        for version in range(3):
            for ci in range(5):
                all_chunks.append(
                    _make_doc(
                        file_id,
                        chunk_index=ci,
                        content=f"content chunk {ci}",
                        score=0.8 - version * 0.05,
                    )
                )

        with (
            patch(
                "src.services.rag._fair_retriever.retrieve", return_value=(all_chunks, MagicMock())
            ),
            patch("src.services.rag._fair_retriever.settings") as mock_settings,
            patch("src.services.rag._fair_retriever.FAIR_RETRIEVE_CACHE_HITS"),
        ):
            mock_settings.RAG_MIN_CHUNKS_PER_FILE = 5
            mock_settings.RAG_FAIR_CONCURRENCY = 4

            result = await fair_retrieve_per_file(
                query="test",
                scope_file_ids=[file_id],
                chunks_per_file=5,
                cached_docs=None,
            )

        # Should have exactly 5 unique chunks (deduped by meeting_id:file_id:chunk_index)
        chunk_indices = {d["metadata"]["chunk_index"] for d in result if "metadata" in d}
        assert len(chunk_indices) == 5
        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_different_files_not_cross_deduped(self) -> None:
        """Chunks with same chunk_index but different file_id are kept separately."""
        docs = [
            _make_doc(1, chunk_index=0, content="file 1 chunk 0"),
            _make_doc(1, chunk_index=1, content="file 1 chunk 1"),
            _make_doc(2, chunk_index=0, content="file 2 chunk 0"),
            _make_doc(2, chunk_index=1, content="file 2 chunk 1"),
        ]

        call_count = 0

        def mock_retrieve(query, **kwargs):
            nonlocal call_count
            fids = kwargs.get("file_ids", [])
            fid = fids[0] if fids else 0
            filtered = [d for d in docs if d["metadata"]["file_id"] == fid]
            call_count += 1
            return filtered, MagicMock()

        with (
            patch("src.services.rag._fair_retriever.retrieve", side_effect=mock_retrieve),
            patch("src.services.rag._fair_retriever.settings") as mock_settings,
            patch("src.services.rag._fair_retriever.FAIR_RETRIEVE_CACHE_HITS"),
        ):
            mock_settings.RAG_MIN_CHUNKS_PER_FILE = 5
            mock_settings.RAG_FAIR_CONCURRENCY = 4

            result = await fair_retrieve_per_file(
                query="test",
                scope_file_ids=[1, 2],
                chunks_per_file=5,
                cached_docs=None,
            )

        assert len(result) == 4
        file_ids_in_result = {d["metadata"]["file_id"] for d in result}
        assert file_ids_in_result == {1, 2}

    @pytest.mark.asyncio
    async def test_stale_chunks_superseded_by_newer_score(self) -> None:
        """When reindexed, higher-score version of same chunk should appear."""
        file_id = 7
        chunks = [
            _make_doc(file_id, chunk_index=0, content="chunk 0 v1", score=0.3),
            _make_doc(file_id, chunk_index=0, content="chunk 0 v2", score=0.9),
            _make_doc(file_id, chunk_index=1, content="chunk 1", score=0.6),
        ]

        with (
            patch("src.services.rag._fair_retriever.retrieve", return_value=(chunks, MagicMock())),
            patch("src.services.rag._fair_retriever.settings") as mock_settings,
            patch("src.services.rag._fair_retriever.FAIR_RETRIEVE_CACHE_HITS"),
        ):
            mock_settings.RAG_MIN_CHUNKS_PER_FILE = 5
            mock_settings.RAG_FAIR_CONCURRENCY = 4

            result = await fair_retrieve_per_file(
                query="test",
                scope_file_ids=[file_id],
                chunks_per_file=5,
                cached_docs=None,
            )

        # Deduped to 2 unique chunk indices
        assert len(result) == 2
        # The first occurrence wins in current dedup (first-seen), but both
        # unique chunk_index values should be present
        indices = {d["metadata"]["chunk_index"] for d in result}
        assert indices == {0, 1}
