"""Tests for Settings API endpoints.

Tests cover:
- GET /settings
- PUT /settings
- GET /settings/bindings
- POST /settings/rebuild-vectors
"""

import os
import tempfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

# Set up test environment
os.environ["API_KEY"] = "test-key"
os.environ["DATA_DIR"] = tempfile.mkdtemp()

from src.core import constants as constants_module

constants_module.DATA_DIR = Path(os.environ["DATA_DIR"])
constants_module.DATABASE_PATH = constants_module.DATA_DIR / "test.db"

from src.api.routers.settings._get import (  # noqa: E402
    _get_current_settings,
    _mask_secret,
)
from src.api.routers.settings._update import (  # noqa: E402
    _is_masked_value,
    _update_settings_in_memory,
    _validated_settings_candidate,
)
from src.core.config import settings  # noqa: E402
from src.core.settings_policy import classify_settings_changes  # noqa: E402


class TestMaskSecret:
    """Test secret masking utility."""

    def test_mask_short_secret(self):
        """Should mask short secrets completely."""
        result = _mask_secret("abc")
        assert result == "***"

    def test_mask_long_secret(self):
        """Should return fixed placeholder for any non-empty secret."""
        result = _mask_secret("abcdefghij")
        assert result == "***"

    def test_mask_empty_secret(self):
        """Should handle empty string."""
        result = _mask_secret("")
        assert result == ""

    def test_mask_none_secret(self):
        """Should handle None."""
        result = _mask_secret(None)
        assert result == ""


class TestIsMaskedValue:
    """Test masked value detection."""

    def test_detects_masked(self):
        """Should detect masked values."""
        assert _is_masked_value("******ghij") is True
        assert _is_masked_value("*******test") is True

    def test_detects_not_masked(self):
        """Should not flag normal values as masked."""
        assert _is_masked_value("plaintext") is False
        assert _is_masked_value("test123") is False

    def test_detects_partial_mask(self):
        """Should detect partial masked values."""
        assert _is_masked_value("******abcd") is True


class TestGetCurrentSettings:
    """Test settings retrieval."""

    def test_returns_settings_response(self):
        """Should return settings as response model."""
        response = _get_current_settings()
        assert response.llm is not None
        assert response.embedding is not None
        assert response.rag is not None

    def test_masks_api_keys(self):
        """Should mask sensitive API keys."""
        response = _get_current_settings()
        # API keys should be masked
        if response.llm.api_key:
            assert "***" in response.llm.api_key or response.llm.api_key == ""

    def test_includes_all_sections(self):
        """Should include all settings sections."""
        response = _get_current_settings()
        assert response.llm is not None
        assert response.embedding is not None
        assert response.rag is not None
        assert response.memory is not None
        assert response.search is not None
        assert response.upload is not None
        assert "EMBEDDING_DIMENSION" in response.activation_policy.reindex_required
        assert "HOST" in response.activation_policy.restart_required


class TestUpdateSettingsInMemory:
    """Test settings update."""

    @pytest.fixture(autouse=True)
    def restore_settings(self, monkeypatch):
        """Restore all settings mutated by _update_settings_in_memory after each test."""
        for attr in (
            "LLM_BINDING",
            "LLM_MODEL",
            "LLM_TEMPERATURE",
            "LLM_MAX_TOKENS",
            "LLM_BASE_URL",
            "LLM_HOST",
            "LLM_API_KEY",
        ):
            monkeypatch.setattr(settings, attr, getattr(settings, attr))

    def test_updates_llm_settings(self):
        """Should update LLM settings."""
        from src.models.schemas import LLMSettings, SettingsUpdateRequest

        req = SettingsUpdateRequest(
            llm=LLMSettings(
                model="new-model",
                binding="openai",
                api_key="test",
                base_url="",
                temperature=0.5,
                max_tokens=1000,
            )
        )

        _update_settings_in_memory(req)

        assert settings.LLM_MODEL == "new-model"

    def test_preserves_existing_values(self):
        """Non-LLM settings remain untouched when only the llm section is updated."""
        from src.models.schemas import LLMSettings, SettingsUpdateRequest

        orig_chunk_size = settings.CHUNK_SIZE
        req = SettingsUpdateRequest(
            llm=LLMSettings(
                model="new-model",
                binding="openai",
                api_key="test",
                base_url="",
                temperature=0.5,
                max_tokens=1000,
            )
        )

        _update_settings_in_memory(req)

        assert settings.LLM_MODEL == "new-model"
        assert orig_chunk_size == settings.CHUNK_SIZE  # RAG settings untouched

    def test_staged_updates_are_not_visible_until_atomic_publish(self):
        previous = settings.copy_live()
        original_model = settings.LLM_MODEL
        candidate = settings.copy_live()
        try:
            with settings.stage_updates(candidate):
                settings.LLM_MODEL = "candidate-model"
                assert candidate.LLM_MODEL == "candidate-model"
                assert original_model == settings.LLM_MODEL
            settings.replace_live(candidate)
            assert settings.LLM_MODEL == "candidate-model"
        finally:
            settings.replace_live(previous)

    def test_candidate_runs_full_settings_validation(self):
        from pydantic import ValidationError

        from src.models.schemas import ASRSettings, SettingsUpdateRequest

        request = SettingsUpdateRequest.model_construct(
            asr=ASRSettings.model_construct(provider="definitely-not-supported")
        )

        with pytest.raises(ValidationError):
            _validated_settings_candidate(request)


class TestEmbeddingRebuildGuard:
    def test_requires_rebuild_when_embedding_dimension_changes(self):
        from src.models.schemas import EmbeddingSettings, SettingsUpdateRequest

        changed_dimension = settings.EMBEDDING_DIMENSION - 1
        if changed_dimension < 128:
            changed_dimension = settings.EMBEDDING_DIMENSION + 1

        req = SettingsUpdateRequest(
            embedding=EmbeddingSettings(
                binding=settings.EMBEDDING_BINDING,
                model=settings.EMBEDDING_MODEL,
                api_key="",
                base_url="",
                dimension=changed_dimension,
            )
        )
        candidate = _validated_settings_candidate(req)
        changes = classify_settings_changes(settings, candidate)
        assert "EMBEDDING_DIMENSION" in changes["reindex_required"]

    def test_no_rebuild_when_embedding_unchanged(self):
        from src.models.schemas import EmbeddingSettings, SettingsUpdateRequest

        req = SettingsUpdateRequest(
            embedding=EmbeddingSettings(
                binding=settings.EMBEDDING_BINDING,
                model=settings.EMBEDDING_MODEL,
                api_key="",
                base_url=settings.EMBEDDING_BASE_URL,
                dimension=settings.EMBEDDING_DIMENSION,
            )
        )
        candidate = _validated_settings_candidate(req)
        changes = classify_settings_changes(settings, candidate)
        assert changes["reindex_required"] == []

    def test_retriever_provider_rejects_legacy_raganything(self):
        from src.models.schemas import RAGSettings

        with pytest.raises(ValueError):
            RAGSettings(
                chunk_size=1024,
                chunk_overlap=128,
                top_k=5,
                query_rewrite_enabled=False,
                score_threshold=1.5,
                reranker_binding="",
                reranker_model="cohere/rerank-4-pro",
                reranker_api_key="",
                reranker_base_url="",
                reranker_top_n=5,
                parent_child_enabled=False,
                child_chunk_size=256,
                child_chunk_overlap=32,
                hybrid_search_enabled=False,
                hybrid_alpha=0.5,
                retriever_provider="raganything",
                raganything_enabled=False,
                raganything_fallback_to_native=True,
            )


class TestSettingsAPIIntegration:
    """Test Settings API endpoints with mocked dependencies."""

    def test_settings_imports(self):
        """Should be able to import settings router."""
        from src.api.routers import settings as settings_router

        assert hasattr(settings_router, "router")

    def test_router_has_routes(self):
        """Router should have registered routes."""
        from src.api.routers.settings import router

        routes = [r.path for r in router.routes]
        assert "/settings" in routes  # GET /settings
        assert any("bindings" in r for r in routes)
        assert any("rebuild-vectors" in r for r in routes)

    @pytest.mark.asyncio
    async def test_reindex_change_returns_machine_readable_policy_error(self):
        from src.main import app

        current = _get_current_settings().embedding.model_dump()
        current["dimension"] = current["dimension"] + 1
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.put(
                "/api/v1/settings",
                json={"embedding": current},
            )

        assert response.status_code == 409
        payload = response.json()
        assert payload["code"] == "SETTINGS_REINDEX_REQUIRED"
        assert "EMBEDDING_DIMENSION" in payload["details"]["reindex_required"]
