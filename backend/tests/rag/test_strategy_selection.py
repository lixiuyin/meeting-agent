"""Tests for retrieval strategy selection."""

from src.services.rag._retriever import _resolve_provider
from src.services.rag._strategies import (
    HybridMultimodalStrategy,
    HybridStrategy,
    MultimodalStrategy,
    NativeStrategy,
    select_strategy,
)


def test_resolve_provider_accepts_hybrid(monkeypatch):
    monkeypatch.setattr("src.services.rag._retriever.settings.RAG_RETRIEVER_PROVIDER", "native")
    monkeypatch.setattr("src.services.rag._retriever.settings.HYBRID_SEARCH_ENABLED", True)
    assert _resolve_provider("hybrid") == "hybrid"


def test_resolve_provider_honors_disabled_hybrid_gate(monkeypatch):
    monkeypatch.setattr("src.services.rag._retriever.settings.RAG_RETRIEVER_PROVIDER", "hybrid")
    monkeypatch.setattr("src.services.rag._retriever.settings.HYBRID_SEARCH_ENABLED", False)
    assert _resolve_provider(None) == "vector"


def test_resolve_provider_rejects_legacy_raganything(monkeypatch):
    monkeypatch.setattr("src.services.rag._retriever.settings.RAG_RETRIEVER_PROVIDER", "native")
    monkeypatch.setattr("src.services.rag._retriever.settings.HYBRID_SEARCH_ENABLED", True)
    assert _resolve_provider("raganything") == "vector"


def test_select_strategy_routes_by_provider():
    native = NativeStrategy(name="native", run=lambda **_: [{"strategy": "native"}])
    hybrid = HybridStrategy(name="hybrid", run=lambda **_: [{"strategy": "hybrid"}])
    multimodal = MultimodalStrategy(name="multimodal", run=lambda **_: [{"strategy": "multimodal"}])
    hybrid_mm = HybridMultimodalStrategy(
        name="hybrid_multimodal",
        run=lambda **_: [{"strategy": "hybrid_multimodal"}],
    )
    strategy = select_strategy(
        "hybrid_multimodal",
        native=native,
        hybrid=hybrid,
        multimodal=multimodal,
        hybrid_multimodal=hybrid_mm,
    )
    out = strategy.retrieve(
        query="q",
        filters={},
        k=1,
        top_k=1,
        threshold=0.0,
        scoped=False,
        trace=None,
    )
    assert out[0]["strategy"] == "hybrid_multimodal"
