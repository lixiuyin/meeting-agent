from contextlib import nullcontext
from unittest.mock import AsyncMock

import pytest

from src.core.config import settings
from src.services.chain import _steps_generate
from src.services.chain._context import PipelineContext
from src.services.chain._steps_context import (
    load_entity_context,
    load_memories,
    load_session_context,
)


@pytest.mark.anyio
async def test_off_mode_skips_recall_and_extraction(monkeypatch):
    from src.services import jobs
    from src.services.knowledge_graph import kg_service
    from src.services.memory import memory_service, session_summary_service

    get_memory = AsyncMock()
    search_memory = AsyncMock()
    enqueue = AsyncMock()
    get_entity_context = AsyncMock()
    search_sessions = AsyncMock()
    monkeypatch.setattr(memory_service, "get", get_memory)
    monkeypatch.setattr(memory_service, "search_semantic", search_memory)
    monkeypatch.setattr(jobs, "enqueue_durable_job", enqueue)
    monkeypatch.setattr(kg_service, "get_entity_context", get_entity_context)
    monkeypatch.setattr(session_summary_service, "search_sessions", search_sessions)
    ctx = PipelineContext(question="q", answer="a", memory_mode="off")

    await load_memories(ctx)
    await load_entity_context(ctx)
    await load_session_context(ctx)
    await _steps_generate.schedule_fact_extraction(ctx)

    get_memory.assert_not_awaited()
    search_memory.assert_not_awaited()
    enqueue.assert_not_awaited()
    get_entity_context.assert_not_awaited()
    search_sessions.assert_not_awaited()
    skipped = {
        span.label
        for span in ctx.trace.spans
        if span.skipped and span.metadata.get("skip_reason") == "memory_mode_off"
    }
    assert skipped == {"load_entity_context", "load_session_context"}


@pytest.mark.anyio
async def test_durable_extraction_replays_original_memory_mode(monkeypatch):
    observed: dict[str, object] = {}

    async def _capture(_payload):
        observed.update(
            auto_extract=settings.MEMORY_AUTO_EXTRACT,
            max_context=settings.MEMORY_MAX_CONTEXT_ITEMS,
            knowledge_graph=settings.KNOWLEDGE_GRAPH_ENABLED,
        )

    monkeypatch.setattr(
        _steps_generate,
        "_run_fact_extraction_job_with_active_mode",
        _capture,
    )

    await _steps_generate.run_fact_extraction_job({"memory_mode": "deep"})

    assert observed == {
        "auto_extract": True,
        "max_context": 8,
        "knowledge_graph": True,
    }


@pytest.mark.anyio
async def test_scheduled_extraction_carries_per_file_revisions_and_exact_refs(monkeypatch):
    enqueue = AsyncMock()
    monkeypatch.setattr("src.services.jobs.enqueue_durable_job", enqueue)
    monkeypatch.setattr(_steps_generate, "get_connection", lambda: nullcontext(object()))
    monkeypatch.setattr(
        _steps_generate,
        "get_meeting_file",
        lambda _conn, file_id, *, user_id: {"updated_at": f"revision-{file_id}"},
    )
    ctx = PipelineContext(
        question="Who owns Orbit?",
        answer="Alice",
        user_id="revision-user",
        session_id="session-1",
        meeting_ids=None,
        file_ids=None,
    )
    ctx.docs = [
        {
            "content": "Alice owns Orbit.",
            "metadata": {
                "meeting_id": 7,
                "file_id": 9,
                "document_revision": "doc-revision-9",
                "page_number": 3,
                "chunk_index": 4,
            },
        },
        {
            "content": "The call starts here.",
            "metadata": {
                "meeting_id": 7,
                "file_id": 10,
                "timestamp_start": 12.5,
                "timestamp_end": 18,
            },
        },
    ]

    await _steps_generate.schedule_fact_extraction(ctx)

    payload = enqueue.await_args.kwargs["payload"]
    assert payload["meeting_ids"] == [7]
    assert payload["file_ids"] == [9, 10]
    assert payload["source_file_revisions"] == [
        {
            "file_id": 9,
            "source_revision": "revision-9",
            "updated_at": "revision-9",
        },
        {
            "file_id": 10,
            "source_revision": "revision-10",
            "updated_at": "revision-10",
        },
    ]
    assert payload["evidence_refs"] == [
        {
            "file_id": 9,
            "meeting_id": 7,
            "source_revision": "doc-revision-9",
            "page_number": 3,
            "chunk_index": 4,
        },
        {
            "file_id": 10,
            "meeting_id": 7,
            "source_revision": "revision-10",
            "timestamp_start": 12.5,
            "timestamp_end": 18,
        },
    ]


@pytest.mark.anyio
async def test_extraction_job_rejects_one_stale_file_in_multi_file_scope(monkeypatch):
    extract = AsyncMock()
    monkeypatch.setattr(
        "src.services.chain._extraction.run_combined_extraction",
        extract,
    )
    monkeypatch.setattr(_steps_generate, "get_connection", lambda: nullcontext(object()))
    monkeypatch.setattr(
        _steps_generate,
        "get_meeting_file",
        lambda _conn, file_id, *, user_id: {
            "updated_at": "revision-1" if file_id == 1 else "new-revision-2"
        },
    )

    await _steps_generate._run_fact_extraction_job_with_active_mode(
        {
            "user_id": "revision-user",
            "file_ids": [1, 2],
            "source_file_revisions": [
                {"file_id": 1, "updated_at": "revision-1"},
                {"file_id": 2, "updated_at": "revision-2"},
            ],
            "evidence_text": "grounded source",
        }
    )

    extract.assert_not_awaited()
