from unittest.mock import AsyncMock

import pytest

from src.services.chain._query_routes import is_recorded_fact_request


@pytest.mark.parametrize("query", ["列出已记录的未完成任务", "List all recorded decisions."])
def test_recorded_ledger_queries_have_dedicated_route(query):
    assert is_recorded_fact_request(query)
    assert not is_recorded_fact_request(query, "off")


@pytest.mark.parametrize(
    "query",
    [
        "为什么已记录的任务延期?",
        "Quote the source for the recorded decision.",
        "What tasks did we agree in the meeting?",
        "/summarize recorded tasks",
        "Explain recorded project status.",
    ],
)
def test_evidence_and_explanation_requests_keep_full_retrieval(query):
    assert not is_recorded_fact_request(query)


@pytest.mark.asyncio
async def test_recorded_route_reads_scoped_ledger_without_auxiliary_model_calls(monkeypatch):
    from src.core import database as db
    from src.services.chain import PipelineContext, _api

    with db.get_write_connection() as conn:
        for user, value in (
            ("ledger-user", "Prepare the release notes"),
            ("other-user", "Private task"),
        ):
            db.set_memory(
                conn,
                user_id=user,
                key="todo.release.notes",
                value=value,
                fact_type="action_item",
                assertion_status="confirmed",
                action_status="open",
            )
    monkeypatch.setattr(
        _api, "ensure_session", lambda ctx: setattr(ctx, "session_id", "ledger-session")
    )
    monkeypatch.setattr(_api, "rewrite_query_step", AsyncMock())
    for name in (
        "_prewarm_query_embedding",
        "load_session_context",
        "load_entity_context",
        "perform_web_search",
    ):
        monkeypatch.setattr(_api, name, AsyncMock(side_effect=AssertionError(f"Unexpected {name}")))
    monkeypatch.setattr(
        "src.services.memory.memory_service.search_semantic",
        AsyncMock(side_effect=AssertionError("Unexpected vector search")),
    )
    monkeypatch.setattr(_api, "load_history", AsyncMock())
    for name in (
        "build_context",
        "save_messages",
        "commit_memory_recall_side_effects",
        "commit_anchor_for_success",
    ):
        monkeypatch.setattr(_api, name, lambda _ctx: None)
    monkeypatch.setattr(_api, "schedule_fact_extraction", AsyncMock())

    async def generate(ctx, _skill=None):
        assert "Prepare the release notes" in ctx.memory_context
        assert "Private task" not in ctx.memory_context
        assert ctx.docs == []
        ctx.answer = "Recorded task: Prepare the release notes"

    monkeypatch.setattr(_api, "generate_answer", generate)
    ctx = PipelineContext(question="List all recorded tasks", user_id="ledger-user")
    await _api._run_pipeline_inner(ctx)
    for name in (
        "_prewarm_query_embedding",
        "load_session_context",
        "load_entity_context",
        "perform_web_search",
    ):
        getattr(_api, name).assert_not_awaited()
