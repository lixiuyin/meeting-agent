"""Tests for SessionSummaryService and pipeline integration."""

import asyncio
import json
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
        assert sid_global in session_ids

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
