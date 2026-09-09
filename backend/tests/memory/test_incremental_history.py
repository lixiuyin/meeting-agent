from unittest.mock import AsyncMock

import pytest

from src.core import database as db
from src.services.memory import _history_context


@pytest.mark.asyncio
async def test_checkpoint_advances_without_resummarizing_completed_prefix(monkeypatch):
    with db.get_write_connection() as conn:
        sid = db.create_session(conn, user_id="checkpoint-test")
        for index in range(205):
            db.add_message(conn, session_id=sid, role="human", content=f"fact {index}")
    summarize = AsyncMock(return_value="compacted facts")
    monkeypatch.setattr(_history_context, "summarize_messages", summarize)
    _, first = await _history_context.load_incremental_history(sid, "gpt-4o-mini", 100)
    assert first["backlog"] is True
    assert summarize.await_count == 2
    _, second = await _history_context.load_incremental_history(sid, "gpt-4o-mini", 100)
    assert second["through_message_id"] > first["through_message_id"]
    assert second["backlog"] is False
    calls = summarize.await_count
    _, third = await _history_context.load_incremental_history(sid, "gpt-4o-mini", 100)
    assert third["through_message_id"] == second["through_message_id"]
    assert summarize.await_count == calls
    with db.get_write_connection() as conn:
        db.clear_messages(conn, sid)
        assert (
            conn.execute(
                "SELECT 1 FROM chat_context_checkpoints WHERE session_id=?", (sid,)
            ).fetchone()
            is None
        )


@pytest.mark.asyncio
async def test_manual_summary_is_available_when_background_summary_disabled(monkeypatch):
    from src.core.config import settings
    from src.services.memory import session_summary_service

    monkeypatch.setattr(settings, "SESSION_SUMMARY_ENABLED", False)
    with db.get_write_connection() as conn:
        sid = db.create_session(conn, user_id="manual-summary")
        for _ in range(10):
            db.add_message(conn, session_id=sid, role="human", content="stored fact")
        db.upsert_session_summary(
            conn, session_id=sid, user_id="manual-summary", summary="saved summary", turn_count=10
        )
    assert await session_summary_service.summarize_session(sid, "manual-summary") is None
    summary = await session_summary_service.summarize_session(sid, "manual-summary", force=True)
    assert summary["summary"] == "saved summary"
