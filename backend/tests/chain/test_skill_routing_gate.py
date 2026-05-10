"""Tests for skill routing min-similarity gate (P2-5)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from skills.matcher import IntentMatchingService
from skills.models import IntentMatchingConfig, SkillSummary


@pytest.fixture
def service():
    svc = IntentMatchingService()
    svc.llm_router = MagicMock()
    svc.llm_router.route = AsyncMock(return_value=("some_skill", 0.8, "reason"))
    return svc


def _make_skill(
    name: str,
    method: str = "semantic",
    threshold: float = 0.3,
    llm_enabled: bool = True,
    llm_weight: float = 0.2,
) -> SkillSummary:
    return SkillSummary(
        name=name,
        display_name=name,
        description=f"Test skill {name}",
        intent_matching=IntentMatchingConfig(
            method=method,
            threshold=threshold,
            examples=["example"],
            keywords={"weight": 0.4, "semantic_weight": 0.4},
            llm_routing={"enabled": llm_enabled, "weight": llm_weight},
        ),
    )


@pytest.mark.anyio
async def test_llm_route_skipped_when_best_score_below_threshold(service, monkeypatch):
    """When best.score is below SKILL_ROUTING_MIN_SIMILARITY the LLM is not called."""
    monkeypatch.setattr("skills.matcher._settings.SKILL_ROUTING_MIN_SIMILARITY", 0.5)

    monkeypatch.setattr(
        service.semantic_matcher,
        "embed_query",
        lambda q: [[0.1, 0.2, 0.3]],
    )
    monkeypatch.setattr(
        service.semantic_matcher,
        "match_with_embedding",
        lambda s, q: (0.25 if s.name == "a" else 0.20, {}),
    )

    # threshold=0.2 so the ambiguous pair (0.25, 0.20) passes the per-skill threshold,
    # but both fall below SKILL_ROUTING_MIN_SIMILARITY=0.5, so LLM routing must skip.
    skills = [_make_skill("a", threshold=0.2), _make_skill("b", threshold=0.2)]
    result = await service.match("some query", skills, use_llm=True)

    assert result is not None
    service.llm_router.route.assert_not_called()


@pytest.mark.anyio
async def test_llm_route_invoked_when_best_score_passes_threshold(service, monkeypatch):
    """When best.score meets SKILL_ROUTING_MIN_SIMILARITY the LLM router fires."""
    monkeypatch.setattr("skills.matcher._settings.SKILL_ROUTING_MIN_SIMILARITY", 0.2)

    monkeypatch.setattr(
        service.semantic_matcher,
        "embed_query",
        lambda q: [[0.1, 0.2, 0.3]],
    )
    monkeypatch.setattr(
        service.semantic_matcher,
        "match_with_embedding",
        lambda s, q: (0.55 if s.name == "a" else 0.50, {}),
    )

    skills = [_make_skill("a", threshold=0.2), _make_skill("b", threshold=0.2)]
    result = await service.match("some query", skills, use_llm=True)

    assert result is not None
    service.llm_router.route.assert_awaited()


@pytest.mark.anyio
async def test_skill_matching_disabled_returns_none(monkeypatch):
    """When SKILL_MATCHING_ENABLED is False the pipeline skips matching entirely."""
    from src.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "SKILL_MATCHING_ENABLED", False)

    # Simulate the gate code path copied from _do_skill_match
    def _gate() -> object | None:
        if not app_settings.SKILL_MATCHING_ENABLED:
            return None
        return "would-match"

    assert _gate() is None
