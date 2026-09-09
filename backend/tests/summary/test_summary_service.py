"""Tests for SessionSummaryService and pipeline integration."""

import asyncio
import json
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core import database as db
from src.core.database import get_write_connection


def _create_session_with_messages(user_id: str, title: str, messages: list[tuple[str, str]]) -> str:
    """Helper: create a session with messages directly in DB."""
    with get_write_connection() as conn:
        session_id = db.create_session(conn, user_id=user_id, title=title)
        for role, content in messages:
            db.add_message(conn, session_id=session_id, role=role, content=content)
    return session_id


class TestSessionSummaryService:
    def test_get_recent_summaries_empty(self):
        """get_recent_summaries returns empty list for user with no summaries."""
        from src.services.memory import session_summary_service

        result = session_summary_service.get_recent_summaries("no_summaries_user")
        assert result == []

    def test_get_recent_summaries_with_data(self):
        """get_recent_summaries returns stored summaries."""
        from src.services.memory import session_summary_service

        user_id = "recent_summ_user"
        sid = _create_session_with_messages(user_id, "Recent", [("human", "q"), ("ai", "a")])
        with get_write_connection() as conn:
            db.upsert_session_summary(
                conn,
                session_id=sid,
                user_id=user_id,
                summary="Recent session about testing.",
                topics=json.dumps(["testing"]),
                key_entities=json.dumps(["pytest"]),
                decisions=json.dumps(["use TDD"]),
                turn_count=2,
            )

        result = session_summary_service.get_recent_summaries(user_id, limit=5)
        assert len(result) == 1
        assert result[0]["summary"] == "Recent session about testing."
        assert result[0]["topics"] == ["testing"]
        assert result[0]["key_entities"] == ["pytest"]
        assert result[0]["decisions"] == ["use TDD"]

    @pytest.mark.asyncio
    async def test_search_sessions_filters_by_meeting(self):
        from src.services.memory import session_summary_service

        user_id = "scope_summary_user"
        with get_write_connection() as conn:
            sid_a = db.create_session(conn, user_id=user_id, title="A")
            sid_b = db.create_session(conn, user_id=user_id, title="B")
            sid_global = db.create_session(conn, user_id=user_id, title="G")
            db.upsert_session_summary(conn, session_id=sid_a, user_id=user_id, summary="summary a")
            db.upsert_session_summary(conn, session_id=sid_b, user_id=user_id, summary="summary b")
            db.upsert_session_summary(
                conn, session_id=sid_global, user_id=user_id, summary="summary g"
            )

        mock_vs = MagicMock()
        mock_vs.similarity_search.return_value = [
            {"session_id": sid_a, "score": 0.1, "meetings_covered": [101]},
            {"session_id": sid_b, "score": 0.2, "meetings_covered": [202]},
            {"session_id": sid_global, "score": 0.3, "meetings_covered": None},
        ]
        with patch(
            "src.services.memory._summary_service.get_summary_vectorstore", return_value=mock_vs
        ):
            results = await session_summary_service.search_sessions(
                user_id,
                "query",
                limit=5,
                meeting_ids=[101],
            )

        session_ids = {item["session_id"] for item in results}
        assert sid_a in session_ids
        assert sid_b not in session_ids
        assert sid_global not in session_ids

    @pytest.mark.asyncio
    async def test_search_sessions_fuses_vector_and_exact_fts_candidates(self):
        from src.services.memory import session_summary_service

        user_id = "hybrid_summary_user"
        vector_sid = _create_session_with_messages(
            user_id,
            "Semantic Session",
            [("human", "general planning"), ("ai", "general response")],
        )
        lexical_sid = _create_session_with_messages(
            user_id,
            "Exact Session",
            [("human", "ticket ZXQ-4817 owner"), ("ai", "assigned to Mei")],
        )
        with get_write_connection() as conn:
            db.upsert_session_summary(
                conn,
                session_id=vector_sid,
                user_id=user_id,
                summary="Semantically related planning notes",
            )
            db.upsert_session_summary(
                conn,
                session_id=lexical_sid,
                user_id=user_id,
                summary="Exact ticket ownership",
            )

        mock_vs = MagicMock()
        mock_vs.similarity_search.return_value = [
            {
                "session_id": vector_sid,
                "score": 0.9,
                "meetings_covered": None,
                "files_covered": None,
            }
        ]
        with patch(
            "src.services.memory._summary_service.get_summary_vectorstore",
            return_value=mock_vs,
        ):
            results = await session_summary_service.search_sessions(
                user_id,
                "ZXQ-4817",
                limit=5,
            )

        assert {item["session_id"] for item in results} == {vector_sid, lexical_sid}
        assert all(0.0 <= item["score"] <= 1.0 for item in results)
        assert {item["session_title"] for item in results} == {
            "Semantic Session",
            "Exact Session",
        }

    @pytest.mark.asyncio
    async def test_search_sessions_uses_fts_when_vector_backend_fails(self):
        from src.services.memory import session_summary_service

        user_id = "summary_fts_fallback_user"
        sid = _create_session_with_messages(
            user_id,
            "Fallback Session",
            [("human", "incident ZYX-9921"), ("ai", "resolved")],
        )
        with get_write_connection() as conn:
            db.upsert_session_summary(
                conn,
                session_id=sid,
                user_id=user_id,
                summary="Incident resolution",
            )

        mock_vs = MagicMock()
        mock_vs.similarity_search.side_effect = RuntimeError("chroma unavailable")
        with patch(
            "src.services.memory._summary_service.get_summary_vectorstore",
            return_value=mock_vs,
        ):
            results = await session_summary_service.search_sessions(
                user_id,
                "ZYX-9921",
                limit=5,
            )

        assert [item["session_id"] for item in results] == [sid]
        assert results[0]["score"] == 1.0

    @pytest.mark.asyncio
    async def test_public_past_search_keeps_semantic_results_when_fts_fails(self):
        """The outer API aggregation must not undo the inner vector fallback."""
        from src.services.memory._summary_service import SessionSummaryService

        user_id = "past_search_vector_fallback_user"
        sid = _create_session_with_messages(
            user_id,
            "Semantic fallback",
            [("human", "roadmap"), ("ai", "delivery plan")],
        )
        with get_write_connection() as conn:
            db.upsert_session_summary(
                conn,
                session_id=sid,
                user_id=user_id,
                summary="Roadmap delivery plan",
                turn_count=2,
            )

        vectorstore = MagicMock()
        vectorstore.similarity_search.return_value = [
            {
                "session_id": sid,
                "score": 0.8,
                "meetings_covered": None,
                "files_covered": None,
            }
        ]
        with (
            patch(
                "src.services.memory._summary_service.get_summary_vectorstore",
                return_value=vectorstore,
            ),
            patch(
                "src.services.memory._summary_service.db.search_chat_messages",
                side_effect=RuntimeError("fts unavailable"),
            ),
        ):
            results = await SessionSummaryService().search_past_conversations(
                user_id,
                "roadmap",
            )

        assert [item["session_id"] for item in results] == [sid]
        assert results[0]["type"] == "session_summary"
        assert results[0]["session_title"] == "Semantic fallback"

    @pytest.mark.asyncio
    async def test_public_past_search_keeps_fts_results_when_summary_search_fails(self):
        """Raw message recall remains available if summary enrichment fails."""
        from src.services.memory._summary_service import SessionSummaryService

        user_id = "past_search_fts_fallback_user"
        sid = _create_session_with_messages(
            user_id,
            "Lexical fallback",
            [("human", "incident ABC-7788"), ("ai", "resolved")],
        )
        service = SessionSummaryService()
        with patch.object(
            service,
            "search_sessions",
            new=AsyncMock(side_effect=RuntimeError("summary lookup unavailable")),
        ):
            results = await service.search_past_conversations(user_id, "ABC-7788")

        assert any(item["session_id"] == sid for item in results)
        assert all(item["type"] == "message" for item in results)

    @pytest.mark.asyncio
    async def test_existing_summary_updates_when_session_resumes(self):
        from src.services.memory._summary_service import SessionSummaryService

        user_id = "resumed_summary_user"
        sid = _create_session_with_messages(
            user_id,
            "Resumed",
            [("human", "q1"), ("ai", "a1"), ("human", "q2"), ("ai", "a2")],
        )
        responses = [
            MagicMock(
                content=json.dumps(
                    {"summary": "first", "topics": [], "key_entities": [], "decisions": []}
                )
            ),
            MagicMock(
                content=json.dumps(
                    {"summary": "updated", "topics": [], "key_entities": [], "decisions": []}
                )
            ),
        ]
        prompt = MagicMock()
        prompt.format.side_effect = lambda **kwargs: kwargs["conversation"]
        vectorstore = MagicMock()
        vectorstore.upsert.return_value = "summary-vector"
        service = SessionSummaryService()

        with (
            patch("src.services.llm.get_llm", return_value=MagicMock()),
            patch("src.services.llm.get_session_summary_prompt", return_value=prompt),
            patch("src.services.llm.cached_retry_invoke", side_effect=responses) as invoke,
            patch(
                "src.services.memory._summary_service.get_summary_vectorstore",
                return_value=vectorstore,
            ),
        ):
            first = await service.summarize_session(sid, user_id)
            with get_write_connection() as conn:
                db.add_message(conn, session_id=sid, role="human", content="q3")
                db.add_message(
                    conn,
                    session_id=sid,
                    role="ai",
                    content="a3",
                    sources_json=json.dumps([{"meeting_id": 77}]),
                )
            updated = await service.summarize_session(sid, user_id)

        assert first and first["turn_count"] == 4
        assert updated and updated["summary"] == "updated"
        assert updated["turn_count"] == 6
        assert invoke.call_count == 2
        assert "previous_summary: first" in invoke.call_args.args[1]
        assert vectorstore.upsert.call_args.kwargs["meetings_covered"] == [77]

    @pytest.mark.asyncio
    async def test_vector_failure_preserves_authoritative_sql_summary(self):
        """A transient Chroma failure must not discard the generated summary."""
        from src.services.memory._summary_service import SessionSummaryService

        user_id = "summary_vector_failure_user"
        sid = _create_session_with_messages(
            user_id,
            "Vector failure",
            [("human", "q1"), ("ai", "a1"), ("human", "q2"), ("ai", "a2")],
        )
        response = MagicMock(
            content=json.dumps(
                {
                    "summary": "Durable SQL summary",
                    "topics": ["recovery"],
                    "key_entities": [],
                    "decisions": [],
                }
            )
        )
        prompt = MagicMock()
        prompt.format.side_effect = lambda **kwargs: kwargs["conversation"]
        vectorstore = MagicMock()
        vectorstore.upsert.side_effect = RuntimeError("chroma unavailable")

        with (
            patch("src.services.llm.get_llm", return_value=MagicMock()),
            patch("src.services.llm.get_session_summary_prompt", return_value=prompt),
            patch("src.services.llm.cached_retry_invoke", return_value=response),
            patch(
                "src.services.memory._summary_service.get_summary_vectorstore",
                return_value=vectorstore,
            ),
        ):
            result = await SessionSummaryService().summarize_session(sid, user_id)

        assert result and result["summary"] == "Durable SQL summary"
        with db.get_connection() as conn:
            stored = db.get_session_summary(conn, sid, user_id=user_id)
        assert stored is not None
        assert stored["summary"] == "Durable SQL summary"
        assert stored["embedding_id"] is None

    @pytest.mark.asyncio
    async def test_malformed_summary_output_gets_one_corrective_retry(self):
        from src.services.memory._summary_service import SessionSummaryService

        user_id = "summary_repair_user"
        sid = _create_session_with_messages(
            user_id,
            "Repair malformed output",
            [("human", "q1"), ("ai", "a1"), ("human", "q2"), ("ai", "a2")],
        )
        responses = [
            MagicMock(content="Here is the summary: not-json"),
            MagicMock(
                content=json.dumps(
                    {
                        "summary": "Recovered structured summary",
                        "topics": ["reliability"],
                        "key_entities": [],
                        "decisions": [],
                    }
                )
            ),
        ]
        prompt = MagicMock()
        prompt.format.return_value = "initial summary prompt"
        vectorstore = MagicMock()
        vectorstore.upsert.return_value = "summary-vector"

        with (
            patch("src.services.llm.get_llm", return_value=MagicMock()),
            patch("src.services.llm.get_session_summary_prompt", return_value=prompt),
            patch("src.services.llm.cached_retry_invoke", side_effect=responses) as invoke,
            patch(
                "src.services.memory._summary_service.get_summary_vectorstore",
                return_value=vectorstore,
            ),
        ):
            result = await SessionSummaryService().summarize_session(sid, user_id)

        assert result and result["summary"] == "Recovered structured summary"
        assert invoke.call_count == 2
        assert "previous response was not a valid" in invoke.call_args.args[1]

    @pytest.mark.asyncio
    async def test_late_stale_summary_cannot_overwrite_vector_or_return_stale_data(self):
        """A slower old snapshot must lose in SQLite, Chroma, and the API result."""
        from src.services.memory._summary_service import SessionSummaryService

        user_id = "concurrent_summary_user"
        sid = _create_session_with_messages(
            user_id,
            "Concurrent",
            [("human", "q1"), ("ai", "a1"), ("human", "q2"), ("ai", "a2")],
        )
        old_started = threading.Event()
        release_old = threading.Event()

        def _invoke(_llm, prompt_text):
            if "q3" not in prompt_text:
                old_started.set()
                assert release_old.wait(timeout=5)
                summary = "stale four-message summary"
            else:
                summary = "current six-message summary"
            return MagicMock(
                content=json.dumps(
                    {"summary": summary, "topics": [], "key_entities": [], "decisions": []}
                )
            )

        prompt = MagicMock()
        prompt.format.side_effect = lambda **kwargs: kwargs["conversation"]
        vectorstore = MagicMock()
        vectorstore.upsert.return_value = "summary-vector"
        service = SessionSummaryService()

        with (
            patch("src.services.llm.get_llm", return_value=MagicMock()),
            patch("src.services.llm.get_session_summary_prompt", return_value=prompt),
            patch("src.services.llm.cached_retry_invoke", side_effect=_invoke),
            patch(
                "src.services.memory._summary_service.get_summary_vectorstore",
                return_value=vectorstore,
            ),
        ):
            old_task = asyncio.create_task(service.summarize_session(sid, user_id))
            assert await asyncio.to_thread(old_started.wait, 5)
            with get_write_connection() as conn:
                db.add_message(conn, session_id=sid, role="human", content="q3")
                db.add_message(conn, session_id=sid, role="ai", content="a3")
            current = await service.summarize_session(sid, user_id)
            release_old.set()
            stale_task_result = await old_task

        assert current and current["turn_count"] == 6
        assert stale_task_result and stale_task_result["turn_count"] == 6
        assert stale_task_result["summary"] == "current six-message summary"
        vectorstore.upsert.assert_called_once()
        assert vectorstore.upsert.call_args.args[2] == "current six-message summary"
        with db.get_connection() as conn:
            stored = db.get_session_summary(conn, sid, user_id=user_id)
        assert stored is not None
        assert stored["turn_count"] == 6
        assert stored["summary"] == "current six-message summary"

    def test_missing_session_summary_vector_is_rebuilt_from_sqlite(self):
        from src.services.memory._summary_vectorstore import (
            _summary_embedding_id,
            sync_missing_summary_vectors,
        )

        user_id = "summary_vector_repair_user"
        sid = _create_session_with_messages(
            user_id,
            "Repair",
            [("human", "q"), ("ai", "a")],
        )
        with get_write_connection() as conn:
            conn.execute(
                "UPDATE chat_messages SET sources_json=? WHERE session_id=? AND role='ai'",
                (json.dumps([{"meeting_id": 81, "file_id": 82}]), sid),
            )
            db.upsert_session_summary(
                conn,
                session_id=sid,
                user_id=user_id,
                summary="Authoritative summary",
                topics=json.dumps(["recovery"]),
                turn_count=2,
                embedding_id=None,
            )

        vectorstore = MagicMock()
        vectorstore._chromadb.get.return_value = {"ids": []}
        vectorstore.upsert.return_value = _summary_embedding_id(sid)
        with patch(
            "src.services.memory._summary_vectorstore.get_summary_vectorstore",
            return_value=vectorstore,
        ):
            repaired = sync_missing_summary_vectors()

        assert repaired == 1
        vectorstore.upsert.assert_called_once_with(
            sid,
            user_id,
            "Authoritative summary",
            ["recovery"],
            meetings_covered=[81],
            files_covered=[82],
        )
        with db.get_connection() as conn:
            stored = db.get_session_summary(conn, sid, user_id=user_id)
        assert stored is not None
        assert stored["embedding_id"] == _summary_embedding_id(sid)

    def test_session_summary_vector_sync_skips_healthy_rows(self):
        from src.services.memory._summary_vectorstore import (
            _summary_embedding_id,
            sync_missing_summary_vectors,
        )

        user_id = "healthy_summary_vector_user"
        sid = _create_session_with_messages(
            user_id,
            "Healthy",
            [("human", "q"), ("ai", "a")],
        )
        embedding_id = _summary_embedding_id(sid)
        with get_write_connection() as conn:
            db.upsert_session_summary(
                conn,
                session_id=sid,
                user_id=user_id,
                summary="Already indexed",
                turn_count=2,
                embedding_id=embedding_id,
            )

        vectorstore = MagicMock()
        vectorstore._chromadb.get.return_value = {"ids": [embedding_id]}
        with patch(
            "src.services.memory._summary_vectorstore.get_summary_vectorstore",
            return_value=vectorstore,
        ):
            repaired = sync_missing_summary_vectors()

        assert repaired == 0
        vectorstore.upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_backfill_is_serial_and_yields(self):
        from src.services.memory._summary_service import SessionSummaryService

        user_id = "backfill_serial_user"
        for idx in range(3):
            _create_session_with_messages(
                user_id,
                f"session-{idx}",
                [("human", "q1"), ("ai", "a1"), ("human", "q2"), ("ai", "a2")],
            )

        service = SessionSummaryService()
        active = 0
        max_active = 0
        calls: list[str] = []

        async def _fake_summarize(session_id: str, _uid: str):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            calls.append(session_id)
            await asyncio.to_thread(lambda: None)
            active -= 1
            return {"session_id": session_id}

        with patch.object(service, "summarize_session", new=AsyncMock(side_effect=_fake_summarize)):
            await asyncio.gather(
                service.summarize_unsummarized(user_id=user_id, max_batch=2),
                service.summarize_unsummarized(user_id=user_id, max_batch=2),
            )

        # Both gather calls process sessions; the backfill lock serializes
        # them but max_batch=2 limits each call to 2 sessions (4 total).
        # Semaphore(3) allows up to 3 concurrent summarize_session calls.
        assert len(calls) == 4
        assert max_active <= 3


class TestPipelineContextSessionField:
    def test_session_context_field_exists(self):
        """PipelineContext has the session_context field."""
        from src.services.chain import PipelineContext

        ctx = PipelineContext(question="test")
        assert hasattr(ctx, "session_context")
        assert ctx.session_context == ""

    def test_build_system_context_includes_session_context(self):
        """_build_system_context includes <prior_conversations> section."""
        from src.services.chain import _build_system_context

        result = _build_system_context(
            memory_context="user likes coffee",
            session_context="Discussed project alpha yesterday",
            entity_context="",
            meeting_context="Meeting about budgets",
            web_context="",
        )
        assert "<user_memory>" in result
        assert "<prior_conversations>" in result
        assert "[Meeting Content]" in result
        assert "user likes coffee" in result
        assert "Discussed project alpha yesterday" in result

    def test_build_system_context_omits_empty_session_context(self):
        """_build_system_context omits <prior_conversations> when empty."""
        from src.services.chain import _build_system_context

        result = _build_system_context(
            memory_context="user likes coffee",
            session_context="",
            entity_context="",
            meeting_context="Meeting about budgets",
            web_context="",
        )
        assert "<prior_conversations>" not in result


class TestNewConfigSettings:
    def test_session_summary_settings_exist(self):
        """New config settings for session summaries exist."""
        from src.core.config import settings

        assert hasattr(settings, "SESSION_SUMMARY_ENABLED")
        assert hasattr(settings, "SESSION_SUMMARY_MIN_TURNS")
        assert hasattr(settings, "SESSION_SUMMARY_MAX_ITEMS")
        assert hasattr(settings, "MEMORY_MAX_CONTEXT_ITEMS")

    def test_session_summary_defaults(self):
        """New config settings have reasonable defaults."""
        from src.core.config import settings

        assert settings.SESSION_SUMMARY_ENABLED is True
        assert settings.SESSION_SUMMARY_MIN_TURNS == 4
        assert settings.SESSION_SUMMARY_MAX_ITEMS == 3
        assert settings.MEMORY_MAX_CONTEXT_ITEMS == 6
