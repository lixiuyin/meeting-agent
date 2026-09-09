"""Tests for settings rebuild requirement detection."""

from uuid import uuid4

import pytest

from src.api.routers.settings._common import (
    _release_db_advisory_lock,
    _try_acquire_db_advisory_lock,
)
from src.api.routers.settings._get import _get_current_settings
from src.api.routers.settings._update import _rebuild_required
from src.core import database as db
from src.core.config import settings
from src.models.schemas.settings import (
    EmbeddingSettings,
    RAGSettings,
    SettingsUpdateRequest,
)


def _current_rag() -> RAGSettings:
    """Get current RAG settings from the API reader."""
    return _get_current_settings().rag


def _make_request(**overrides) -> SettingsUpdateRequest:
    """Build a minimal update request with optional overrides."""
    req = SettingsUpdateRequest()
    if "embedding" in overrides:
        object.__setattr__(req, "embedding", overrides["embedding"])
    if "rag" in overrides:
        object.__setattr__(req, "rag", overrides["rag"])
    return req


class TestEmbeddingRebuild:
    def test_embedding_model_change(self) -> None:
        req = _make_request(
            embedding=EmbeddingSettings(
                binding=settings.EMBEDDING_BINDING,
                model="totally-different-model",
                dimension=settings.EMBEDDING_DIMENSION,
            )
        )
        needs_rebuild, reason = _rebuild_required(req)
        assert needs_rebuild is True
        assert "embedding model" in reason

    def test_embedding_binding_change(self) -> None:
        req = _make_request(
            embedding=EmbeddingSettings(
                binding="fake_binding",
                model=settings.EMBEDDING_MODEL,
                dimension=settings.EMBEDDING_DIMENSION,
            )
        )
        needs_rebuild, reason = _rebuild_required(req)
        assert needs_rebuild is True
        assert "embedding binding" in reason

    def test_no_embedding_change(self) -> None:
        req = _make_request(
            embedding=EmbeddingSettings(
                binding=settings.EMBEDDING_BINDING,
                model=settings.EMBEDDING_MODEL,
                dimension=settings.EMBEDDING_DIMENSION,
            )
        )
        needs_rebuild, reason = _rebuild_required(req)
        assert needs_rebuild is False


class TestRagRebuild:
    def test_chunk_size_change(self) -> None:
        current = _current_rag()
        changed_size = 2048 if current.chunk_size != 2048 else 1024
        rag = current.model_copy(update={"chunk_size": changed_size})
        req = _make_request(rag=rag)
        needs_rebuild, reason = _rebuild_required(req)
        assert needs_rebuild is True
        assert "chunk_size" in reason

    def test_speaker_in_content_change(self) -> None:
        current = _current_rag()
        rag = current.model_copy(
            update={
                "speaker_in_content": not current.speaker_in_content,
            }
        )
        req = _make_request(rag=rag)
        needs_rebuild, reason = _rebuild_required(req)
        assert needs_rebuild is True
        assert "speaker_in_content" in reason

    def test_split_on_speaker_change_change(self) -> None:
        current = _current_rag()
        rag = current.model_copy(
            update={
                "split_on_speaker_change": not current.split_on_speaker_change,
            }
        )
        req = _make_request(rag=rag)
        needs_rebuild, reason = _rebuild_required(req)
        assert needs_rebuild is True
        assert "split_on_speaker_change" in reason

    def test_audio_semantic_boundary_enabled_change(self) -> None:
        current = _current_rag()
        rag = current.model_copy(
            update={
                "audio_semantic_boundary_enabled": not current.audio_semantic_boundary_enabled,
            }
        )
        req = _make_request(rag=rag)
        needs_rebuild, reason = _rebuild_required(req)
        assert needs_rebuild is True
        assert "audio_semantic_boundary_enabled" in reason

    def test_non_text_chunking_strategy_change(self) -> None:
        current = _current_rag()
        rag = current.model_copy(
            update={
                "non_text_chunking_strategy": "text"
                if current.non_text_chunking_strategy == "native"
                else "native",
            }
        )
        req = _make_request(rag=rag)
        needs_rebuild, reason = _rebuild_required(req)
        assert needs_rebuild is True
        assert "non_text_chunking_strategy" in reason

    @pytest.mark.parametrize(
        "field",
        [
            "parent_child_enabled",
            "semantic_chunking_enabled",
            "index_tables",
            "index_image_captions",
        ],
    )
    def test_index_shape_switches_require_rebuild(self, field: str) -> None:
        current = _current_rag()
        rag = current.model_copy(update={field: not getattr(current, field)})
        needs_rebuild, reason = _rebuild_required(_make_request(rag=rag))
        assert needs_rebuild is True
        assert field in reason

    def test_top_k_no_rebuild(self) -> None:
        """Changing top_k (retrieval-only) should NOT require rebuild."""
        current = _current_rag()
        rag = current.model_copy(update={"top_k": current.top_k + 10})
        req = _make_request(rag=rag)
        needs_rebuild, reason = _rebuild_required(req)
        assert needs_rebuild is False

    def test_hybrid_search_toggle_no_rebuild(self) -> None:
        """The BM25 mirror is always maintained, so this is retrieval-only."""
        current = _current_rag()
        provider = "vector" if current.hybrid_search_enabled else "hybrid"
        rag = current.model_copy(
            update={
                "hybrid_search_enabled": not current.hybrid_search_enabled,
                "retriever_provider": provider,
            }
        )
        needs_rebuild, reason = _rebuild_required(_make_request(rag=rag))
        assert needs_rebuild is False
        assert reason == ""

    def test_reranker_no_rebuild(self) -> None:
        """Changing reranker settings should NOT require rebuild."""
        current = _current_rag()
        rag = current.model_copy(update={"reranker_binding": "different_reranker"})
        req = _make_request(rag=rag)
        needs_rebuild, reason = _rebuild_required(req)
        assert needs_rebuild is False

    def test_rag_same_values_no_rebuild(self) -> None:
        current = _current_rag()
        req = _make_request(rag=current)
        needs_rebuild, reason = _rebuild_required(req)
        assert needs_rebuild is False


class TestNoChanges:
    def test_empty_request_no_rebuild(self) -> None:
        req = SettingsUpdateRequest()
        needs_rebuild, reason = _rebuild_required(req)
        assert needs_rebuild is False
        assert reason == ""


def test_rebuild_advisory_lock_is_atomic_and_releasable() -> None:
    lock_name = f"test_rebuild_{uuid4().hex}"
    try:
        assert _try_acquire_db_advisory_lock(lock_name) is True
        assert _try_acquire_db_advisory_lock(lock_name) is False
        _release_db_advisory_lock(lock_name)
        assert _try_acquire_db_advisory_lock(lock_name) is True
    finally:
        _release_db_advisory_lock(lock_name)


def test_rebuild_advisory_lock_renews_only_for_current_owner() -> None:
    from src.api.routers.settings._common import renew_rebuild_advisory_lock

    lock_name = f"test_rebuild_renew_{uuid4().hex}"
    try:
        assert _try_acquire_db_advisory_lock(lock_name)
        assert renew_rebuild_advisory_lock(lock_name)
        with db.get_write_connection() as conn:
            conn.execute(
                "UPDATE kv_state SET value='another-owner' WHERE key=?",
                (f"{lock_name}_owner",),
            )
        assert not renew_rebuild_advisory_lock(lock_name)
    finally:
        _release_db_advisory_lock(lock_name)
