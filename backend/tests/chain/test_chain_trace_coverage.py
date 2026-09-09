"""Unit test for chat pipeline trace span coverage."""

import pytest

from src.services.chain._api import ask


@pytest.mark.asyncio
async def test_ask_trace_contains_all_expected_spans(monkeypatch):
    """Verify that ask() produces a complete set of trace spans."""

    # Mock skill loader / matcher
    class _FakeSkillLoader:
        def load_summaries(self):
            return []

        def get_full(self, name):
            return None

    class _FakeMatcher:
        async def match(self, question, skills):
            return None

    monkeypatch.setattr("src.services.chain._api._get_skill_loader", lambda: _FakeSkillLoader())
    monkeypatch.setattr("src.services.chain._api._get_skill_matcher", lambda: _FakeMatcher())

    # Mock generate_answer directly so we don't need a real LangChain runnable
    async def _mock_generate_answer(ctx, skill_definition=None):
        ctx.trace.start_span("generate_answer", "generate")
        ctx.answer = "Mocked answer for trace test."
        ctx.trace.finish_span("generate_answer")

    monkeypatch.setattr("src.services.chain._api.generate_answer", _mock_generate_answer)

    # Mock retrieve to return empty docs (we only care about spans)
    monkeypatch.setattr("src.services.rag._retriever._vector_retrieve", lambda *a, **k: [])

    result = await ask(
        question="What are the action items?",
        user_id="trace-test",
    )

    assert result.trace is not None
    spans = result.trace["spans"]
    labels = [s["label"] for s in spans]

    expected_labels = {
        "classify_intent",
        "prewarm_query_embedding",
        "skill_match",
        "pipeline",
        "ensure_session",
        "retrieve",
        "suppress_near_duplicates",
        "load_memories",
        "load_entity_context",
        "load_history",
        "build_context",
        "generate_answer",
        "save_messages",
        "schedule_fact_extraction",
    }

    for label in expected_labels:
        assert label in labels, f"Missing span: {label}"

    for span in spans:
        assert span["duration_ms"] is not None, f"Span {span['label']} missing duration_ms"
        assert span["status"] in ("success", "degraded", "timeout", "error"), (
            f"Span {span['label']} has non-terminal status"
        )


@pytest.mark.asyncio
async def test_skipped_spans_have_skipped_field(monkeypatch):
    """Spans created with skipped=True should include 'skipped: true' in output."""

    class _FakeSkillLoader:
        def load_summaries(self):
            return []

        def get_full(self, name):
            return None

    class _FakeMatcher:
        async def match(self, question, skills):
            return None

    monkeypatch.setattr("src.services.chain._api._get_skill_loader", lambda: _FakeSkillLoader())
    monkeypatch.setattr("src.services.chain._api._get_skill_matcher", lambda: _FakeMatcher())

    async def _mock_generate_answer(ctx, skill_definition=None):
        ctx.trace.start_span("generate_answer", "generate")
        ctx.answer = "Mocked answer."
        ctx.trace.finish_span("generate_answer")

    monkeypatch.setattr("src.services.chain._api.generate_answer", _mock_generate_answer)
    monkeypatch.setattr("src.services.rag._retriever._vector_retrieve", lambda *a, **k: [])

    result = await ask(question="What happened?", user_id="trace-test")
    spans = result.trace["spans"]
    skipped_spans = [s for s in spans if s.get("skipped") is True]
    assert len(skipped_spans) >= 1, "Expected at least one skipped span"


@pytest.mark.asyncio
async def test_casual_intent_trace(monkeypatch):
    """Casual inputs should still emit classify_intent, skill_match, and pipeline spans."""
    result = await ask(question="hi", user_id="trace-test")
    assert result.trace is not None
    labels = [s["label"] for s in result.trace["spans"]]
    assert "classify_intent" in labels
    assert "skill_match" in labels
    assert "pipeline" in labels
    assert "ensure_session" in labels
    assert "save_messages" in labels
