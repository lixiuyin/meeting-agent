from unittest.mock import Mock

import pytest

from skills.loader import SkillLoader
from skills.matcher import IntentMatchingService
from skills.models import IntentMatchingConfig, SkillSummary


@pytest.mark.asyncio
async def test_impossible_builtin_match_does_not_call_embedding_provider(monkeypatch):
    service = IntentMatchingService()
    forbidden = Mock(side_effect=AssertionError("unreachable skill must not call embeddings"))
    monkeypatch.setattr(service.semantic_matcher, "_get_embeddings", forbidden)
    assert (
        await service.match("Who owns the database migration?", SkillLoader().load_summaries())
        is None
    )
    forbidden.assert_not_called()


@pytest.mark.parametrize("llm_weight,reachable", [(0.2, False), (0.8, True)])
def test_bound_includes_possible_llm_confidence_boost(llm_weight, reachable):
    skills = [
        SkillSummary(
            name=name,
            display_name=name,
            version="1",
            description=name,
            intent_matching=IntentMatchingConfig(
                method="hybrid",
                threshold=0.7,
                keywords={"required": ["proposal"], "weight": 0.5, "semantic_weight": 0.5},
                examples=["write a proposal"],
                llm_routing={"enabled": True, "weight": llm_weight},
            ),
        )
        for name in ["one", "two"]
    ]
    service = IntentMatchingService()
    assert service._has_reachable_match("Who owns migration?", skills, True) is reachable
    assert service._has_reachable_match("Write a proposal", skills, True)
    assert not service._has_reachable_match("Who owns migration?", skills, False)


def test_semantic_only_skills_remain_reachable():
    skill = SkillSummary(
        name="semantic",
        display_name="semantic",
        version="1",
        description="semantic",
        intent_matching=IntentMatchingConfig(
            method="semantic", threshold=0.8, examples=["meeting summary"]
        ),
    )
    assert IntentMatchingService()._has_reachable_match("different wording", [skill], True)
