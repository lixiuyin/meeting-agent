"""Integration tests for skill-aware chain pipeline."""

import asyncio

import pytest

from src.services.chain._api import ask


def _make_mock_loader(skill_name: str | None = None):
    """Build a mock loader that returns summaries and optionally a full definition."""
    from skills.models import IntentMatchingConfig, SkillDefinition, SkillSummary

    summary = SkillSummary(
        name=skill_name or "tech_proposal_generator",
        display_name="Tech Proposal",
        description="test",
        intent_matching=IntentMatchingConfig(method="keyword", threshold=0.5),
    )

    class MockLoader:
        def load_summaries(self):
            return [summary]

        def get_full(self, name: str):
            if name == summary.name:
                return SkillDefinition(
                    name=summary.name,
                    display_name=summary.display_name,
                    description=summary.description,
                    intent_matching=summary.intent_matching,
                )
            return None

    return MockLoader()


class TestChainSkillIntegration:
    @pytest.mark.asyncio
    async def test_ask_returns_skill_used_when_matched(self, monkeypatch):
        pipeline_calls = []

        async def _mock_run_pipeline(ctx, skill_definition=None, *, skill_task=None):
            # Skill resolution now happens inside the pipeline via skill_task.
            # Reproduce that behaviour here so the test reflects the new contract.
            if skill_task is not None and skill_definition is None:
                match = await skill_task
                if match and getattr(match, "matched", False):
                    from src.services.chain._api import _get_skill_loader

                    full = _get_skill_loader().get_full(match.skill.name)
                    skill_definition = full.model_dump() if full else None
                    ctx.skill_name = match.skill.name
                    ctx.skill_confidence = float(match.score)
            pipeline_calls.append(skill_definition)
            ctx.session_id = "test-session-001"
            ctx.answer = "# Mocked tech proposal"
            ctx.docs = []

        monkeypatch.setattr(
            "src.services.chain._api._run_pipeline",
            _mock_run_pipeline,
        )

        async def _mock_match(self, query, skills):
            from skills.models import IntentMatchingConfig, SkillMatchResult, SkillSummary

            summary = SkillSummary(
                name="tech_proposal_generator",
                display_name="Tech Proposal",
                description="test",
                intent_matching=IntentMatchingConfig(method="keyword", threshold=0.5),
            )
            return SkillMatchResult(skill=summary, score=0.85, matched=True)

        monkeypatch.setattr(
            "src.services.chain._api._get_skill_matcher",
            lambda: type("MockMatcher", (), {"match": _mock_match})(),
        )
        monkeypatch.setattr(
            "src.services.chain._api._get_skill_loader",
            lambda: _make_mock_loader("tech_proposal_generator"),
        )

        result = await ask("Please generate a MOST technical proposal")
        assert result.skill_used == "tech_proposal_generator"
        assert result.skill_confidence == 0.85
        assert len(pipeline_calls) == 1
        assert pipeline_calls[0] is not None

    @pytest.mark.asyncio
    async def test_ask_no_skill_when_unmatched(self, monkeypatch):
        pipeline_calls = []

        async def _mock_run_pipeline(ctx, skill_definition=None, *, skill_task=None):
            if skill_task is not None:
                await skill_task  # resolve; None match → no skill_definition
            pipeline_calls.append(skill_definition)
            ctx.session_id = "test-session-002"
            ctx.answer = "Just a regular answer."
            ctx.docs = []

        monkeypatch.setattr(
            "src.services.chain._api._run_pipeline",
            _mock_run_pipeline,
        )

        async def _mock_match(self, query, skills):
            return None

        monkeypatch.setattr(
            "src.services.chain._api._get_skill_matcher",
            lambda: type("MockMatcher", (), {"match": _mock_match})(),
        )
        monkeypatch.setattr(
            "src.services.chain._api._get_skill_loader",
            lambda: _make_mock_loader(),
        )

        result = await ask("How is the weather today")
        assert result.skill_used is None
        assert result.skill_confidence is None
        assert len(pipeline_calls) == 1
        assert pipeline_calls[0] is None

    @pytest.mark.asyncio
    async def test_ask_trace_exposes_skill_timeout_fallback(self, monkeypatch):
        async def _mock_run_pipeline(ctx, skill_definition=None, *, skill_task=None):
            assert skill_definition is None
            if skill_task is not None:
                assert await skill_task is None
            ctx.session_id = "test-session-timeout"
            ctx.answer = "Fallback answer."
            ctx.docs = []

        class _SlowMatcher:
            async def match(self, query, skills):
                await asyncio.sleep(0.05)
                return None

        monkeypatch.setattr("src.services.chain._api._run_pipeline", _mock_run_pipeline)
        monkeypatch.setattr("src.services.chain._api._get_skill_matcher", lambda: _SlowMatcher())
        monkeypatch.setattr(
            "src.services.chain._api._get_skill_loader", lambda: _make_mock_loader()
        )
        monkeypatch.setattr("src.services.chain._api.settings.SKILL_MATCH_TIMEOUT_S", 0.001)

        result = await ask("Explain the meeting blockers in detail")

        skill_span = next(span for span in result.trace["spans"] if span["label"] == "skill_match")
        assert skill_span["status"] == "timeout"
        assert skill_span["metadata"]["outcome"] == "timeout_fallback"
        assert result.answer == "Fallback answer."

    @pytest.mark.asyncio
    async def test_casual_intent_bypasses_skill_matching(self, monkeypatch):
        pipeline_calls = []

        async def _mock_run_pipeline(ctx, skill_definition=None, *, skill_task=None):
            pipeline_calls.append(skill_definition)

        monkeypatch.setattr(
            "src.services.chain._api._run_pipeline",
            _mock_run_pipeline,
        )

        result = await ask("hello")
        assert result.skill_used is None
        assert "meeting agent" in result.answer.lower()
        assert pipeline_calls == []
