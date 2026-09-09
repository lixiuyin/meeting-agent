from unittest.mock import AsyncMock

import pytest

from src.services.chain import PipelineContext


@pytest.mark.asyncio
async def test_project_binding_uses_resolved_followup_but_explicit_question_wins(monkeypatch):
    from src.core import database as db
    from src.services.chain._steps_retrieve import prepare_query_plan

    with db.get_write_connection() as conn:
        for project in ("atlas", "orbit"):
            db.set_memory(
                conn,
                user_id="plan-project",
                key=f"task.{project}",
                value="ship",
                project_id=project,
                fact_type="action_item",
            )
    monkeypatch.setattr(
        "src.services.chain._steps_retrieve._load_known_speakers", lambda *args, **kwargs: []
    )
    followup = PipelineContext(
        question="继续查看未完成的任务",
        rewritten_query="atlas unfinished tasks",
        user_id="plan-project",
    )
    await prepare_query_plan(followup)
    assert followup.query_plan.project_ids == ("atlas",)
    explicit = PipelineContext(
        question="orbit 的未完成任务", rewritten_query="atlas tasks", user_id="plan-project"
    )
    await prepare_query_plan(explicit)
    assert explicit.query_plan.project_ids == ("orbit",)


@pytest.mark.asyncio
async def test_query_plan_is_published_before_parallel_context_branches(monkeypatch) -> None:
    from src.services.chain._api import _run_pipeline_inner

    order: list[str] = []

    monkeypatch.setattr(
        "src.services.chain._api.ensure_session",
        lambda ctx: setattr(ctx, "session_id", "query-plan-order"),
    )
    monkeypatch.setattr("src.services.chain._api.rewrite_query_step", AsyncMock(return_value=None))

    async def prepare(ctx: PipelineContext) -> None:
        order.append("plan")
        ctx.query_plan = object()

    async def assert_plan(ctx: PipelineContext) -> None:
        assert ctx.query_plan is not None
        assert order[0] == "plan"
        order.append("branch")

    monkeypatch.setattr("src.services.chain._api.prepare_query_plan", prepare)
    monkeypatch.setattr(
        "src.services.chain._api._prewarm_query_embedding", AsyncMock(return_value=None)
    )
    monkeypatch.setattr("src.services.chain._api.retrieve_documents", assert_plan)
    monkeypatch.setattr("src.services.chain._api.load_memories", assert_plan)
    monkeypatch.setattr(
        "src.services.chain._api.load_session_context", AsyncMock(return_value=None)
    )
    monkeypatch.setattr("src.services.chain._api.load_entity_context", AsyncMock(return_value=None))
    monkeypatch.setattr("src.services.chain._api.load_history", AsyncMock(return_value=None))
    monkeypatch.setattr("src.services.chain._api.perform_web_search", AsyncMock(return_value=None))
    monkeypatch.setattr("src.services.chain._api.pre_rerank_dedup", lambda _ctx: None)
    monkeypatch.setattr("src.services.chain._api.rerank_documents", lambda _ctx: None)
    monkeypatch.setattr("src.services.chain._api.suppress_near_duplicates", lambda _ctx: None)
    monkeypatch.setattr("src.services.chain._api.build_context", lambda _ctx: None)
    monkeypatch.setattr(
        "src.services.chain._api.generate_answer",
        AsyncMock(side_effect=lambda ctx, _skill=None: setattr(ctx, "answer", "ok")),
    )
    monkeypatch.setattr("src.services.chain._api.save_messages", lambda _ctx: None)
    monkeypatch.setattr(
        "src.services.chain._api.commit_memory_recall_side_effects", lambda _ctx: None
    )
    monkeypatch.setattr("src.services.chain._api.commit_anchor_for_success", lambda _ctx: None)
    monkeypatch.setattr(
        "src.services.chain._api.schedule_fact_extraction", AsyncMock(return_value=None)
    )
    monkeypatch.setattr("src.services.memory._service._crud.flush_pending_touches", lambda: None)

    ctx = PipelineContext(question="What was known as of 2025-03-01?")
    await _run_pipeline_inner(ctx)

    assert order[0] == "plan"
    assert order.count("branch") == 2


@pytest.mark.asyncio
async def test_frozen_snapshot_does_not_load_current_context_or_extract(monkeypatch) -> None:
    from src.services.chain._api import _run_pipeline_inner

    ctx = PipelineContext(question="continue", continuation_mode="saved_snapshot")

    def ensure(snapshot_ctx: PipelineContext) -> None:
        snapshot_ctx.session_id = "frozen-session"
        snapshot_ctx.snapshot_restored = True
        snapshot_ctx.frozen_combined_context = "frozen"

    monkeypatch.setattr("src.services.chain._api.ensure_session", ensure)
    monkeypatch.setattr("src.services.chain._api.rewrite_query_step", AsyncMock(return_value=None))
    monkeypatch.setattr("src.services.chain._api.prepare_query_plan", AsyncMock(return_value=None))
    prewarm = AsyncMock(return_value=None)
    monkeypatch.setattr("src.services.chain._api._prewarm_query_embedding", prewarm)
    monkeypatch.setattr("src.services.chain._api.retrieve_documents", AsyncMock(return_value=None))
    for name in (
        "load_memories",
        "load_session_context",
        "load_entity_context",
        "perform_web_search",
    ):
        monkeypatch.setattr(f"src.services.chain._api.{name}", AsyncMock(return_value=None))
    history = AsyncMock(return_value=None)
    monkeypatch.setattr("src.services.chain._api.load_history", history)

    def fail_if_snapshot_is_mutated(_ctx: PipelineContext) -> None:
        pytest.fail("frozen snapshot must bypass current retrieval post-processing")

    monkeypatch.setattr("src.services.chain._api.pre_rerank_dedup", fail_if_snapshot_is_mutated)
    monkeypatch.setattr("src.services.chain._api.rerank_documents", fail_if_snapshot_is_mutated)
    monkeypatch.setattr(
        "src.services.chain._api.suppress_near_duplicates", fail_if_snapshot_is_mutated
    )
    monkeypatch.setattr("src.services.chain._api.build_context", lambda _ctx: None)
    monkeypatch.setattr(
        "src.services.chain._api.generate_answer",
        AsyncMock(side_effect=lambda answer_ctx, _skill=None: setattr(answer_ctx, "answer", "ok")),
    )
    monkeypatch.setattr("src.services.chain._api.save_messages", lambda _ctx: None)
    commit_memory = AsyncMock()
    commit_anchor = AsyncMock()
    extraction = AsyncMock()
    monkeypatch.setattr("src.services.chain._api.commit_memory_recall_side_effects", commit_memory)
    monkeypatch.setattr("src.services.chain._api.commit_anchor_for_success", commit_anchor)
    monkeypatch.setattr("src.services.chain._api.schedule_fact_extraction", extraction)
    monkeypatch.setattr("src.services.memory._service._crud.flush_pending_touches", lambda: None)

    await _run_pipeline_inner(ctx)

    prewarm.assert_not_awaited()
    history.assert_awaited_once()
    for name in (
        "load_memories",
        "load_session_context",
        "load_entity_context",
        "perform_web_search",
    ):
        getattr(__import__("src.services.chain._api", fromlist=[name]), name).assert_not_awaited()
    commit_memory.assert_not_awaited()
    commit_anchor.assert_not_awaited()
    extraction.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "confirmed"])
async def test_memory_project_label_does_not_erase_selected_materials(monkeypatch, status):
    from src.core import database as db
    from src.core.database.projects import save_project
    from src.services.chain._steps_retrieve import prepare_query_plan

    user_id = f"label-scope-{status}"
    with db.get_write_connection() as conn:
        db.set_memory(
            conn,
            user_id=user_id,
            key="project.atlas.owner",
            value="Alice",
            project_id="atlas",
            assertion_status=status,
        )
    monkeypatch.setattr(
        "src.services.chain._steps_retrieve._load_known_speakers", lambda *args, **kwargs: []
    )
    ctx = PipelineContext(
        question="Who owns Atlas?", user_id=user_id, meeting_ids=[42], file_ids=[87]
    )
    await prepare_query_plan(ctx)
    assert ctx.file_ids == [87]
    assert ctx.query_plan.project_ids == ("atlas",)
    # User-created bindings remain authoritative even when explicitly empty.
    with db.get_write_connection() as conn:
        save_project(conn, user_id, "atlas", "Atlas", [], [])
    bound = PipelineContext(
        question="Who owns Atlas?", user_id=user_id, meeting_ids=[42], file_ids=[87]
    )
    await prepare_query_plan(bound)
    assert bound.file_ids == [-1]
