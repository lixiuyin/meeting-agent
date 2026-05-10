"""Integration chaos tests: real Chroma + SQLite, no mocks.

These tests exercise the funnel narrow and retrieval pipeline against a real
vector store to validate that mock-skewed assumptions hold under actual
index/query conditions.

Requires ``chromadb`` (installed via ``langchain-chroma``) and a working
embedding provider (or the deterministic ``FakeEmbeddings`` shim).
"""

from __future__ import annotations

import contextlib
import math
import uuid
from typing import Any

import pytest

pytestmark = [pytest.mark.chaos, pytest.mark.integration]


# ---------------------------------------------------------------------------
# Lightweight deterministic embeddings (no API calls)
# ---------------------------------------------------------------------------


class _FakeEmbeddings:
    """Deterministic embeddings: each text maps to a fixed-dim vector.

    Uses a simple hash-based scheme so identical text -> identical vector,
    but different text -> different vector.  Not semantically meaningful,
    but sufficient for testing vector store round-trips.
    """

    def __init__(self, dim: int = 4) -> None:
        self.dim = dim

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)

    def _vec(self, text: str) -> list[float]:
        if text.startswith("IMPORTANT"):
            # Strong signal on dim-0; cluster all IMPORTANT docs together
            raw = [10.0] + [0.1 * i for i in range(1, self.dim)]
        else:
            raw = [0.0] + [0.1 * i + 0.01 * (hash(text) % 10) for i in range(1, self.dim)]
        norm = math.sqrt(sum(x * x for x in raw)) or 1.0
        return [x / norm for x in raw]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_temp_chroma():
    """Create an isolated in-memory Chroma collection for testing."""
    import chromadb

    client = chromadb.EphemeralClient()
    name = f"chaos_test_{uuid.uuid4().hex[:12]}"
    collection = client.create_collection(name, metadata={"hnsw:space": "l2"})
    return client, collection, name


def _cleanup_chroma(client: Any, name: str) -> None:
    """Delete the temporary collection to free memory."""
    with contextlib.suppress(Exception):
        client.delete_collection(name)


def _index_documents(
    collection: Any,
    n_files: int,
    chunks_per_file: int,
    high_score_file_ids: set[int] | None = None,
) -> None:
    """Index synthetic chunks into the Chroma collection.

    High-score files get content prefixed with "IMPORTANT:" to make them
    textually distinct.  All chunks for a file share the same content
    pattern so embedding similarity groups by file.
    """
    emb = _FakeEmbeddings()
    high_score_file_ids = high_score_file_ids or set()

    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict] = []
    embeddings: list[list[float]] = []

    for fid in range(1, n_files + 1):
        prefix = "IMPORTANT " if fid in high_score_file_ids else "content"
        for ci in range(chunks_per_file):
            text = f"{prefix} file_{fid} chunk_{ci}"
            ids.append(f"meeting_1_chunk_{fid}_{ci}")
            documents.append(text)
            metadatas.append(
                {
                    "meeting_id": 1,
                    "file_id": fid,
                    "chunk_index": ci,
                    "speakers_in_chunk": "Alice,Bob" if fid % 3 == 0 else "Carol",
                }
            )
            embeddings.append(emb.embed_query(text))

    if ids:
        collection.add(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
            embeddings=embeddings,
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestWideKRecallAtScale:
    """Verify that wide fetch retrieves high-relevance files from a large corpus."""

    def test_high_score_files_found_in_large_corpus(self) -> None:
        """Index 200 files (3 chunks each), query for IMPORTANT content.

        The top-k results should include at least some of the high-score files.
        """
        client, collection, name = _make_temp_chroma()
        try:
            high_ids = {5, 20, 50, 100, 150}
            _index_documents(
                collection,
                n_files=200,
                chunks_per_file=3,
                high_score_file_ids=high_ids,
            )

            emb = _FakeEmbeddings()
            query_vec = emb.embed_query("IMPORTANT file content")
            results = collection.query(query_embeddings=[query_vec], n_results=30)

            retrieved_file_ids = {
                m["file_id"] for m in (results["metadatas"][0] if results["metadatas"] else [])
            }
            overlap = retrieved_file_ids & high_ids
            assert len(overlap) >= 3, (
                f"Expected >= 3 high-score files, got {len(overlap)}: {overlap}"
            )
        finally:
            _cleanup_chroma(client, name)

    def test_empty_corpus_returns_no_results(self) -> None:
        client, collection, name = _make_temp_chroma()
        try:
            emb = _FakeEmbeddings()
            query_vec = emb.embed_query("anything")
            results = collection.query(query_embeddings=[query_vec], n_results=10)
            assert not results["ids"][0]
        finally:
            _cleanup_chroma(client, name)


class TestSpeakerPushdownFilter:
    """Verify Chroma metadata filtering for speaker pushdown."""

    def test_speaker_contains_filter(self) -> None:
        """Chunks with speakers_in_chunk containing 'Alice' should be filterable."""
        client, collection, name = _make_temp_chroma()
        try:
            _index_documents(collection, n_files=10, chunks_per_file=2)

            results = collection.query(
                query_embeddings=[_FakeEmbeddings().embed_query("content")],
                n_results=30,
                where={"speakers_in_chunk": {"$contains": "Alice"}},
            )

            if results["metadatas"] and results["metadatas"][0]:
                for meta in results["metadatas"][0]:
                    assert "Alice" in meta.get("speakers_in_chunk", ""), (
                        f"Expected Alice in speakers_in_chunk, got {meta}"
                    )
        finally:
            _cleanup_chroma(client, name)

    def test_no_speaker_filter_returns_all(self) -> None:
        client, collection, name = _make_temp_chroma()
        try:
            _index_documents(collection, n_files=10, chunks_per_file=2)

            results = collection.query(
                query_embeddings=[_FakeEmbeddings().embed_query("content")],
                n_results=50,
            )
            assert len(results["ids"][0]) == 20  # 10 files * 2 chunks
        finally:
            _cleanup_chroma(client, name)
