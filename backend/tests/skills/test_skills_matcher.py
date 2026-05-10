"""Tests for skills.matcher module."""

import asyncio
import logging
import time

import numpy as np
import pytest

from skills.loader import SkillLoader
from skills.matcher import IntentMatchingService, KeywordMatcher, LLMRouter
from skills.models import IntentMatchingConfig, SkillSummary


class TestKeywordMatcher:
    def test_required_keywords_must_all_match(self):
        matcher = KeywordMatcher()
        config = {"required": ["technical proposal"], "optional": [], "excluded": []}
        score, details = matcher.match("Please write a technical proposal", config)
        assert score > 0
        assert details["required_match"] == 1.0

    def test_missing_required_returns_zero(self):
        matcher = KeywordMatcher()
        config = {"required": ["technical proposal", "most"], "optional": [], "excluded": []}
        score, details = matcher.match("Please write a proposal", config)
        assert score == 0.0
        assert details["reason"] == "missing_required_keywords"

    def test_excluded_keyword_rejects(self):
        matcher = KeywordMatcher()
        config = {"required": [], "optional": [], "excluded": ["do not"]}
        score, details = matcher.match("Please do not write a technical proposal", config)
        assert score == 0.0
        assert "excluded_keyword" in details["reason"]

    def test_optional_keywords_boost_score(self):
        matcher = KeywordMatcher()
        config = {
            "required": ["technical proposal"],
            "optional": ["most", "application"],
            "excluded": [],
        }
        score1, _ = matcher.match("technical proposal", config)
        score2, _ = matcher.match("technical proposal most application", config)
        assert score2 > score1

    def test_regex_pattern_match(self):
        matcher = KeywordMatcher()
        config = {"required": [], "optional": [], "excluded": [], "patterns": [r"\b\d{4}\b"]}
        score, details = matcher.match("project for 2024", config)
        assert score > 0
        assert details["regex_match"] == 1.0


@pytest.fixture
def tech_proposal_summary():
    loader = SkillLoader()
    return loader.get_summary("tech_proposal_generator")


@pytest.fixture
def mock_embeddings(monkeypatch):
    """Replace embedding calls with deterministic fakes that always return high similarity."""

    dim = 8
    base = np.ones(dim, dtype=np.float32)
    base /= np.linalg.norm(base)

    def _text_to_vec(text: str) -> list[float]:
        # Produce vectors close to `base` so cosine similarity is always high
        rng = np.random.RandomState(hash(text) % (2**31))
        vec = base + rng.randn(dim) * 0.1
        vec /= np.linalg.norm(vec)
        return vec.tolist()

    class _FakeEmbeddings:
        def embed_documents(self, texts):
            return [_text_to_vec(t) for t in texts]

        def embed_query(self, text):
            return _text_to_vec(text)

    def _fake_get_embeddings(self):
        if self._embeddings is None:
            self._embeddings = _FakeEmbeddings()
        return self._embeddings

    monkeypatch.setattr("skills.matcher.SemanticMatcher._get_embeddings", _fake_get_embeddings)


class TestIntentMatchingService:
    @pytest.mark.asyncio
    async def test_match_tech_proposal(self, tech_proposal_summary, mock_embeddings):
        if tech_proposal_summary is None:
            pytest.skip("tech_proposal skill not loaded")
        service = IntentMatchingService()
        result = await service.match(
            "Please generate a MOST technical proposal", [tech_proposal_summary]
        )
        assert result is not None
        assert result.matched is True
        assert result.skill.name == "tech_proposal_generator"
        assert result.score >= 0.7

    @pytest.mark.asyncio
    async def test_no_match_for_unrelated_query(self, tech_proposal_summary, mock_embeddings):
        if tech_proposal_summary is None:
            pytest.skip("tech_proposal skill not loaded")
        service = IntentMatchingService()
        result = await service.match("How is the weather today", [tech_proposal_summary])
        assert result is None

    @pytest.mark.asyncio
    async def test_hybrid_combines_keyword_and_semantic(
        self, tech_proposal_summary, mock_embeddings
    ):
        if tech_proposal_summary is None:
            pytest.skip("tech_proposal skill not loaded")
        service = IntentMatchingService()
        result = await service.match("Generate a technical proposal", [tech_proposal_summary])
        assert result is not None
        assert result.matched is True
        assert "keyword" in result.details
        if tech_proposal_summary.intent_matching.examples:
            assert "semantic" in result.details

    @pytest.mark.asyncio
    async def test_empty_skills_returns_none(self):
        service = IntentMatchingService()
        result = await service.match("any query", [])
        assert result is None

    @pytest.mark.asyncio
    async def test_llm_routing_not_triggered_when_clear_winner(
        self, tech_proposal_summary, mock_embeddings
    ):
        if tech_proposal_summary is None:
            pytest.skip("tech_proposal skill not loaded")
        service = IntentMatchingService()
        result = await service.match(
            "Please generate a MOST technical proposal", [tech_proposal_summary]
        )
        assert result is not None
        assert result.ambiguous is False


@pytest.mark.asyncio
async def test_route_timeout_returns_first_candidate(monkeypatch):
    router = LLMRouter()

    class _SlowLLM:
        async def ainvoke(self, _prompt):
            await asyncio.sleep(1.0)
            return "SKILL: second\nCONFIDENCE: 0.9\nREASONING: slow"

    candidates = [
        SkillSummary(
            name="first",
            display_name="First",
            description="first candidate",
            intent_matching=IntentMatchingConfig(method="keyword", threshold=0.1),
        ),
        SkillSummary(
            name="second",
            display_name="Second",
            description="second candidate",
            intent_matching=IntentMatchingConfig(method="keyword", threshold=0.1),
        ),
    ]

    monkeypatch.setattr("skills.matcher._settings.SKILL_ROUTE_TIMEOUT_S", 0.05)
    monkeypatch.setattr(router, "_llm", _SlowLLM())

    selected, confidence, reasoning = await router.route("pick one", candidates)
    assert selected == "first"
    assert confidence == 0.5
    assert reasoning == "timeout_fallback"


@pytest.mark.asyncio
async def test_embed_query_timeout_falls_back_to_none(monkeypatch):
    service = IntentMatchingService()
    monkeypatch.setattr("skills.matcher._settings.SEMANTIC_EMBED_TIMEOUT_S", 0.05)

    def _slow_embed(_query: str):
        time.sleep(0.2)
        return np.ones(8, dtype=np.float32)

    monkeypatch.setattr(service.semantic_matcher, "embed_query", _slow_embed)

    hybrid_skill = SkillSummary(
        name="hybrid_skill",
        display_name="Hybrid",
        description="hybrid matching test",
        intent_matching=IntentMatchingConfig(
            method="hybrid",
            threshold=0.2,
            keywords={
                "required": ["proposal"],
                "optional": [],
                "excluded": [],
                "weight": 0.6,
                "semantic_weight": 0.4,
            },
            examples=["create technical proposal", "draft proposal for project"],
        ),
    )

    result = await service.match("please create a proposal", [hybrid_skill], use_llm=False)
    assert result is not None
    assert result.matched is True
    assert result.details["semantic"] == 0.0
    assert result.details["semantic_details"]["reason"] == "no_query_embedding"


@pytest.mark.asyncio
async def test_embed_query_timeout_logs_warning(monkeypatch, caplog):
    """When semantic embed times out, a WARNING must be logged."""
    service = IntentMatchingService()
    monkeypatch.setattr("skills.matcher._settings.SEMANTIC_EMBED_TIMEOUT_S", 0.05)

    def _slow_embed(_query: str):
        time.sleep(0.2)
        return np.ones(8, dtype=np.float32)

    monkeypatch.setattr(service.semantic_matcher, "embed_query", _slow_embed)

    hybrid_skill = SkillSummary(
        name="hybrid_skill",
        display_name="Hybrid",
        description="hybrid matching test",
        intent_matching=IntentMatchingConfig(
            method="hybrid",
            threshold=0.2,
            keywords={
                "required": ["proposal"],
                "optional": [],
                "excluded": [],
                "weight": 0.6,
                "semantic_weight": 0.4,
            },
            examples=["create technical proposal", "draft proposal for project"],
        ),
    )

    caplog.set_level(logging.WARNING, logger="skills.matcher")
    await service.match("please create a proposal", [hybrid_skill], use_llm=False)

    assert any("semantic embed timed out" in r.message for r in caplog.records), (
        f"Expected embed timeout warning, got: {[r.message for r in caplog.records]}"
    )
