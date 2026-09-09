"""Regression tests for local-confidence score provenance."""

import pytest

from src.services.chain._context import PipelineContext
from src.services.chain._steps_context import (
    _calibrated_local_confidence,
    _normalize_top_score,
    perform_web_search,
)


def test_explicit_distance_is_normalized() -> None:
    assert _normalize_top_score({"score_kind": "distance"}, 0.5) == pytest.approx(2 / 3)


def test_relevance_is_not_normalized_again() -> None:
    assert _normalize_top_score({"score_kind": "relevance"}, 0.8) == 0.8


def test_legacy_public_result_defaults_to_relevance() -> None:
    assert _normalize_top_score({}, 0.8) == 0.8


def test_rank_score_is_not_treated_as_calibrated_confidence() -> None:
    assert (
        _calibrated_local_confidence({"score": 1.0, "score_kind": "relevance", "confidence": 0.99})
        is None
    )


def test_calibrated_confidence_must_be_bounded() -> None:
    assert _calibrated_local_confidence({"confidence": 0.8, "confidence_kind": "calibrated"}) == 0.8
    assert (
        _calibrated_local_confidence({"confidence": 1.1, "confidence_kind": "calibrated"}) is None
    )


@pytest.mark.asyncio
async def test_always_mode_does_not_skip_web_for_top_rrf_score(monkeypatch) -> None:
    from src.core.config import settings
    from src.services import search as search_module
    from src.services.search import SearchResult

    calls: list[str] = []

    async def fake_web_search(query: str, **_kwargs):
        calls.append(query)
        return [
            SearchResult(
                title="Alpha",
                url="https://example.test",
                snippet="alpha result",
                source="test",
            )
        ]

    monkeypatch.setattr(settings, "SEARCH_BINDING", "test")
    monkeypatch.setattr(search_module, "web_search", fake_web_search)
    monkeypatch.setattr(search_module, "format_search_results", lambda _items: "web context")
    ctx = PipelineContext(
        question="alpha",
        use_web_search=True,
        web_search_mode="always",
        docs=[{"score": 1.0, "score_kind": "relevance"}],
    )

    await perform_web_search(ctx)

    assert calls == ["alpha"]
    assert ctx.web_context == "web context"


@pytest.mark.asyncio
async def test_fallback_skips_only_for_calibrated_confidence(monkeypatch) -> None:
    from src.core.config import settings
    from src.services import search as search_module

    calls: list[str] = []

    async def fake_web_search(query: str, **_kwargs):
        calls.append(query)
        return []

    monkeypatch.setattr(settings, "SEARCH_BINDING", "test")
    monkeypatch.setattr(search_module, "web_search", fake_web_search)
    ctx = PipelineContext(
        question="alpha",
        web_search_mode="fallback",
        docs=[{"confidence": 0.9, "confidence_kind": "calibrated"}],
    )

    await perform_web_search(ctx)

    assert calls == []
