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
    ctx = PipelineContext(question="hello?", session_id=session_id)
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
