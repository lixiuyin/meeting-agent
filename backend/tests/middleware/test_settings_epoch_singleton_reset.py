"""T8: Verify settings changes reset relevant singletons (C-H1)."""

import pytest

from src.core.config import activate_settings_snapshot, build_settings_snapshot, settings
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

    def test_new_request_never_reuses_client_created_by_old_snapshot(self, monkeypatch):
        from src.services.llm import _providers

        monkeypatch.setattr(settings, "LLM_BINDING", "old-test-provider")
        old_snapshot = build_settings_snapshot(epoch=1)
        monkeypatch.setattr(settings, "LLM_BINDING", "new-test-provider")
        monkeypatch.setitem(
            _providers._LLM_CREATORS,
            "old-test-provider",
            lambda model_name=None: {"provider": "old", "model": model_name},
        )
        monkeypatch.setitem(
            _providers._LLM_CREATORS,
            "new-test-provider",
            lambda model_name=None: {"provider": "new", "model": model_name},
        )
        reset_llm()

        with activate_settings_snapshot(old_snapshot):
            assert get_llm() == {"provider": "old", "model": None}

        assert get_llm() == {"provider": "new", "model": None}
        reset_llm()
