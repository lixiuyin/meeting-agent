"""Tests for session summary database CRUD, FTS search, and provenance."""

import json
import time

from src.core import database as db
from src.core.database import get_write_connection


def _create_session_with_messages(user_id: str, title: str, messages: list[tuple[str, str]]) -> str:
    """Helper: create a session with messages directly in DB."""
    with get_write_connection() as conn:
        session_id = db.create_session(conn, user_id=user_id, title=title)
        for role, content in messages:
            db.add_message(conn, session_id=session_id, role=role, content=content)
    return session_id


class TestSessionSummaryCRUD:
    def test_upsert_and_get_summary(self):
        """Upsert a session summary, then retrieve it."""
        user_id = "summary_crud_user"
        session_id = _create_session_with_messages(
            user_id, "Test Session", [("human", "hi"), ("ai", "hello")]
        )

        with get_write_connection() as conn:
            db.upsert_session_summary(
                conn,
                session_id=session_id,
                user_id=user_id,
                summary="We discussed greetings.",
                topics=json.dumps(["greetings"]),
                key_entities=json.dumps(["user"]),
                decisions=json.dumps([]),
                turn_count=2,
            )

        with db.get_connection() as conn:
            result = db.get_session_summary(conn, session_id)

        assert result is not None
        assert result["summary"] == "We discussed greetings."
        assert json.loads(result["topics"]) == ["greetings"]
        assert result["turn_count"] == 2

    def test_upsert_updates_existing(self):
        """Upserting with the same session_id updates the existing row."""
        user_id = "upsert_user"
        session_id = _create_session_with_messages(
            user_id, "Upsert Test", [("human", "q"), ("ai", "a")]
        )

        with get_write_connection() as conn:
            db.upsert_session_summary(
                conn,
                session_id=session_id,
                user_id=user_id,
                summary="First version",
                turn_count=2,
            )

        # CURRENT_TIMESTAMP has second-level precision; ensure the second upsert
        # gets a strictly newer timestamp so the ON CONFLICT WHERE guard fires.
        time.sleep(1.0)

        with get_write_connection() as conn:
            db.upsert_session_summary(
                conn,
                session_id=session_id,
                user_id=user_id,
                summary="Updated version",
                turn_count=4,
            )

        with db.get_connection() as conn:
            result = db.get_session_summary(conn, session_id)
        assert result["summary"] == "Updated version"
        assert result["turn_count"] == 4

    def test_late_stale_summary_cannot_overwrite_more_complete_summary(self):
        """LLM completion order must not move summarized coverage backwards."""
        user_id = "monotonic_summary_user"
        session_id = _create_session_with_messages(
            user_id, "Concurrent Summary", [("human", "q"), ("ai", "a")]
        )

        with get_write_connection() as conn:
            db.upsert_session_summary(
                conn,
                session_id=session_id,
                user_id=user_id,
                summary="Covers twelve messages",
                turn_count=12,
            )
            # Make the stored timestamp older so the former `turn_count OR
            # updated_at` predicate would incorrectly accept the stale write.
            conn.execute(
                "UPDATE session_summaries SET updated_at="
                "datetime('now', '-1 day') WHERE session_id=?",
                (session_id,),
            )
            db.upsert_session_summary(
                conn,
                session_id=session_id,
                user_id=user_id,
                summary="Late result covering only ten messages",
                turn_count=10,
            )

        with db.get_connection() as conn:
            result = db.get_session_summary(conn, session_id)
        assert result is not None
        assert result["summary"] == "Covers twelve messages"
        assert result["turn_count"] == 12

    def test_equal_coverage_retry_can_replace_older_summary(self):
        """A retry for the same snapshot may improve an older generated result."""
        user_id = "equal_coverage_summary_user"
        session_id = _create_session_with_messages(
            user_id, "Summary Retry", [("human", "q"), ("ai", "a")]
        )

        with get_write_connection() as conn:
            db.upsert_session_summary(
                conn,
                session_id=session_id,
                user_id=user_id,
                summary="First attempt",
                turn_count=2,
            )
            conn.execute(
                "UPDATE session_summaries SET updated_at="
                "datetime('now', '-1 day') WHERE session_id=?",
                (session_id,),
            )
            db.upsert_session_summary(
                conn,
                session_id=session_id,
                user_id=user_id,
                summary="Improved retry",
                turn_count=2,
            )

        with db.get_connection() as conn:
            result = db.get_session_summary(conn, session_id)
        assert result is not None
        assert result["summary"] == "Improved retry"
        assert result["turn_count"] == 2

    def test_list_summaries(self):
        """List summaries returns them ordered by creation date."""
        user_id = "list_summary_user"
        for i in range(3):
            sid = _create_session_with_messages(
                user_id, f"Session {i}", [("human", f"q{i}"), ("ai", f"a{i}")]
            )
            with get_write_connection() as conn:
                db.upsert_session_summary(
                    conn,
                    session_id=sid,
                    user_id=user_id,
                    summary=f"Summary {i}",
                    turn_count=2,
                )

        with db.get_connection() as conn:
            summaries = db.list_session_summaries(conn, user_id=user_id, limit=10)
        assert len(summaries) == 3

    def test_get_nonexistent_summary(self):
        """Getting a summary for a session with no summary returns None."""
        with db.get_connection() as conn:
            result = db.get_session_summary(conn, "nonexistent_session")
        assert result is None

    def test_delete_summary_directly(self):
        """Deleting a session summary removes it from the database."""
        user_id = "delete_summ_user"
        session_id = _create_session_with_messages(
            user_id, "Delete Summary", [("human", "x"), ("ai", "y")]
        )
        with get_write_connection() as conn:
            db.upsert_session_summary(
                conn,
                session_id=session_id,
                user_id=user_id,
                summary="Will be deleted",
            )

        with get_write_connection() as conn:
            db.delete_session_summary(conn, session_id)

        with db.get_connection() as conn:
            result = db.get_session_summary(conn, session_id)
        assert result is None


class TestUnsummarizedSessions:
    def test_finds_sessions_without_summary(self):
        """Sessions with enough messages but no summary are detected."""
        user_id = "unsummarized_user"
        _create_session_with_messages(
            user_id,
            "Rich Session",
            [
                ("human", "q1"),
                ("ai", "a1"),
                ("human", "q2"),
                ("ai", "a2"),
                ("human", "q3"),
                ("ai", "a3"),
            ],
        )

        with db.get_connection() as conn:
            unsummarized = db.get_unsummarized_sessions(conn, user_id=user_id, min_messages=4)
        assert len(unsummarized) >= 1
        assert unsummarized[0]["message_count"] >= 4

    def test_ignores_sessions_with_summary(self):
        """Sessions that already have a summary are excluded."""
        user_id = "summarized_user"
        sid = _create_session_with_messages(
            user_id,
            "Already Summarized",
            [("human", "q"), ("ai", "a")] * 4,
        )
        with get_write_connection() as conn:
            db.upsert_session_summary(
                conn,
                session_id=sid,
                user_id=user_id,
                summary="Already done",
            )

        with db.get_connection() as conn:
            unsummarized = db.get_unsummarized_sessions(conn, user_id=user_id, min_messages=4)
        session_ids = [s["id"] for s in unsummarized]
        assert sid not in session_ids

    def test_ignores_sessions_below_threshold(self):
        """Sessions with fewer messages than the threshold are excluded."""
        user_id = "short_session_user"
        _create_session_with_messages(user_id, "Short", [("human", "hi"), ("ai", "hello")])

        with db.get_connection() as conn:
            unsummarized = db.get_unsummarized_sessions(conn, user_id=user_id, min_messages=4)
        assert len(unsummarized) == 0


class TestChatMessageFTS:
    def test_hyphenated_identifier_uses_token_boundaries(self):
        user_id = "fts_identifier_user"
        session_id = _create_session_with_messages(
            user_id,
            "Incident",
            [("human", "Please inspect incident ZYX-9921"), ("ai", "Resolved")],
        )

        with db.get_connection() as conn:
            results = db.search_chat_messages(
                conn,
                user_id=user_id,
                query="ZYX-9921",
                limit=10,
            )

        assert {row["session_id"] for row in results} == {session_id}

    def test_search_finds_matching_messages(self):
        """FTS search returns messages containing the search term."""
        user_id = "fts_user"
        _create_session_with_messages(
            user_id,
            "Budget Discussion",
            [
                ("human", "What is the budget for project alpha?"),
                ("ai", "The budget for project alpha is $50,000."),
            ],
        )

        with db.get_connection() as conn:
            results = db.search_chat_messages(conn, user_id=user_id, query="budget", limit=10)
        assert len(results) >= 1
        assert any("budget" in r["content"].lower() for r in results)

    def test_search_scoped_to_user(self):
        """FTS search only returns messages from the specified user."""
        _create_session_with_messages(
            "fts_user_a",
            "A's Session",
            [("human", "alpha topic"), ("ai", "alpha response")],
        )
        _create_session_with_messages(
            "fts_user_b",
            "B's Session",
            [("human", "alpha topic different user"), ("ai", "response b")],
        )

        with db.get_connection() as conn:
            results_a = db.search_chat_messages(conn, user_id="fts_user_a", query="alpha", limit=10)
            results_b = db.search_chat_messages(conn, user_id="fts_user_b", query="alpha", limit=10)
        # Each user should only see their own messages
        for r in results_a:
            assert r["session_id"] is not None
        for r in results_b:
            assert r["session_id"] is not None
        # Ensure results are from different sessions
        a_sessions = {r["session_id"] for r in results_a}
        b_sessions = {r["session_id"] for r in results_b}
        assert a_sessions.isdisjoint(b_sessions)

    def test_search_empty_result(self):
        """FTS search returns empty list when nothing matches."""
        with db.get_connection() as conn:
            results = db.search_chat_messages(
                conn,
                user_id="nonexistent_user",
                query="xyznonexistent",
                limit=10,
            )
        assert results == []


class TestMemoryProvenance:
    def test_provenance_stored_on_memory(self):
        """session_id and turn_index are stored when set on memory."""
        user_id = "provenance_user"
        session_id = _create_session_with_messages(
            user_id, "Source Session", [("human", "hi"), ("ai", "hello")]
        )

        from src.services.memory import memory_service

        memory_service.set(
            user_id,
            "test_fact",
            "test value",
            source="auto_extracted",
            session_id=session_id,
            turn_index=1,
        )

        with db.get_connection() as conn:
            mem = db.get_memory_full(conn, user_id=user_id, key="test_fact")
        assert mem is not None
        assert mem["session_id"] == session_id
        assert mem["turn_index"] == 1

    def test_provenance_null_when_not_provided(self):
        """Provenance columns are null when not explicitly provided."""
        from src.services.memory import memory_service

        memory_service.set("prov_null_user", "manual_fact", "value", source="manual")

        with db.get_connection() as conn:
            mem = db.get_memory_full(conn, user_id="prov_null_user", key="manual_fact")
        assert mem is not None
        assert mem.get("session_id") is None
        assert mem.get("turn_index") is None
