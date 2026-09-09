"""MCP is a thin client of the canonical HTTP API."""

import inspect
import json
from unittest.mock import AsyncMock, patch

from src import mcp as mcp_module


def test_mcp_tools_are_registered_and_keep_expected_async_boundaries():
    assert mcp_module.mcp.name == "meeting-agent"
    for name in (
        "list_meetings",
        "search_meetings",
        "ask_about_meetings",
        "manage_memory",
        "list_skills",
        "invoke_skill",
    ):
        assert hasattr(mcp_module, name)
    assert inspect.iscoroutinefunction(mcp_module.ask_about_meetings)
    assert inspect.iscoroutinefunction(mcp_module.invoke_skill)


def test_module_has_no_direct_storage_dependencies():
    source = inspect.getsource(mcp_module)
    assert "database as db" not in source
    assert "services.rag" not in source
    assert "services.memory" not in source
    assert "init_db" not in source


@patch("src.mcp._request")
def test_list_meetings_uses_api(request):
    request.return_value = {
        "meetings": [
            {
                "id": 4,
                "title": "Planning",
                "file_types": ["pdf", "audio"],
                "status": "ready",
                "created_at": "2026-09-03",
            }
        ]
    }
    result = mcp_module.list_meetings(status="ready", limit=5)
    request.assert_called_once_with("GET", "/meetings", params={"status": "ready", "limit": 5})
    assert "[4] Planning | pdf,audio | ready" in result


@patch("src.mcp._request")
def test_search_meetings_uses_retrieve_only_api(request):
    request.return_value = {
        "results": [{"meeting_title": "Roadmap", "content": "Q1 scope", "score": 0.82}]
    }
    result = mcp_module.search_meetings("scope", meeting_ids=[3], top_k=8)
    request.assert_called_once_with(
        "POST",
        "/chat/search",
        json={"question": "scope", "meeting_ids": [3], "top_k": 8},
    )
    assert "Roadmap" in result
    assert "Q1 scope" in result


async def test_ask_about_meetings_uses_api():
    payload = {
        "answer": "The team approved it.",
        "session_id": "s1",
        "sources": [{"meeting_title": "Review"}],
    }
    with patch("src.mcp._arequest", new=AsyncMock(return_value=payload)) as request:
        result = await mcp_module.ask_about_meetings("What happened?", meeting_ids=[2])
    request.assert_awaited_once_with(
        "POST",
        "/chat",
        json={"question": "What happened?", "session_id": None, "meeting_ids": [2]},
    )
    assert json.loads(result)["sources"] == ["Review"]


@patch("src.mcp._request")
def test_memory_crud_uses_api(request):
    request.return_value = {"key": "lang", "value": "Python"}
    assert mcp_module.manage_memory("set", key="lang", value="Python") == (
        "Memory saved: lang = Python"
    )
    request.assert_called_once_with("POST", "/memory", json={"key": "lang", "value": "Python"})


@patch("src.mcp._request")
def test_list_skills_uses_api(request):
    request.return_value = {
        "skills": [
            {
                "name": "weekly",
                "display_name": "Weekly Report",
                "description": "Build a weekly report",
                "examples": ["summarize this week"],
            }
        ]
    }
    assert "Weekly Report" in mcp_module.list_skills()
    request.assert_called_once_with("GET", "/skills")


async def test_invoke_skill_uses_api():
    payload = {
        "skill_name": "weekly",
        "content": "Report",
        "sources": [{"meeting_id": 1}],
    }
    with patch("src.mcp._arequest", new=AsyncMock(return_value=payload)) as request:
        result = await mcp_module.invoke_skill("weekly", "summarize", meeting_ids=[1])
    request.assert_awaited_once()
    assert json.loads(result)["output"] == "Report"


def test_validation_and_api_errors_are_user_readable():
    assert "between 1 and 100" in mcp_module.list_meetings(limit=0)
    assert "between 1 and 100" in mcp_module.search_meetings("x", top_k=101)
    with patch("src.mcp._request", side_effect=RuntimeError("offline")):
        assert "offline" in mcp_module.list_skills()
