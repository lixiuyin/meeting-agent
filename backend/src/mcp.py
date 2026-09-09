"""Thin MCP adapter for the Meeting Agent HTTP API.

The API process owns persistence, migrations, authorization, retrieval and
generation. MCP intentionally contains no direct database or vector-store
access, so desktop clients observe the same behavior as the web UI.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Annotated, Any, Literal

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .core.config import settings

logger = logging.getLogger(__name__)
_MCP_USER_ID = "default"


def _resolve_transport() -> Literal["stdio", "sse", "streamable-http"]:
    explicit = os.getenv("MCP_TRANSPORT")
    raw = (explicit if explicit is not None else settings.MCP_TRANSPORT).strip().lower()
    if explicit is None and os.getenv("MCP_HTTP_PORT"):
        raw = "streamable-http"
    aliases = {"http": "streamable-http", "streamable_http": "streamable-http"}
    raw = aliases.get(raw, raw)
    if not raw:
        return "stdio"
    if raw not in {"stdio", "sse", "streamable-http"}:
        raise RuntimeError("MCP_TRANSPORT must be stdio, sse, or streamable-http")
    return raw  # type: ignore[return-value]


def _resolve_http_port() -> int:
    raw = os.getenv("MCP_HTTP_PORT") or os.getenv("FASTMCP_PORT") or str(settings.MCP_HTTP_PORT)
    try:
        port = int(raw)
    except ValueError as exc:
        raise RuntimeError("MCP_HTTP_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError("MCP_HTTP_PORT must be between 1 and 65535")
    return port


_MCP_TRANSPORT = _resolve_transport()
_MCP_HOST = os.getenv("MCP_HOST") or os.getenv("FASTMCP_HOST") or settings.MCP_HOST
_MCP_PORT = _resolve_http_port()


def _validate_http_binding(
    transport: Literal["stdio", "sse", "streamable-http"], host: str
) -> None:
    if transport != "stdio" and host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError(
            "MCP HTTP/SSE must bind to loopback because FastMCP has no inbound "
            "authentication configured. Put an authenticated same-host reverse proxy "
            "in front of 127.0.0.1 instead of exposing the port directly."
        )


_validate_http_binding(_MCP_TRANSPORT, _MCP_HOST)

mcp = FastMCP(
    "meeting-agent",
    instructions=(
        "A Meeting Agent API client that can search meeting transcripts, answer "
        "questions, and manage the authenticated user's memory."
    ),
    host=_MCP_HOST,
    port=_MCP_PORT,
)

if _MCP_TRANSPORT != "stdio" and not (
    settings.MCP_API_KEY.get_secret_value() or settings.API_KEY.get_secret_value()
):
    raise RuntimeError(
        "MCP HTTP/SSE requires MCP_API_KEY or API_KEY for downstream API calls. "
        "Use trusted stdio transport for an unauthenticated development backend."
    )


def _base_url() -> str:
    return settings.MCP_API_URL.rstrip("/")


def _headers() -> dict[str, str]:
    api_key = settings.MCP_API_KEY.get_secret_value() or settings.API_KEY.get_secret_value()
    return {"X-API-Key": api_key} if api_key else {}


def _error_text(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            detail = exc.response.json().get("detail")
        except (ValueError, AttributeError):
            detail = exc.response.text[:500]
        return f"API error {exc.response.status_code}: {detail}"
    return f"API request failed: {type(exc).__name__}: {exc}"


def _request(method: str, path: str, **kwargs: Any) -> Any:
    with httpx.Client(base_url=_base_url(), headers=_headers(), timeout=120.0) as client:
        response = client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()


async def _arequest(method: str, path: str, **kwargs: Any) -> Any:
    async with httpx.AsyncClient(base_url=_base_url(), headers=_headers(), timeout=120.0) as client:
        response = await client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()


@mcp.tool()
def list_meetings(
    status: str | None = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
) -> str:
    """List uploaded meetings through the authenticated HTTP API."""
    if not 1 <= limit <= 100:
        return "Error: limit must be between 1 and 100"
    try:
        payload = _request("GET", "/meetings", params={"status": status, "limit": limit})
    except Exception as exc:
        return _error_text(exc)
    meetings = payload.get("meetings", [])
    if not meetings:
        return "No meetings found."
    lines = []
    for meeting in meetings:
        file_types = meeting.get("file_types") or []
        file_type = meeting.get("file_type") or ",".join(file_types) or "unknown"
        lines.append(
            f"[{meeting['id']}] {meeting['title']} | {file_type} | "
            f"{meeting['status']} | {meeting['created_at']}"
        )
    return "\n".join(lines)


@mcp.tool()
def search_meetings(
    query: str,
    meeting_ids: list[int] | None = None,
    top_k: Annotated[int, Field(ge=1, le=100)] = 5,
) -> str:
    """Search meeting content without invoking the answer model."""
    if not 1 <= top_k <= 100:
        return "Error: top_k must be between 1 and 100"
    try:
        payload = _request(
            "POST",
            "/chat/search",
            json={"question": query, "meeting_ids": meeting_ids, "top_k": top_k},
        )
    except Exception as exc:
        return _error_text(exc)
    results = payload.get("results", [])
    if not results:
        return "No relevant content found."
    return "\n\n".join(
        f"--- Result {index} (score: {float(item.get('score', 0)):.2f}, "
        f"source: {item.get('meeting_title') or 'Unknown'}) ---\n"
        f"{str(item.get('content') or '')[:300]}"
        for index, item in enumerate(results, 1)
    )


@mcp.tool()
async def ask_about_meetings(
    question: str,
    session_id: str | None = None,
    user_id: str = "default",
    meeting_ids: list[int] | None = None,
) -> str:
    """Ask a RAG question through the same API used by the frontend."""
    if user_id != _MCP_USER_ID:
        logger.warning("Ignoring MCP user_id override; API credentials determine the principal")
    try:
        payload = await _arequest(
            "POST",
            "/chat",
            json={
                "question": question,
                "session_id": session_id,
                "meeting_ids": meeting_ids,
            },
        )
    except Exception as exc:
        return _error_text(exc)
    sources = [source.get("meeting_title", "") for source in payload.get("sources", [])]
    return json.dumps(
        {
            "answer": payload.get("answer", ""),
            "session_id": payload.get("session_id"),
            "sources": sources,
        },
        indent=2,
        ensure_ascii=False,
    )


@mcp.tool()
def manage_memory(
    action: Literal["set", "get", "list", "delete", "search", "decay", "merge"],
    key: str | None = None,
    value: str | None = None,
    user_id: str = "default",
    page: Annotated[int, Field(ge=1)] = 1,
    page_size: Annotated[int, Field(ge=1, le=100)] = 50,
) -> str:
    """Manage the authenticated principal's long-term memory through the API."""
    if user_id != _MCP_USER_ID:
        logger.warning("Ignoring MCP user_id override; API credentials determine the principal")
    page = max(1, page)
    page_size = max(1, min(100, page_size))
    try:
        if action == "set":
            if not key or not value:
                return "Error: 'key' and 'value' are required for 'set' action"
            item = _request("POST", "/memory", json={"key": key, "value": value})
            return f"Memory saved: {item['key']} = {item['value']}"
        if action in {"get", "list"}:
            if action == "get" and not key:
                return "Error: 'key' is required for 'get' action"
            payload = _request(
                "GET",
                "/memory",
                params={"limit": page_size, "offset": (page - 1) * page_size},
            )
            memories = payload.get("items") or payload.get("memories") or []
            if action == "get":
                match = next((item for item in memories if item.get("key") == key), None)
                return str(match["value"]) if match else f"Memory not found: {key}"
            if not memories:
                return "No memories stored."
            total = int(payload.get("total", len(memories)))
            lines = [f"--- Page {page} ({len(memories)} of {total} total) ---"]
            lines.extend(
                f"- {item['key']}: {item['value']} ({item.get('source', 'unknown')})"
                for item in memories
            )
            if page * page_size < total:
                lines.append(f"(use page={page + 1} for next page)")
            return "\n".join(lines)
        if action == "delete":
            if not key:
                return "Error: 'key' is required for 'delete' action"
            _request("DELETE", "/memory", params={"key": key})
            return f"Memory deleted: {key}"
        if action == "search":
            query = value or key
            if not query:
                return "Error: 'key' or 'value' is required for 'search' action"
            payload = _request(
                "POST",
                "/memory/search",
                json={"query": query, "limit": page_size, "min_importance": 1},
            )
            memories = payload.get("memories", [])
            return "\n".join(f"- {item['key']}: {item['value']}" for item in memories) or (
                "No memories found."
            )
        if action == "decay":
            payload = _request("POST", "/memory/decay")
            return f"Decayed {payload.get('decayed_count', 0)} memories."
        if action == "merge":
            if not key or not value:
                return (
                    "Error: comma-separated source names in 'key' and target in 'value' "
                    "are required"
                )
            _request(
                "POST",
                "/memory/entities/merge",
                json={
                    "source_names": [part.strip() for part in key.split(",")],
                    "target_name": value,
                },
            )
            return f"Entities merged into: {value}"
    except Exception as exc:
        return _error_text(exc)
    return f"Unknown action: {action}"


@mcp.tool()
def list_skills() -> str:
    """List available skills through the API."""
    try:
        payload = _request("GET", "/skills")
    except Exception as exc:
        return _error_text(exc)
    skills = payload.get("skills", [])
    if not skills:
        return "No skills available."
    lines = ["# Available Skills\n"]
    for skill in skills:
        lines.append(f"## {skill['display_name']} (`{skill['name']}`)")
        lines.append(f"{skill['description']}\n")
        lines.append(f"**Examples**: {', '.join(skill.get('examples', [])[:2])}\n")
    return "\n".join(lines)


@mcp.tool()
async def invoke_skill(
    skill_name: str,
    query: str,
    user_id: str = "default",
    meeting_ids: list[int] | None = None,
) -> str:
    """Invoke a named skill through the authenticated API."""
    if user_id != _MCP_USER_ID:
        logger.warning("Ignoring MCP user_id override; API credentials determine the principal")
    try:
        payload = await _arequest(
            "POST",
            "/skills/invoke",
            json={
                "skill_name": skill_name,
                "query": query,
                "user_id": _MCP_USER_ID,
                "meeting_ids": meeting_ids,
            },
        )
    except Exception as exc:
        return _error_text(exc)
    return json.dumps(
        {
            "skill": payload.get("skill_name", skill_name),
            "output": payload.get("content", ""),
            "sources": payload.get("sources", []),
        },
        indent=2,
        ensure_ascii=False,
    )


if __name__ == "__main__":
    mcp.run(transport=_MCP_TRANSPORT)
