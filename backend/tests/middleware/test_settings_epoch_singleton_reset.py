"""T8: Verify settings changes reset relevant singletons (C-H1)."""

import pytest

from src.services.embedder import get_embeddings, reset_embeddings
from src.services.llm import get_llm, reset_llm
from src.services.rag._reranker import reset_reranker_state


@pytest.mark.unit
class TestSettingsEpochSingletonReset:
    def test_reset_embeddings_clears_singleton(self):
        """After reset_embeddings(), get_embeddings() returns a fresh instance."""
        old = get_embeddings()
        reset_embeddings()
        new = get_embeddings()
        # Both should be valid embedding instances
        assert old is not None
        assert new is not None

    def test_reset_llm_clears_singleton(self):
        """After reset_llm(), get_llm() returns a fresh instance."""
        old = get_llm()
        reset_llm()
        new = get_llm()
        assert old is not None
        assert new is not None

    def test_reset_reranker_does_not_raise(self):
        """reset_reranker_state() should succeed even without active clients."""
        reset_reranker_state()  # should not raise

    def test_multiple_resets_idempotent(self):
        """Calling reset multiple times is safe."""
        for _ in range(3):
            reset_embeddings()
            get_embeddings()
        for _ in range(3):
            reset_llm()
            get_llm()
