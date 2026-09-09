"""Retention must not lose external-vector deletion work."""

from src.core import database as db
from src.core.config import settings
from src.core.database import get_write_connection
from src.services.retention import purge_old_chat_messages, purge_stale_low_importance_memories


def test_old_session_summary_vector_is_queued(monkeypatch):
    monkeypatch.setattr(settings, "CHAT_MESSAGE_RETENTION_DAYS", 1)
    with get_write_connection() as conn:
        session_id = db.create_session(conn, user_id="retention-user")
        db.add_message(conn, session_id=session_id, role="human", content="old message")
        db.upsert_session_summary(
            conn,
            session_id=session_id,
            user_id="retention-user",
            summary="old summary",
            embedding_id="old-summary-vector",
        )
        conn.execute(
            "UPDATE chat_sessions SET updated_at=datetime('now', '-2 days') WHERE id=?",
            (session_id,),
        )

    assert purge_old_chat_messages() == 1
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM pending_vector_deletions "
            "WHERE collection='session_summary' AND embedding_id='old-summary-vector'"
        ).fetchone()
    assert row is not None


def test_memory_delete_rolls_back_when_outbox_insert_fails():
    with get_write_connection() as conn:
        conn.execute(
            "INSERT INTO user_memories (user_id, key, value, importance, embedding_id, updated_at) "
            "VALUES ('retention-user', 'old', 'value', 0.1, 'old-memory-vector', "
            "datetime('now', '-100 days'))"
        )
        conn.execute(
            "CREATE TRIGGER fail_retention_outbox BEFORE INSERT ON pending_vector_deletions "
            "WHEN NEW.embedding_id='old-memory-vector' BEGIN "
            "SELECT RAISE(ABORT, 'forced outbox failure'); END"
        )
    assert purge_stale_low_importance_memories() == 0
    with get_write_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM user_memories WHERE embedding_id='old-memory-vector'"
        ).fetchone()
        conn.execute("DROP TRIGGER fail_retention_outbox")
    assert row is not None
