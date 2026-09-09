"""Regression tests for ``save_messages`` when the parent chat session is
deleted concurrently while a streaming pipeline is in flight.

A user can hit ``DELETE /api/v1/sessions/{id}`` mid-stream — the answer has
already been streamed over SSE, but the pipeline's tail-end persistence
fires after the parent row is gone, hitting an ``sqlite3.IntegrityError:
FOREIGN KEY constraint failed``. The fix is to detect the missing session
and skip persistence cleanly.
"""

import os
import tempfile
from pathlib import Path

import pytest

# Set up test environment before importing app modules.
os.environ.setdefault("API_KEY", "")
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())

from src.core import constants as constants_module

constants_module.DATA_DIR = Path(os.environ["DATA_DIR"])
constants_module.DATABASE_PATH = constants_module.DATA_DIR / "test.db"

from src.core import database as db  # noqa: E402
from src.core.database import get_write_connection, init_db  # noqa: E402
from src.services.chain import PipelineContext  # noqa: E402
from src.services.chain._steps_generate import save_messages  # noqa: E402
from src.services.chain._steps_session import ensure_session  # noqa: E402


@pytest.fixture(autouse=True)
def _initialized_db(tmp_path, monkeypatch):
    """Use a fresh temp DB per test so deletes don't leak across tests."""
    db_path = tmp_path / "race.db"
    monkeypatch.setattr(constants_module, "DATABASE_PATH", db_path)
    monkeypatch.setattr("src.core.config.settings.DB_PATH", db_path)
    # Reset connection pool so a new thread-local connection points at the
    # new path.
    from src.core.database import close_all_connections

    close_all_connections()
    init_db()
    yield
    close_all_connections()


def _make_ctx(session_id: str, *, answer: str = "the answer") -> PipelineContext:
    ctx = PipelineContext(question="hello?", session_id=session_id, user_id="u1")
    ctx.answer = answer
    ctx.docs = []
    return ctx


def test_save_messages_skips_when_session_deleted_before_persist(caplog):
    """The classic race: pipeline hands off to save_messages after the user
    deleted the session.  Must not raise; must log a clean warning.
    """
    session_id = "deleted-session-id"
    # Create the session, then delete it — simulates the mid-stream race.
    with get_write_connection() as conn:
        db.create_session(conn, user_id="u1", title="t", session_id=session_id)
    with get_write_connection() as conn:
        db.delete_session(conn, session_id)

    ctx = _make_ctx(session_id)
    with caplog.at_level("WARNING"):
        save_messages(ctx)  # must NOT raise

    # Cleanly logged — operator can see why the answer wasn't persisted.
    assert any(
        "deleted while streaming" in rec.message
        or "deleted during message persistence" in rec.message
        for rec in caplog.records
    ), f"expected a deletion warning; got: {[r.message for r in caplog.records]}"


def test_save_messages_persists_when_session_exists():
    """Normal path: session present → both messages persisted."""
    session_id = "live-session-id"
    with get_write_connection() as conn:
        db.create_session(conn, user_id="u1", title="t", session_id=session_id)

    ctx = _make_ctx(session_id, answer="hi there")
    save_messages(ctx)

    from src.core.database import get_connection

    with get_connection() as conn:
        rows = db.get_messages(conn, session_id)
    assert len(rows) == 2
    assert rows[0]["role"] == "human"
    assert rows[1]["role"] == "ai"
    assert rows[1]["content"] == "hi there"


def test_saved_snapshot_continuation_restores_scope_and_citation_preview():
    import json

    session_id = "saved-snapshot-session"
    with get_write_connection() as conn:
        db.create_session(conn, user_id="u1", title="t", session_id=session_id)
        conn.execute(
            "UPDATE chat_sessions SET task_state_json=? WHERE id=?",
            (
                json.dumps(
                    {
                        "schema_version": 3,
                        "active_scope": {
                            "meeting_ids": [7],
                            "file_ids": [9],
                            "date_from": "2025-01-01",
                            "date_to": "2025-03-01",
                        },
                    }
                ),
                session_id,
            ),
        )
        db.add_turn(
            conn,
            session_id=session_id,
            human_content="question",
            ai_content="answer",
            sources_json=json.dumps(
                [
                    {
                        "meeting_id": 7,
                        "file_id": 9,
                        "chunk_index": 2,
                        "document_revision": "rev-1",
                        "content": "Alice owns Orbit.",
                    }
                ]
            ),
        )

    ctx = PipelineContext(
        question="continue",
        session_id=session_id,
        user_id="u1",
        continuation_mode="saved_snapshot",
    )
    ensure_session(ctx)

    assert ctx.meeting_ids == [7]
    assert ctx.file_ids == [9]
    assert ctx.date_from.isoformat() == "2025-01-01"
    assert ctx.date_to.isoformat() == "2025-03-01"
    assert len(ctx.docs) == 1
    assert ctx.docs[0]["content"] == "Alice owns Orbit."
    assert ctx.docs[0]["metadata"]["document_revision"] == "rev-1"
    assert ctx.docs[0]["saved_snapshot"] is True
    assert "revision=rev-1" in ctx.restored_source_context
    assert "Alice owns Orbit" in ctx.restored_source_context


def test_saved_scope_continuation_restores_scope_without_replaying_evidence():
    import json

    session_id = "saved-scope-session"
    with get_write_connection() as conn:
        db.create_session(conn, user_id="u1", title="t", session_id=session_id)
        conn.execute(
            "UPDATE chat_sessions SET task_state_json=? WHERE id=?",
            (
                json.dumps(
                    {
                        "schema_version": 3,
                        "active_scope": {"meeting_ids": [7], "file_ids": [9]},
                    }
                ),
                session_id,
            ),
        )
        db.add_turn(
            conn,
            session_id=session_id,
            human_content="question",
            ai_content="answer",
            sources_json=json.dumps([{"content": "Old evidence", "meeting_id": 7}]),
        )

    ctx = PipelineContext(
        question="continue",
        session_id=session_id,
        user_id="u1",
        continuation_mode="saved_scope",
    )
    ensure_session(ctx)

    assert ctx.meeting_ids == [7]
    assert ctx.file_ids == [9]
    assert ctx.docs == []
    assert ctx.restored_source_context == ""


def test_saved_snapshot_round_trip_restores_exact_frozen_context():
    session_id = "exact-snapshot-session"
    with get_write_connection() as conn:
        db.create_session(conn, user_id="u1", title="t", session_id=session_id)

    original = _make_ctx(session_id, answer="answer")
    original.combined_context = "frozen context with complete evidence"
    original.docs = [
        {
            "content": "complete document content",
            "score": 0.9,
            "metadata": {"chunk_id": "chunk-1", "file_id": 9, "meeting_id": 7},
        }
    ]
    original.memory_context = "current memory at answer time"
    save_messages(original)

    restored = PipelineContext(
        question="continue",
        session_id=session_id,
        user_id="u1",
        continuation_mode="saved_snapshot",
    )
    ensure_session(restored)

    assert restored.snapshot_restored is True
    assert restored.frozen_combined_context == "frozen context with complete evidence"
    assert restored.docs[0]["content"] == "complete document content"
    assert restored.docs[0]["metadata"]["chunk_id"] == "chunk-1"
    assert isinstance(restored.frozen_snapshot_source_ai_message_id, int)


def test_saved_snapshot_rejects_tampered_payload():
    import json

    session_id = "tampered-snapshot-session"
    with get_write_connection() as conn:
        db.create_session(conn, user_id="u1", title="t", session_id=session_id)
    original = _make_ctx(session_id, answer="answer")
    original.combined_context = "trusted frozen context"
    save_messages(original)
    with get_write_connection() as conn:
        state = json.loads(db.get_session(conn, session_id, user_id="u1")["task_state_json"])
        state["frozen_snapshot"]["combined_context"] = "tampered"
        conn.execute(
            "UPDATE chat_sessions SET task_state_json=? WHERE id=?",
            (json.dumps(state), session_id),
        )

    restored = PipelineContext(
        question="continue",
        session_id=session_id,
        user_id="u1",
        continuation_mode="saved_snapshot",
    )
    from src.core.exceptions import ContinuationSnapshotError

    with pytest.raises(ContinuationSnapshotError, match="No recoverable evidence"):
        ensure_session(restored)
    assert restored.snapshot_restored is False
    assert restored.frozen_combined_context == ""
    assert restored.snapshot_restore_status == "unavailable"


def test_save_messages_preserves_initial_objective_for_follow_up_in_task_state_v4():
    import json

    session_id = "continued-task-session"
    with get_write_connection() as conn:
        db.create_session(conn, user_id="u1", title="t", session_id=session_id)

    first = _make_ctx(session_id, answer="first answer")
    first.question = "Audit all Atlas action items"
    save_messages(first)
    with db.get_connection() as conn:
        first_state = json.loads(db.get_session(conn, session_id, user_id="u1")["task_state_json"])

    second = _make_ctx(session_id, answer="follow-up answer")
    second.question = "Which ones are overdue?"
    second.session_task_state = first_state
    save_messages(second)
    with db.get_connection() as conn:
        state = json.loads(db.get_session(conn, session_id, user_id="u1")["task_state_json"])

    assert state["schema_version"] == 4
    assert state["objective"] == "Audit all Atlas action items"
    assert state["last_query"] == "Which ones are overdue?"
    assert state["turn_count"] == 2


def test_save_messages_updates_active_objective_when_user_changes_task():
    import json

    session_id = "changed-task-session"
    with get_write_connection() as conn:
        db.create_session(conn, user_id="u1", title="t", session_id=session_id)
    first = _make_ctx(session_id, answer="first answer")
    first.question = "Audit Atlas action items"
    save_messages(first)
    with db.get_connection() as conn:
        first_state = json.loads(db.get_session(conn, session_id, user_id="u1")["task_state_json"])

    second = _make_ctx(session_id, answer="second answer")
    second.question = "Compare the Design and Security Review decisions"
    second.session_task_state = first_state
    save_messages(second)
    with db.get_connection() as conn:
        state = json.loads(db.get_session(conn, session_id, user_id="u1")["task_state_json"])
    assert state["root_objective"] == "Audit Atlas action items"
    assert state["objective"] == "Compare the Design and Security Review decisions"
    assert state["objective_history"] == ["Audit Atlas action items"]


def test_history_add_message_swallows_fk_violation_and_keeps_cache_consistent(
    caplog,
):
    """When the parent row is deleted between probe and INSERT, the FK
    backstop in ``SQLiteChatMessageHistory.add_message`` must drop the
    write AND skip the in-memory append so the cache stays in sync with
    disk.
    """
    from langchain_core.messages import HumanMessage

    from src.services.memory._history import SQLiteChatMessageHistory

    session_id = "fk-test-session"
    with get_write_connection() as conn:
        db.create_session(conn, user_id="u1", title="t", session_id=session_id)

    history = SQLiteChatMessageHistory(session_id)
    initial_len = len(history.messages)

    # Simulate the race: delete the parent row after the history was loaded.
    with get_write_connection() as conn:
        db.delete_session(conn, session_id)

    with caplog.at_level("WARNING"):
        history.add_message(HumanMessage(content="orphaned"))

    # In-memory list must NOT have grown — DB write failed, cache stays
    # consistent with the (now empty) on-disk state.
    assert len(history.messages) == initial_len
    assert any(
        "FK violation" in rec.message or "deleted" in rec.message for rec in caplog.records
    ), f"expected an FK warning; got: {[r.message for r in caplog.records]}"


def test_incomplete_turn_keeps_quality_status_in_history():
    ctx = _make_ctx("incomplete-turn")
    ensure_session(ctx)
    ctx.degraded = True
    ctx.degradation_reason = "generation_timeout"
    save_messages(ctx)
    with db.get_connection() as conn:
        messages = db.get_messages(conn, ctx.session_id)
    assert messages[-1]["content"] == ctx.answer
    assert messages[-1]["degradation_reason"] == "generation_timeout"
    assert messages[-2]["degradation_reason"] is None
