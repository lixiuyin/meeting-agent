"""Tests for memory clustering (text and semantic)."""

from unittest.mock import MagicMock, patch

import pytest

from src.services.memory import (
    _semantic_cluster_memories,
    _text_cluster_memories,
)


class TestTextClusterMemories:
    def _make_mem(self, key: str, value: str, category: str = "pref") -> dict:
        return {"key": key, "value": value, "category": category}

    def test_clusters_similar_memories(self):
        memories = [
            self._make_mem("user_lang", "user prefers English language"),
            self._make_mem("language_pref", "user language preference English"),
            self._make_mem("user_food", "user likes pizza"),
        ]
        clusters = _text_cluster_memories(memories, similarity_threshold=0.4)
        # The two language memories should be in the same cluster
        two_mem_clusters = [c for c in clusters if len(c) == 2]
        assert len(two_mem_clusters) == 1
        keys_in_cluster = {m["key"] for m in two_mem_clusters[0]}
        assert "user_lang" in keys_in_cluster
        assert "language_pref" in keys_in_cluster

    def test_keeps_dissimilar_memories_separate(self):
        memories = [
            self._make_mem("user_lang", "user prefers English language"),
            self._make_mem("project_name", "working on project alpha"),
            self._make_mem("meeting_tool", "uses Zoom for meetings"),
        ]
        clusters = _text_cluster_memories(memories, similarity_threshold=0.5)
        # All should be in separate clusters
        assert len(clusters) == 3

    def test_single_memory_forms_own_cluster(self):
        memories = [self._make_mem("only_one", "single memory value")]
        clusters = _text_cluster_memories(memories)
        assert len(clusters) == 1
        assert len(clusters[0]) == 1

    def test_empty_list_returns_empty(self):
        assert _text_cluster_memories([]) == []


class TestSemanticClusterMemories:
    """Tests for _semantic_cluster_memories — falls back to text clustering on error."""

    def _make_mem(self, key: str, value: str) -> dict:
        return {"key": key, "value": value, "category": "pref"}

    @pytest.mark.asyncio
    async def test_falls_back_to_text_on_empty_vectorstore(self):
        """When the vector store is empty, semantic clustering delegates to text clustering."""

        memories = [
            self._make_mem("user_lang", "user prefers English language"),
            self._make_mem("language_pref", "user language preference English"),
            self._make_mem("user_food", "user likes pizza"),
        ]

        mock_vs = MagicMock()
        mock_vs.is_empty.return_value = True

        with patch("src.services.memory.get_memory_vectorstore", return_value=mock_vs):
            clusters = await _semantic_cluster_memories(memories)

        # Should produce the same result as _text_cluster_memories
        assert len(clusters) >= 1

    @pytest.mark.asyncio
    async def test_falls_back_to_text_on_chroma_error(self):
        """When Chroma raises, the function returns text-based clusters without crashing."""

        memories = [
            self._make_mem("k1", "python is great"),
            self._make_mem("k2", "loves python"),
        ]

        mock_vs = MagicMock()
        mock_vs.is_empty.return_value = False
        mock_vs._chromadb.similarity_search_with_score.side_effect = RuntimeError("chroma down")

        with patch("src.services.memory.get_memory_vectorstore", return_value=mock_vs):
            clusters = await _semantic_cluster_memories(memories)

        # Must not raise; must return some clusters
        total = sum(len(c) for c in clusters)
        assert total == 2

    @pytest.mark.asyncio
    async def test_merges_semantically_similar_memories(self):
        """Memories close in embedding space should be clustered together.

        The implementation batch-embeds all memory values once via
        ``embed_documents`` and computes cosine similarity locally, so
        the test patches the embedder directly. (An earlier version of
        this test patched the vectorstore's per-memory ``similarity_search``
        — that path no longer exists.)
        """
        k1 = "prefers_python"
        k2 = "likes_python_programming"
        memories = [
            {"key": k1, "value": "loves Python", "category": "pref"},
            {"key": k2, "value": "Python is favorite", "category": "pref"},
            {"key": "unrelated", "value": "enjoys tennis", "category": "hobby"},
        ]

        # k1 ≈ k2 (high cosine sim), unrelated is orthogonal.
        mock_emb = MagicMock()
        mock_emb.embed_documents.return_value = [
            [1.0, 0.0, 0.0],  # k1
            [0.99, 0.01, 0.0],  # k2 — very close to k1
            [0.0, 0.0, 1.0],  # unrelated — orthogonal
        ]

        with patch("src.services.embedder.get_embeddings", return_value=mock_emb):
            clusters = await _semantic_cluster_memories(memories)

        two_mem_clusters = [c for c in clusters if len(c) >= 2]
        assert len(two_mem_clusters) >= 1
        merged_keys = {m["key"] for m in two_mem_clusters[0]}
        assert k1 in merged_keys and k2 in merged_keys

    @pytest.mark.asyncio
    async def test_small_batch_uses_configured_similarity_threshold(self):
        memories = [
            {"key": "k1", "value": "python is great", "category": "pref"},
            {"key": "k2", "value": "loves python", "category": "pref"},
        ]
        mock_emb = MagicMock()
        mock_emb.embed_documents.return_value = [[1.0, 0.0], [0.0, 1.0]]
        with patch("src.services.embedder.get_embeddings", return_value=mock_emb):
            clusters = await _semantic_cluster_memories(memories)

        assert all(len(c) == 1 for c in clusters)

    def test_config_semantic_cluster_enabled_exists(self):
        from src.core.config import settings

        assert hasattr(settings, "MEMORY_SEMANTIC_CLUSTER_ENABLED")

    @pytest.mark.asyncio
    async def test_semantic_clustering_imports_embedder_successfully(self, caplog):
        """Regression: a previous version of this module used
        ``from ....services.embedder import get_embeddings`` (4 dots), which
        walks past the top-level ``src`` package and raises
        ``ImportError: attempted relative import beyond top-level package``.
        That sent every call straight to the text-clustering fallback —
        semantic clustering was effectively disabled in production.

        This test executes the embedder code path and asserts the embedder
        is actually called; if the relative import is broken again, the
        ``except`` branch swallows the ImportError, ``embed_documents`` is
        never called, and this assertion fails.
        """
        memories = [
            self._make_mem("k1", "loves python"),
            self._make_mem("k2", "python is great"),
        ]

        mock_emb = MagicMock()
        mock_emb.embed_documents.return_value = [
            [1.0, 0.0, 0.0],
            [0.99, 0.01, 0.0],
        ]

        with (
            caplog.at_level("WARNING", logger="src.services.memory._common"),
            patch("src.services.embedder.get_embeddings", return_value=mock_emb),
        ):
            await _semantic_cluster_memories(memories)

        mock_emb.embed_documents.assert_called_once()
        assert "ImportError" not in caplog.text
        assert "falling back to text clustering" not in caplog.text
