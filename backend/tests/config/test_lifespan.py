import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from src.api.lifespan import (
    _prewarm_skill_matcher_once,
    _recover_incomplete_file_summaries,
    _startup_summary_backfill_enabled,
)
from src.core import database as db
from src.core.database import get_write_connection
from src.services.parser._http import (
    close_parser_http_client,
    get_parser_http_client,
)


def test_startup_backfill_disabled_by_default(monkeypatch):
    from src.core.config import settings

    monkeypatch.setattr(settings, "SESSION_SUMMARY_ENABLED", True)
    monkeypatch.setattr(settings, "SESSION_SUMMARY_STARTUP_BACKFILL", False)
    assert _startup_summary_backfill_enabled() is False


@pytest.mark.asyncio
async def test_incomplete_file_summary_is_normalized_when_auto_summary_disabled(monkeypatch):
    from src.api import lifespan as lifespan_module

    monkeypatch.setattr(lifespan_module.settings, "MEETING_AUTO_SUMMARIZE_FILES", False)
    with get_write_connection() as conn:
        meeting_id = db.create_meeting(conn, title="Interrupted summary", user_id="default")
        file_id = db.create_meeting_file(
            conn,
            meeting_id=meeting_id,
            file_type="txt",
            file_name="notes.txt",
            file_path="/tmp/notes.txt",
        )
        db.update_meeting_file_status(conn, file_id, "summarizing", transcript="parsed")
        db.update_meeting_status(conn, meeting_id, "processing")
        db.update_meeting_status(conn, meeting_id, "summarizing")

    requeued, normalized = await _recover_incomplete_file_summaries()

    assert (requeued, normalized) == (0, 1)
    with db.get_connection() as conn:
        file_row = db.get_meeting_file(conn, file_id)
        meeting_row = db.get_meeting(conn, meeting_id)
    assert file_row is not None and file_row["status"] == "ready"
    assert file_row["summary_status"] == "pending"
    assert meeting_row is not None and meeting_row["status"] == "ready"


@pytest.mark.asyncio
async def test_incomplete_file_summary_is_requeued_when_auto_summary_enabled(monkeypatch):
    from src.api import lifespan as lifespan_module
    from src.services.processor import _pipeline_common

    monkeypatch.setattr(lifespan_module.settings, "MEETING_AUTO_SUMMARIZE_FILES", True)
    with get_write_connection() as conn:
        meeting_id = db.create_meeting(conn, title="Interrupted summary", user_id="default")
        file_id = db.create_meeting_file(
            conn,
            meeting_id=meeting_id,
            file_type="txt",
            file_name="notes.txt",
            file_path="/tmp/notes.txt",
        )
        db.update_meeting_file_status(conn, file_id, "summarizing", transcript="parsed")
        db.update_meeting_status(conn, meeting_id, "processing")
        db.update_meeting_status(conn, meeting_id, "summarizing")

    scheduled: list[tuple[int, int]] = []

    async def _record_schedule(scheduled_file_id: int, scheduled_meeting_id: int) -> None:
        scheduled.append((scheduled_file_id, scheduled_meeting_id))

    monkeypatch.setattr(_pipeline_common, "schedule_post_ready_summary", _record_schedule)

    requeued, normalized = await _recover_incomplete_file_summaries()

    assert (requeued, normalized) == (1, 0)
    assert scheduled == [(file_id, meeting_id)]
    with db.get_connection() as conn:
        file_row = db.get_meeting_file(conn, file_id)
    assert file_row is not None and file_row["status"] == "summarizing"


@pytest.mark.asyncio
async def test_skill_matcher_prewarm_batches_corpus_before_query_warmup() -> None:
    calls = []
    summaries = [
        SimpleNamespace(
            name="first",
            intent_matching=SimpleNamespace(method="hybrid", examples=["one"]),
        ),
        SimpleNamespace(
            name="second",
            intent_matching=SimpleNamespace(method="semantic", examples=["two"]),
        ),
        SimpleNamespace(
            name="keyword-only",
            intent_matching=SimpleNamespace(method="keyword", examples=[]),
        ),
    ]

    class _Loader:
        def load_summaries(self):
            return summaries

    class _SemanticMatcher:
        def precompute_skills_embeddings(self, skills):
            calls.append(("corpus", [skill.name for skill in skills]))
            return {skill.name for skill in skills}

        def embed_query(self, query):
            calls.append(("query", query))

    matcher = SimpleNamespace(semantic_matcher=_SemanticMatcher())

    summary_count, precomputed = await _prewarm_skill_matcher_once(_Loader(), matcher)

    assert summary_count == 3
    assert precomputed == 2
    assert calls == [
        ("corpus", ["first", "second"]),
        ("query", "warmup"),
    ]


# ── Lifespan shutdown cleanliness ────────────────────────────────────────────


@pytest.mark.anyio
async def test_parser_shutdown_after_threadpool_no_warnings(caplog):
    """Simulate the ThreadPoolExecutor + asyncio.run pattern followed by
    lifespan shutdown — no 'Event loop is closed' warnings."""

    # 1. Create a client in a worker thread (simulating cascade dispatch)
    def worker() -> None:
        async def inner() -> None:
            get_parser_http_client()

        asyncio.run(inner())

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor(max_workers=1) as ex:
        await loop.run_in_executor(ex, worker)
    # Worker loop is now closed, client may linger

    # 2. Simulate lifespan shutdown calling close_parser_http_client
    caplog.set_level(logging.WARNING, logger="src.services.parser._http")
    await close_parser_http_client()

    assert not any("Event loop is closed" in r.message for r in caplog.records), (
        f"Unexpected warnings: {[r.message for r in caplog.records]}"
    )
