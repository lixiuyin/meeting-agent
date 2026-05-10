"""Tests for per-request rag_mode plumbing."""

from unittest.mock import patch

import pytest

from src.services.chain._context import PipelineContext
from src.services.chain._steps_retrieve import retrieve_documents
from src.services.rag._retriever import retrieve


def test_retrieve_rag_mode_native_overrides_provider(monkeypatch):
    monkeypatch.setattr("src.services.rag._retriever.settings.RAGANYTHING_ENABLED", True)
    monkeypatch.setattr("src.services.rag._retriever.settings.RAG_RETRIEVER_PROVIDER", "multimodal")
    monkeypatch.setattr("src.services.rag._retriever.settings.HYBRID_SEARCH_ENABLED", False)

    with (
        patch(
            "src.services.rag._retriever._vector_retrieve",
            return_value=[{"content": "native", "metadata": {}, "score": 0.1}],
        ) as mock_vector,
        patch(
            "src.services.rag._retriever.retrieve_with_raganything",
            return_value=[{"content": "mm", "metadata": {}, "score": 0.2}],
        ) as mock_mm,
    ):
        out, _qa = retrieve("q", rag_mode="native")
        assert out[0]["content"] == "native"
        mock_vector.assert_called_once()
        mock_mm.assert_not_called()


def test_retrieve_rag_mode_multimodal_maps_to_multimodal_strategy(monkeypatch):
    monkeypatch.setattr("src.services.rag._retriever.settings.RAGANYTHING_ENABLED", True)
    monkeypatch.setattr("src.services.rag._retriever.settings.RAG_RETRIEVER_PROVIDER", "native")

    with patch(
        "src.services.rag._retriever.retrieve_with_raganything",
        return_value=[{"content": "mm", "metadata": {}, "score": 0.2}],
    ) as mock_mm:
        out, _qa = retrieve("q", rag_mode="multimodal")
        assert out[0]["content"] == "mm"
        mock_mm.assert_called_once()


def test_retrieve_rag_mode_multimodal_empty_falls_back_to_native(monkeypatch):
    monkeypatch.setattr("src.services.rag._retriever.settings.RAGANYTHING_ENABLED", True)
    monkeypatch.setattr(
        "src.services.rag._retriever.settings.RAGANYTHING_FALLBACK_TO_NATIVE",
        True,
    )
    monkeypatch.setattr("src.services.rag._retriever.settings.HYBRID_SEARCH_ENABLED", False)

    with (
        patch("src.services.rag._retriever.retrieve_with_raganything", return_value=[]) as mock_mm,
        patch(
            "src.services.rag._retriever._vector_retrieve",
            return_value=[{"content": "native", "metadata": {}, "score": 0.1}],
        ) as mock_vector,
    ):
        out, _qa = retrieve("q", rag_mode="multimodal")
        assert out[0]["content"] == "native"
        mock_mm.assert_called_once()
        mock_vector.assert_called_once()


@pytest.mark.anyio
async def test_retrieve_documents_forwards_rag_mode_to_retriever(monkeypatch):
    """rag_mode flows from PipelineContext into the underlying retrieve() call.

    Broad recall path: rag_mode is forwarded through narrow_scope_via_funnel
    into the wide-fetch retrieve(); fair_retrieve_per_file then propagates it
    further per-file.  Patches all retrieve entry points to capture the mode.
    """
    captured_modes: list[str | None] = []

    def _fake_retrieve(*args, **kwargs):
        captured_modes.append(kwargs.get("rag_mode", args[-2] if len(args) >= 2 else None))
        return ([{"content": "ok", "metadata": {"meeting_id": 1}, "score": 0.1}], None)

    async def _fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr("src.services.chain._steps_retrieve.settings.MULTI_QUERY_ENABLED", False)
    monkeypatch.setattr("src.services.chain._steps_retrieve.settings.HYBRID_SEARCH_ENABLED", False)
    monkeypatch.setattr("src.services.chain._steps_retrieve.settings.RERANKER_BINDING", "")
    monkeypatch.setattr(
        "src.services.chain._steps_retrieve.settings.RAG_FILE_SCOPING_MODE",
        "router_and_funnel",
    )
    monkeypatch.setattr("src.services.chain._steps_retrieve.retrieve", _fake_retrieve)
    # Broad recall now reaches retrieve via _funnel_narrow and _fair_retriever
    # — patch those module-level imports too.
    monkeypatch.setattr("src.services.rag._funnel_narrow.retrieve", _fake_retrieve)
    monkeypatch.setattr("src.services.rag._fair_retriever.retrieve", _fake_retrieve)
    monkeypatch.setattr("src.services.chain._steps_retrieve.asyncio.to_thread", _fake_to_thread)
    monkeypatch.setattr(
        "src.services.chain._steps_retrieve.retrieve_sibling_chunks", lambda *_a, **_k: []
    )

    async def _fake_enumerate(_mids):
        return []

    async def _no_route(*_args, **_kwargs):
        return None

    # Strategy calls the rag-layer routing helpers; patching the canonical
    # rag.* paths ensures both the strategy and the (legacy) chain-layer
    # re-exports observe the override.
    monkeypatch.setattr(
        "src.services.rag._scoping_strategies._enumerate_scope_files",
        _fake_enumerate,
    )
    monkeypatch.setattr(
        "src.services.rag._scoping_strategies._route_scope_files_with_scores",
        _no_route,
    )
    monkeypatch.setattr(
        "src.services.rag._scoping_strategies._route_scope_files_via_summary",
        _no_route,
    )
    # Strategy uses asyncio.to_thread internally
    monkeypatch.setattr("src.services.rag._scoping_strategies.asyncio.to_thread", _fake_to_thread)

    ctx = PipelineContext(question="q", rag_mode="multimodal")
    await retrieve_documents(ctx)

    assert captured_modes, "rag_mode should be forwarded to at least one retrieve call"
    assert all(m == "multimodal" for m in captured_modes)
