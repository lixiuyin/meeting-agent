"""Tests for MCP server - Model Context Protocol tools.

Tests cover basic MCP module functionality, each tool's happy path,
error handling, edge cases, and user ID override behavior.
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Set up test environment
os.environ["API_KEY"] = ""
os.environ["DATA_DIR"] = tempfile.mkdtemp()

from src.core import constants as constants_module

constants_module.DATA_DIR = Path(os.environ["DATA_DIR"])
constants_module.DATABASE_PATH = constants_module.DATA_DIR / "test.db"

# Import MCP module
from src import mcp as mcp_module  # noqa: E402

# ---------------------------------------------------------------------------
# Test data helpers
# ---------------------------------------------------------------------------


def _make_meeting(
    mid: int = 1,
    title: str = "Team Standup",
    file_type: str = "video/mp4",
    status: str = "ready",
    created_at: str = "2025-01-15T10:00:00",
) -> dict:
    """Return a dict mimicking a meeting row from list_meetings."""
    return {
        "id": mid,
        "title": title,
        "file_type": file_type,
        "status": status,
        "created_at": created_at,
    }


def _make_retrieval_result(
    content: str = "Discussed Q1 roadmap",
    score: float = 0.92,
    meeting_id: int = 1,
    title: str = "Team Standup",
) -> dict:
    """Return a dict mimicking a retrieval result from retrieve()."""
    return {
        "content": content,
        "score": score,
        "metadata": {"meeting_id": meeting_id, "title": title},
    }


def _make_memory(
    key: str = "preferred_language",
    value: str = "English",
    source: str = "user",
) -> dict:
    """Return a dict mimicking a memory entry from list_all()."""
    return {"key": key, "value": value, "source": source}


# ===========================================================================
# 1. Module exports and configuration
# ===========================================================================


class TestMCPExports:
    """Test MCP module exports."""

    def test_mcp_instance_exists(self):
        """Should have MCP server instance."""
        assert hasattr(mcp_module, "mcp")

    def test_tools_exist(self):
        """Should have tool functions defined."""
        assert hasattr(mcp_module, "list_meetings")
        assert hasattr(mcp_module, "search_meetings")
        assert hasattr(mcp_module, "ask_about_meetings")
        assert hasattr(mcp_module, "manage_memory")
        assert hasattr(mcp_module, "list_skills")
        assert hasattr(mcp_module, "invoke_skill")

    def test_tool_signatures(self):
        """Should have correct function signatures."""
        import inspect

        # list_meetings
        sig = inspect.signature(mcp_module.list_meetings)
        params = list(sig.parameters.keys())
        assert "status" in params
        assert "limit" in params

        # search_meetings
        sig = inspect.signature(mcp_module.search_meetings)
        params = list(sig.parameters.keys())
        assert "query" in params
        assert "meeting_ids" in params
        assert "top_k" in params

        # manage_memory
        sig = inspect.signature(mcp_module.manage_memory)
        params = list(sig.parameters.keys())
        assert "action" in params
        assert "key" in params
        assert "value" in params
        assert "user_id" in params

        # list_skills
        sig = inspect.signature(mcp_module.list_skills)
        params = list(sig.parameters.keys())
        assert params == []

        # invoke_skill
        sig = inspect.signature(mcp_module.invoke_skill)
        params = list(sig.parameters.keys())
        assert "skill_name" in params
        assert "query" in params
        assert "user_id" in params
        assert "meeting_ids" in params

    def test_ask_about_meetings_is_async(self):
        """Should be a coroutine function."""
        import asyncio

        assert asyncio.iscoroutinefunction(mcp_module.ask_about_meetings)

    def test_invoke_skill_is_async(self):
        """Should be a coroutine function."""
        import asyncio

        assert asyncio.iscoroutinefunction(mcp_module.invoke_skill)


class TestMCPInstructions:
    """Test MCP server configuration."""

    def test_mcp_has_instructions(self):
        """Should have instructions configured."""
        assert mcp_module.mcp is not None

    def test_mcp_server_name(self):
        """Should have the correct server name."""
        assert mcp_module.mcp.name == "meeting-agent"


class TestMCPConstants:
    """Test MCP internal constants."""

    def test_mcp_user_id_is_default(self):
        """Should use 'default' as the fixed user ID."""
        assert mcp_module._MCP_USER_ID == "default"

    def test_mcp_user_id_is_string(self):
        """Should be a string, not None."""
        assert isinstance(mcp_module._MCP_USER_ID, str)


# ===========================================================================
# 2. list_meetings
# ===========================================================================


class TestListMeetings:
    """Tests for the list_meetings MCP tool."""

    @patch("src.mcp.db")
    def test_returns_formatted_meetings(self, mock_db):
        """Should return newline-separated meeting summaries."""
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.get_connection.return_value = mock_conn
        mock_db.list_meetings.return_value = [
            _make_meeting(mid=1, title="Standup", file_type="video/mp4", status="ready"),
            _make_meeting(mid=2, title="Retro", file_type="audio/wav", status="processing"),
        ]

        result = mcp_module.list_meetings()

        assert "[1] Standup" in result
        assert "video/mp4" in result
        assert "ready" in result
        assert "[2] Retro" in result
        assert "processing" in result
        mock_db.list_meetings.assert_called_once_with(mock_conn, status=None, limit=20)

    @patch("src.mcp.db")
    def test_returns_empty_message_when_no_meetings(self, mock_db):
        """Should return 'No meetings found.' when database is empty."""
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.get_connection.return_value = mock_conn
        mock_db.list_meetings.return_value = []

        result = mcp_module.list_meetings()

        assert result == "No meetings found."

    @patch("src.mcp.db")
    def test_passes_status_filter(self, mock_db):
        """Should pass status filter to database layer."""
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.get_connection.return_value = mock_conn
        mock_db.list_meetings.return_value = []

        mcp_module.list_meetings(status="ready")

        mock_db.list_meetings.assert_called_once_with(mock_conn, status="ready", limit=20)

    @patch("src.mcp.db")
    def test_passes_custom_limit(self, mock_db):
        """Should pass custom limit to database layer."""
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.get_connection.return_value = mock_conn
        mock_db.list_meetings.return_value = []

        mcp_module.list_meetings(limit=5)

        mock_db.list_meetings.assert_called_once_with(mock_conn, status=None, limit=5)

    @patch("src.mcp.db")
    def test_passes_both_status_and_limit(self, mock_db):
        """Should pass both status and limit to database layer."""
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.get_connection.return_value = mock_conn
        mock_db.list_meetings.return_value = [
            _make_meeting(mid=3, status="failed"),
        ]

        result = mcp_module.list_meetings(status="failed", limit=10)

        mock_db.list_meetings.assert_called_once_with(mock_conn, status="failed", limit=10)
        assert "[3]" in result

    @patch("src.mcp.db")
    def test_single_meeting_format(self, mock_db):
        """Should format a single meeting with all expected fields."""
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.get_connection.return_value = mock_conn
        mock_db.list_meetings.return_value = [
            _make_meeting(
                mid=42,
                title="Planning Session",
                file_type="application/pdf",
                status="ready",
                created_at="2025-03-20T14:30:00",
            ),
        ]

        result = mcp_module.list_meetings()

        assert "[42]" in result
        assert "Planning Session" in result
        assert "application/pdf" in result
        assert "ready" in result
        assert "2025-03-20T14:30:00" in result


# ===========================================================================
# 3. search_meetings
# ===========================================================================


class TestSearchMeetings:
    """Tests for the search_meetings MCP tool."""

    @patch("src.services.rag.retrieve")
    def test_returns_formatted_results(self, mock_retrieve):
        """Should return formatted search results with scores and snippets."""
        mock_retrieve.return_value = (
            [
                _make_retrieval_result(
                    content="Discussed the Q1 roadmap and key milestones",
                    score=0.95,
                    meeting_id=1,
                    title="Q1 Planning",
                ),
            ],
            None,
        )

        result = mcp_module.search_meetings(query="Q1 roadmap")

        assert "Result 1" in result
        assert "0.95" in result
        assert "Q1 Planning" in result
        assert "Discussed the Q1 roadmap" in result
        mock_retrieve.assert_called_once_with("Q1 roadmap", meeting_ids=None, top_k=5)

    @patch("src.services.rag.retrieve")
    def test_returns_empty_message_when_no_results(self, mock_retrieve):
        """Should return 'No relevant content found.' when retrieve returns empty."""
        mock_retrieve.return_value = ([], None)

        result = mcp_module.search_meetings(query="nonexistent topic")

        assert result == "No relevant content found."

    @patch("src.services.rag.retrieve")
    def test_passes_meeting_ids_filter(self, mock_retrieve):
        """Should pass meeting_ids filter to retrieve."""
        mock_retrieve.return_value = ([_make_retrieval_result(meeting_id=5)], None)

        mcp_module.search_meetings(query="test", meeting_ids=[5, 6])

        mock_retrieve.assert_called_once_with("test", meeting_ids=[5, 6], top_k=5)

    @patch("src.services.rag.retrieve")
    def test_passes_custom_top_k(self, mock_retrieve):
        """Should pass custom top_k to retrieve."""
        mock_retrieve.return_value = ([], None)

        mcp_module.search_meetings(query="test", top_k=10)

        mock_retrieve.assert_called_once_with("test", meeting_ids=None, top_k=10)

    @patch("src.services.rag.retrieve")
    def test_truncates_long_content_to_300_chars(self, mock_retrieve):
        """Should truncate content snippets to 300 characters."""
        long_content = "A" * 500
        mock_retrieve.return_value = (
            [{"content": long_content, "score": 0.8, "metadata": {}}],
            None,
        )

        result = mcp_module.search_meetings(query="test")

        # The snippet should be 300 chars, not 500
        assert "A" * 300 in result
        assert "A" * 301 not in result

    @patch("src.services.rag.retrieve")
    def test_multiple_results_separated(self, mock_retrieve):
        """Should separate multiple results with double newlines."""
        mock_retrieve.return_value = (
            [
                _make_retrieval_result(content="Result one", score=0.9, title="Meeting A"),
                _make_retrieval_result(content="Result two", score=0.8, title="Meeting B"),
            ],
            None,
        )

        result = mcp_module.search_meetings(query="test")

        assert "Result 1" in result
        assert "Result 2" in result
        assert "Result one" in result
        assert "Result two" in result
        # Double newline separator between results
        assert "\n\n" in result

    @patch("src.services.rag.retrieve")
    def test_missing_metadata_uses_fallback(self, mock_retrieve):
        """Should use fallback title when metadata is missing."""
        mock_retrieve.return_value = (
            [{"content": "Some content", "score": 0.7, "metadata": {}}],
            None,
        )

        result = mcp_module.search_meetings(query="test")

        assert "Meeting#" in result

    @patch("src.services.rag.retrieve")
    def test_missing_title_uses_meeting_id(self, mock_retrieve):
        """Should use Meeting#<id> when title is missing but meeting_id exists."""
        mock_retrieve.return_value = (
            [{"content": "Some content", "score": 0.7, "metadata": {"meeting_id": 99}}],
            None,
        )

        result = mcp_module.search_meetings(query="test")

        assert "Meeting#99" in result


# ===========================================================================
# 4. ask_about_meetings
# ===========================================================================


class TestAskAboutMeetings:
    """Tests for the ask_about_meetings MCP tool."""

    @pytest.mark.asyncio
    @patch("src.services.chain.ask")
    async def test_returns_json_with_answer_and_sources(self, mock_ask):
        """Should return JSON with answer, session_id, and sources."""
        mock_result = MagicMock()
        mock_result.answer = "The meeting discussed Q1 targets."
        mock_result.session_id = "sess-001"
        mock_result.sources = [
            {"meeting_title": "Q1 Planning"},
            {"meeting_title": "All-Hands"},
        ]
        mock_ask.return_value = mock_result

        result = await mcp_module.ask_about_meetings(question="What was discussed?")

        parsed = json.loads(result)
        assert parsed["answer"] == "The meeting discussed Q1 targets."
        assert parsed["session_id"] == "sess-001"
        assert parsed["sources"] == ["Q1 Planning", "All-Hands"]

    @pytest.mark.asyncio
    @patch("src.services.chain.ask")
    async def test_passes_session_id_to_ask(self, mock_ask):
        """Should forward session_id to the ask function."""
        mock_result = MagicMock()
        mock_result.answer = "Yes"
        mock_result.session_id = "sess-abc"
        mock_result.sources = []
        mock_ask.return_value = mock_result

        await mcp_module.ask_about_meetings(question="test", session_id="sess-abc")

        mock_ask.assert_called_once_with(
            question="test",
            session_id="sess-abc",
            user_id="default",
            meeting_ids=None,
        )

    @pytest.mark.asyncio
    @patch("src.services.chain.ask")
    async def test_passes_meeting_ids_to_ask(self, mock_ask):
        """Should forward meeting_ids to the ask function."""
        mock_result = MagicMock()
        mock_result.answer = "ok"
        mock_result.session_id = "s1"
        mock_result.sources = []
        mock_ask.return_value = mock_result

        await mcp_module.ask_about_meetings(question="test", meeting_ids=[10, 20])

        mock_ask.assert_called_once_with(
            question="test",
            session_id=None,
            user_id="default",
            meeting_ids=[10, 20],
        )

    @pytest.mark.asyncio
    @patch("src.services.chain.ask")
    async def test_ignores_custom_user_id(self, mock_ask):
        """Should ignore user_id overrides and use _MCP_USER_ID."""
        mock_result = MagicMock()
        mock_result.answer = "ok"
        mock_result.session_id = "s1"
        mock_result.sources = []
        mock_ask.return_value = mock_result

        await mcp_module.ask_about_meetings(question="test", user_id="custom-user")

        # Should always pass _MCP_USER_ID, not the custom one
        call_kwargs = mock_ask.call_args
        assert call_kwargs.kwargs["user_id"] == "default"

    @pytest.mark.asyncio
    @patch("src.services.chain.ask")
    async def test_logs_warning_on_user_id_override(self, mock_ask, caplog):
        """Should log a warning when user_id differs from _MCP_USER_ID."""
        mock_result = MagicMock()
        mock_result.answer = "ok"
        mock_result.session_id = "s1"
        mock_result.sources = []
        mock_ask.return_value = mock_result

        import logging

        with caplog.at_level(logging.WARNING, logger="src.mcp"):
            await mcp_module.ask_about_meetings(question="test", user_id="alice")

        assert "Ignoring MCP user_id override" in caplog.text
        assert "alice" in caplog.text

    @pytest.mark.asyncio
    @patch("src.services.chain.ask")
    async def test_no_warning_when_user_id_is_default(self, mock_ask, caplog):
        """Should not log warning when user_id matches _MCP_USER_ID."""
        mock_result = MagicMock()
        mock_result.answer = "ok"
        mock_result.session_id = "s1"
        mock_result.sources = []
        mock_ask.return_value = mock_result

        import logging

        with caplog.at_level(logging.WARNING, logger="src.mcp"):
            await mcp_module.ask_about_meetings(question="test", user_id="default")

        assert "Ignoring MCP user_id override" not in caplog.text

    @pytest.mark.asyncio
    @patch("src.services.chain.ask")
    async def test_empty_sources_list(self, mock_ask):
        """Should handle empty sources list gracefully."""
        mock_result = MagicMock()
        mock_result.answer = "No relevant meetings found."
        mock_result.session_id = "s1"
        mock_result.sources = []
        mock_ask.return_value = mock_result

        result = await mcp_module.ask_about_meetings(question="irrelevant")
        parsed = json.loads(result)

        assert parsed["sources"] == []

    @pytest.mark.asyncio
    @patch("src.services.chain.ask")
    async def test_result_is_valid_json(self, mock_ask):
        """Should always return valid JSON."""
        mock_result = MagicMock()
        mock_result.answer = "Test with special chars: <>&\"'"
        mock_result.session_id = "s1"
        mock_result.sources = []
        mock_ask.return_value = mock_result

        result = await mcp_module.ask_about_meetings(question="test")

        parsed = json.loads(result)
        assert parsed["answer"] == "Test with special chars: <>&\"'"


# ===========================================================================
# 5. manage_memory
# ===========================================================================


class TestManageMemorySet:
    """Tests for manage_memory with action='set'."""

    @patch("src.services.memory.memory_service")
    def test_set_saves_memory(self, mock_svc):
        """Should save a memory and return confirmation."""
        result = mcp_module.manage_memory(action="set", key="lang", value="Python")

        mock_svc.set.assert_called_once_with("default", "lang", "Python")
        assert result == "Memory saved: lang = Python"

    @patch("src.services.memory.memory_service")
    def test_set_requires_key(self, mock_svc):
        """Should return error when key is missing for set action."""
        result = mcp_module.manage_memory(action="set", key=None, value="val")

        assert "Error" in result
        assert "'key'" in result
        mock_svc.set.assert_not_called()

    @patch("src.services.memory.memory_service")
    def test_set_requires_value(self, mock_svc):
        """Should return error when value is missing for set action."""
        result = mcp_module.manage_memory(action="set", key="k", value=None)

        assert "Error" in result
        assert "'value'" in result
        mock_svc.set.assert_not_called()

    @patch("src.services.memory.memory_service")
    def test_set_with_empty_string_key_fails(self, mock_svc):
        """Should treat empty string as missing key."""
        result = mcp_module.manage_memory(action="set", key="", value="val")

        assert "Error" in result
        mock_svc.set.assert_not_called()

    @patch("src.services.memory.memory_service")
    def test_set_with_empty_string_value_fails(self, mock_svc):
        """Should treat empty string as missing value."""
        result = mcp_module.manage_memory(action="set", key="k", value="")

        assert "Error" in result
        mock_svc.set.assert_not_called()


class TestManageMemoryGet:
    """Tests for manage_memory with action='get'."""

    @patch("src.services.memory.memory_service")
    def test_get_returns_value(self, mock_svc):
        """Should return the stored value."""
        mock_svc.get.return_value = "Python"

        result = mcp_module.manage_memory(action="get", key="lang")

        mock_svc.get.assert_called_once_with("default", "lang")
        assert result == "Python"

    @patch("src.services.memory.memory_service")
    def test_get_returns_not_found(self, mock_svc):
        """Should return 'not found' message for missing key."""
        mock_svc.get.return_value = None

        result = mcp_module.manage_memory(action="get", key="nonexistent")

        assert "Memory not found" in result
        assert "nonexistent" in result

    @patch("src.services.memory.memory_service")
    def test_get_requires_key(self, mock_svc):
        """Should return error when key is missing for get action."""
        result = mcp_module.manage_memory(action="get", key=None)

        assert "Error" in result
        assert "'key'" in result
        mock_svc.get.assert_not_called()

    @patch("src.services.memory.memory_service")
    def test_get_with_empty_key_fails(self, mock_svc):
        """Should treat empty string as missing key for get."""
        result = mcp_module.manage_memory(action="get", key="")

        assert "Error" in result
        mock_svc.get.assert_not_called()


class TestManageMemoryList:
    """Tests for manage_memory with action='list'."""

    @patch("src.services.memory.memory_service")
    def test_list_returns_formatted_memories(self, mock_svc):
        """Should return formatted list of memories."""
        mock_svc.list_all.return_value = [
            _make_memory(key="lang", value="Python", source="user"),
            _make_memory(key="tz", value="UTC", source="system"),
        ]

        result = mcp_module.manage_memory(action="list")

        assert "- lang: Python (user)" in result
        assert "- tz: UTC (system)" in result
        mock_svc.list_all.assert_called_once_with("default")

    @patch("src.services.memory.memory_service")
    def test_list_returns_empty_message(self, mock_svc):
        """Should return 'No memories stored.' when none exist."""
        mock_svc.list_all.return_value = []

        result = mcp_module.manage_memory(action="list")

        assert result == "No memories stored."


class TestManageMemoryDelete:
    """Tests for manage_memory with action='delete'."""

    @patch("src.services.memory.memory_service")
    def test_delete_removes_memory(self, mock_svc):
        """Should delete a memory and return confirmation."""
        result = mcp_module.manage_memory(action="delete", key="lang")

        mock_svc.delete.assert_called_once_with("default", "lang")
        assert result == "Memory deleted: lang"

    @patch("src.services.memory.memory_service")
    def test_delete_requires_key(self, mock_svc):
        """Should return error when key is missing for delete action."""
        result = mcp_module.manage_memory(action="delete", key=None)

        assert "Error" in result
        assert "'key'" in result
        mock_svc.delete.assert_not_called()

    @patch("src.services.memory.memory_service")
    def test_delete_with_empty_key_fails(self, mock_svc):
        """Should treat empty string as missing key for delete."""
        result = mcp_module.manage_memory(action="delete", key="")

        assert "Error" in result
        mock_svc.delete.assert_not_called()


class TestManageMemoryGeneral:
    """Tests for manage_memory general behavior."""

    @patch("src.services.memory.memory_service")
    def test_unknown_action_returns_error(self, mock_svc):
        """Should return error for unknown action."""
        result = mcp_module.manage_memory(action="unknown_action")

        assert "Unknown action" in result
        assert "unknown_action" in result
        assert "set, get, list, delete" in result

    @patch("src.services.memory.memory_service")
    def test_ignores_custom_user_id(self, mock_svc):
        """Should ignore user_id override and use _MCP_USER_ID."""
        mock_svc.list_all.return_value = []

        mcp_module.manage_memory(action="list", user_id="custom-user")

        mock_svc.list_all.assert_called_once_with("default")

    @patch("src.services.memory.memory_service")
    def test_logs_warning_on_user_id_override(self, mock_svc, caplog):
        """Should log warning when user_id differs from _MCP_USER_ID."""
        import logging

        mock_svc.list_all.return_value = []

        with caplog.at_level(logging.WARNING, logger="src.mcp"):
            mcp_module.manage_memory(action="list", user_id="custom-user")

        assert "Ignoring MCP user_id override in manage_memory" in caplog.text

    @patch("src.services.memory.memory_service")
    def test_no_warning_when_user_id_is_default(self, mock_svc, caplog):
        """Should not warn when user_id matches default."""
        import logging

        mock_svc.list_all.return_value = []

        with caplog.at_level(logging.WARNING, logger="src.mcp"):
            mcp_module.manage_memory(action="list", user_id="default")

        assert "Ignoring" not in caplog.text

    @patch("src.services.memory.memory_service")
    def test_user_id_override_affects_set_too(self, mock_svc):
        """Should ignore custom user_id for set action as well."""
        result = mcp_module.manage_memory(action="set", key="k", value="v", user_id="alice")

        mock_svc.set.assert_called_once_with("default", "k", "v")
        assert "Memory saved" in result


# ===========================================================================
# 6. list_skills
# ===========================================================================


class TestListSkills:
    """Tests for the list_skills MCP tool."""

    @patch("skills.loader.SkillLoader")
    def test_returns_formatted_skills(self, mock_loader_cls):
        """Should return formatted skill list with names and descriptions."""
        mock_loader = MagicMock()
        mock_loader_cls.return_value = mock_loader

        mock_skill = MagicMock()
        mock_skill.display_name = "Meeting Summary"
        mock_skill.name = "meeting_summary"
        mock_skill.description = "Generate a summary of meeting content."
        mock_skill.intent_matching.examples = [
            "summarize this meeting",
            "give me a recap",
        ]
        mock_skill.metadata.category = "analysis"
        mock_loader.load_all.return_value = [mock_skill]

        result = mcp_module.list_skills()

        assert "Meeting Summary" in result
        assert "meeting_summary" in result
        assert "Generate a summary" in result
        assert "summarize this meeting" in result
        assert "analysis" in result

    @patch("skills.loader.SkillLoader")
    def test_returns_no_skills_message(self, mock_loader_cls):
        """Should return 'No skills available.' when none are loaded."""
        mock_loader = MagicMock()
        mock_loader_cls.return_value = mock_loader
        mock_loader.load_all.return_value = []

        result = mcp_module.list_skills()

        assert result == "No skills available."

    @patch("skills.loader.SkillLoader")
    def test_limits_examples_to_two(self, mock_loader_cls):
        """Should show at most 2 examples per skill."""
        mock_loader = MagicMock()
        mock_loader_cls.return_value = mock_loader

        mock_skill = MagicMock()
        mock_skill.display_name = "Test"
        mock_skill.name = "test"
        mock_skill.description = "desc"
        mock_skill.intent_matching.examples = [
            "ex1",
            "ex2",
            "ex3",
            "ex4",
        ]
        mock_skill.metadata.category = "test"
        mock_loader.load_all.return_value = [mock_skill]

        result = mcp_module.list_skills()

        assert "ex1" in result
        assert "ex2" in result
        assert "ex3" not in result
        assert "ex4" not in result

    @patch("skills.loader.SkillLoader")
    def test_multiple_skills_listed(self, mock_loader_cls):
        """Should list multiple skills."""
        mock_loader = MagicMock()
        mock_loader_cls.return_value = mock_loader

        skills = []
        for i in range(3):
            s = MagicMock()
            s.display_name = f"Skill {i}"
            s.name = f"skill_{i}"
            s.description = f"Description {i}"
            s.intent_matching.examples = [f"example {i}"]
            s.metadata.category = "cat"
            skills.append(s)
        mock_loader.load_all.return_value = skills

        result = mcp_module.list_skills()

        for i in range(3):
            assert f"Skill {i}" in result
            assert f"skill_{i}" in result

    @patch("skills.loader.SkillLoader")
    def test_includes_header(self, mock_loader_cls):
        """Should include 'Available Skills' header."""
        mock_loader = MagicMock()
        mock_loader_cls.return_value = mock_loader

        mock_skill = MagicMock()
        mock_skill.display_name = "Test"
        mock_skill.name = "test"
        mock_skill.description = "desc"
        mock_skill.intent_matching.examples = ["ex1"]
        mock_skill.metadata.category = "cat"
        mock_loader.load_all.return_value = [mock_skill]

        result = mcp_module.list_skills()

        assert "# Available Skills" in result


# ===========================================================================
# 7. invoke_skill
# ===========================================================================


class TestInvokeSkill:
    """Tests for the invoke_skill MCP tool."""

    @pytest.mark.asyncio
    @patch("src.services.chain._extract_sources", return_value=[{"meeting_title": "M1"}])
    @patch("src.services.chain._api._run_pipeline", new_callable=AsyncMock)
    @patch("skills.loader.SkillLoader")
    async def test_returns_json_with_skill_output(
        self, mock_loader_cls, mock_run_pipeline, mock_extract
    ):
        """Should return JSON with skill name, output, and sources."""
        mock_loader = MagicMock()
        mock_loader_cls.return_value = mock_loader

        mock_skill = MagicMock()
        mock_skill.name = "meeting_summary"
        mock_skill.model_dump.return_value = {"name": "meeting_summary"}
        mock_loader.get.return_value = mock_skill

        async def fake_pipeline(ctx, skill_def):
            ctx.answer = "Generated summary"

        mock_run_pipeline.side_effect = fake_pipeline

        result = await mcp_module.invoke_skill(
            skill_name="meeting_summary", query="summarize the meeting"
        )

        parsed = json.loads(result)
        assert parsed["skill"] == "meeting_summary"
        assert parsed["output"] == "Generated summary"
        assert parsed["sources"] == [{"meeting_title": "M1"}]

    @pytest.mark.asyncio
    @patch("skills.loader.SkillLoader")
    async def test_returns_error_for_unknown_skill(self, mock_loader_cls):
        """Should return error message when skill is not found."""
        mock_loader = MagicMock()
        mock_loader_cls.return_value = mock_loader
        mock_loader.get.return_value = None

        available_skill = MagicMock()
        available_skill.name = "summarize"
        mock_loader.load_all.return_value = [available_skill]

        result = await mcp_module.invoke_skill(skill_name="nonexistent", query="test")

        assert "Error" in result
        assert "nonexistent" in result
        assert "summarize" in result

    @pytest.mark.asyncio
    @patch("skills.loader.SkillLoader")
    async def test_lists_available_skills_on_not_found(self, mock_loader_cls):
        """Should list available skills in the error message."""
        mock_loader = MagicMock()
        mock_loader_cls.return_value = mock_loader
        mock_loader.get.return_value = None

        skills = []
        for name in ["a", "b", "c"]:
            s = MagicMock()
            s.name = name
            skills.append(s)
        mock_loader.load_all.return_value = skills

        result = await mcp_module.invoke_skill(skill_name="missing", query="test")

        assert "a" in result
        assert "b" in result
        assert "c" in result

    @pytest.mark.asyncio
    @patch("src.services.chain._extract_sources", return_value=[])
    @patch("src.services.chain._api._run_pipeline", new_callable=AsyncMock)
    @patch("skills.loader.SkillLoader")
    async def test_ignores_custom_user_id(self, mock_loader_cls, mock_run_pipeline, mock_extract):
        """Should ignore user_id override and use _MCP_USER_ID."""
        mock_loader = MagicMock()
        mock_loader_cls.return_value = mock_loader

        mock_skill = MagicMock()
        mock_skill.name = "test_skill"
        mock_skill.model_dump.return_value = {"name": "test_skill"}
        mock_loader.get.return_value = mock_skill

        # Capture the PipelineContext passed to _run_pipeline
        captured_ctx = None

        async def capture_pipeline(ctx, skill_def):
            nonlocal captured_ctx
            captured_ctx = ctx
            ctx.answer = "done"

        mock_run_pipeline.side_effect = capture_pipeline

        await mcp_module.invoke_skill(
            skill_name="test_skill",
            query="test",
            user_id="custom-user",
        )

        assert captured_ctx is not None
        assert captured_ctx.user_id == "default"

    @pytest.mark.asyncio
    @patch("src.services.chain._extract_sources", return_value=[])
    @patch("src.services.chain._api._run_pipeline", new_callable=AsyncMock)
    @patch("skills.loader.SkillLoader")
    async def test_logs_warning_on_user_id_override(
        self, mock_loader_cls, mock_run_pipeline, mock_extract, caplog
    ):
        """Should log warning when user_id differs from _MCP_USER_ID."""
        import logging

        mock_loader = MagicMock()
        mock_loader_cls.return_value = mock_loader

        mock_skill = MagicMock()
        mock_skill.name = "test_skill"
        mock_skill.model_dump.return_value = {"name": "test_skill"}
        mock_loader.get.return_value = mock_skill

        async def noop_pipeline(ctx, skill_def):
            ctx.answer = "ok"

        mock_run_pipeline.side_effect = noop_pipeline

        with caplog.at_level(logging.WARNING, logger="src.mcp"):
            await mcp_module.invoke_skill(
                skill_name="test_skill",
                query="test",
                user_id="alice",
            )

        assert "Ignoring MCP user_id override in invoke_skill" in caplog.text

    @pytest.mark.asyncio
    @patch("src.services.chain._extract_sources", return_value=[])
    @patch("src.services.chain._api._run_pipeline", new_callable=AsyncMock)
    @patch("skills.loader.SkillLoader")
    async def test_passes_meeting_ids_to_context(
        self, mock_loader_cls, mock_run_pipeline, mock_extract
    ):
        """Should pass meeting_ids to the PipelineContext."""
        mock_loader = MagicMock()
        mock_loader_cls.return_value = mock_loader

        mock_skill = MagicMock()
        mock_skill.name = "test_skill"
        mock_skill.model_dump.return_value = {"name": "test_skill"}
        mock_loader.get.return_value = mock_skill

        captured_ctx = None

        async def capture_pipeline(ctx, skill_def):
            nonlocal captured_ctx
            captured_ctx = ctx
            ctx.answer = "done"

        mock_run_pipeline.side_effect = capture_pipeline

        await mcp_module.invoke_skill(
            skill_name="test_skill",
            query="test",
            meeting_ids=[1, 2, 3],
        )

        assert captured_ctx.meeting_ids == [1, 2, 3]

    @pytest.mark.asyncio
    @patch("src.services.chain._extract_sources", return_value=[])
    @patch("src.services.chain._api._run_pipeline", new_callable=AsyncMock)
    @patch("skills.loader.SkillLoader")
    async def test_result_is_valid_json(self, mock_loader_cls, mock_run_pipeline, mock_extract):
        """Should always return valid JSON."""
        mock_loader = MagicMock()
        mock_loader_cls.return_value = mock_loader

        mock_skill = MagicMock()
        mock_skill.name = "test_skill"
        mock_skill.model_dump.return_value = {"name": "test_skill"}
        mock_loader.get.return_value = mock_skill

        async def noop_pipeline(ctx, skill_def):
            ctx.answer = "Result with special chars: <>&\"'"

        mock_run_pipeline.side_effect = noop_pipeline

        result = await mcp_module.invoke_skill(skill_name="test_skill", query="test")

        parsed = json.loads(result)
        assert parsed["output"] == "Result with special chars: <>&\"'"

    @pytest.mark.asyncio
    @patch("src.services.chain._extract_sources", return_value=[])
    @patch("src.services.chain._api._run_pipeline", new_callable=AsyncMock)
    @patch("skills.loader.SkillLoader")
    async def test_passes_skill_definition_to_pipeline(
        self, mock_loader_cls, mock_run_pipeline, mock_extract
    ):
        """Should pass skill model_dump() to _run_pipeline."""
        mock_loader = MagicMock()
        mock_loader_cls.return_value = mock_loader

        mock_skill = MagicMock()
        mock_skill.name = "test_skill"
        mock_skill.model_dump.return_value = {"name": "test_skill", "prompt": "x"}
        mock_loader.get.return_value = mock_skill

        async def noop_pipeline(ctx, skill_def):
            ctx.answer = "ok"

        mock_run_pipeline.side_effect = noop_pipeline

        await mcp_module.invoke_skill(skill_name="test_skill", query="test")

        mock_run_pipeline.assert_called_once()
        call_args = mock_run_pipeline.call_args
        assert call_args.args[1] == {"name": "test_skill", "prompt": "x"}

    @pytest.mark.asyncio
    @patch("src.services.chain._extract_sources", return_value=[])
    @patch("src.services.chain._api._run_pipeline", new_callable=AsyncMock)
    @patch("skills.loader.SkillLoader")
    async def test_no_meeting_ids_defaults_to_none(
        self, mock_loader_cls, mock_run_pipeline, mock_extract
    ):
        """Should pass None for meeting_ids when not specified."""
        mock_loader = MagicMock()
        mock_loader_cls.return_value = mock_loader

        mock_skill = MagicMock()
        mock_skill.name = "test_skill"
        mock_skill.model_dump.return_value = {"name": "test_skill"}
        mock_loader.get.return_value = mock_skill

        captured_ctx = None

        async def capture_pipeline(ctx, skill_def):
            nonlocal captured_ctx
            captured_ctx = ctx
            ctx.answer = "done"

        mock_run_pipeline.side_effect = capture_pipeline

        await mcp_module.invoke_skill(skill_name="test_skill", query="test")

        assert captured_ctx.meeting_ids is None
