"""MCP Server - exposes Meeting Agent capabilities via Model Context Protocol.

Security: This server uses stdio transport (the default for FastMCP), which
trusts the host process.  If HTTP/SSE transport is ever enabled, an API-key
guard MUST be added (see the check in ``__main__.py`` / ``__init__``).
"""

import json
import logging
import os
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .core import database as db
from .core.audit import audit_log
from .core.config import settings
from .core.database import init_db

logger = logging.getLogger(__name__)
_MCP_USER_ID = "default"

_HTTP_TRANSPORT_ENV_VARS = (
    "MCP_HTTP_PORT",
    "MCP_HOST",
    "FASTMCP_HOST",
    "FASTMCP_PORT",
)

mcp = FastMCP(
    "meeting-agent",
    instructions=(
        "A Meeting Agent that can search meeting transcripts, "
        "answer questions about meetings with RAG, and manage user memory. "
        "All meetings are stored locally with their transcripts indexed in a vector database."
    ),
)

# Security: HTTP/SSE transport is unauthenticated at the transport layer
# (no per-request API-key middleware), so we fail-closed here — if any
# HTTP-related env var is set the server *must* have API_KEY configured.
# This prevents accidental exposure of all MCP tools over the network
# without authentication.  The stdio transport is inherently trusted
# because the host process (Claude Desktop, etc.) is the caller.
_http_requested = any(os.getenv(v) for v in _HTTP_TRANSPORT_ENV_VARS)
if _http_requested and not settings.API_KEY.get_secret_value():
    raise RuntimeError(
        "MCP HTTP transport requires API_KEY to be configured. "
        "Set API_KEY in your environment or use stdio transport instead."
    )


@mcp.tool()
def list_meetings(
    status: str | None = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
) -> str:
    """List all uploaded meetings with their processing status.

    Args:
        status: Filter by status (uploading, processing, ready, failed)
        limit: Maximum number of results (1-100)
    """
    if not 1 <= limit <= 100:
        return "Error: limit must be between 1 and 100"
    with db.get_connection() as conn:
        meetings = db.list_meetings(conn, status=status, limit=limit)
    if not meetings:
        return "No meetings found."
    lines = []
    for m in meetings:
        lines.append(
            f"[{m['id']}] {m['title']} | {m['file_type']} | {m['status']} | {m['created_at']}"
        )
    return "\n".join(lines)


@mcp.tool()
def search_meetings(
    query: str,
    meeting_ids: list[int] | None = None,
    top_k: Annotated[int, Field(ge=1, le=100)] = 5,
) -> str:
    """Search meeting content by semantic similarity.

    Args:
        query: Search query text
        meeting_ids: Restrict search to specific meeting IDs
        top_k: Number of results to return (1-100)
    """
    if not 1 <= top_k <= 100:
        return "Error: top_k must be between 1 and 100"
    from .services.rag import retrieve

    results, _qa = retrieve(query, meeting_ids=meeting_ids, top_k=top_k)
    if not results:
        return "No relevant content found."
    lines = []
    for i, r in enumerate(results, 1):
        meta = r.get("metadata", {})
        title = meta.get("title", f"Meeting#{meta.get('meeting_id')}")
        score = r.get("score", 0)
        snippet = r["content"][:300]
        lines.append(f"--- Result {i} (score: {score:.2f}, source: {title}) ---\n{snippet}")
    return "\n\n".join(lines)


@mcp.tool()
async def ask_about_meetings(
    question: str,
    session_id: str | None = None,
    user_id: str = "default",
    meeting_ids: list[int] | None = None,
) -> str:
    """Ask a question about meeting content. Uses RAG with conversation memory.

    Args:
        question: The question to answer
        session_id: Optional session ID for multi-turn conversation
        user_id: User identifier for personalization and memory
        meeting_ids: Restrict to specific meetings
    """
    from .services.chain import ask

    effective_user_id = _MCP_USER_ID
    if user_id != effective_user_id:
        logger.warning(
            "Ignoring MCP user_id override: requested=%s effective=%s",
            user_id,
            effective_user_id,
        )

    result = await ask(
        question=question,
        session_id=session_id,
        user_id=effective_user_id,
        meeting_ids=meeting_ids,
    )
    response = {
        "answer": result.answer,
        "session_id": result.session_id,
        "sources": [s["meeting_title"] for s in result.sources],
    }
    return json.dumps(response, indent=2)


@mcp.tool()
def manage_memory(
    action: Literal["set", "get", "list", "delete", "search", "decay", "merge"],
    key: str | None = None,
    value: str | None = None,
    user_id: str = "default",
    page: Annotated[int, Field(ge=1)] = 1,
    page_size: Annotated[int, Field(ge=1, le=100)] = 50,
) -> str:
    """Manage long-term user memory (preferences, key facts).

    Args:
        action: Operation to perform (set, get, list, delete, search, decay, merge)
        key: Memory key (required for set, get, delete)
        value: Memory value (required for set)
        user_id: User identifier
        page: Page number for list action (1-based)
        page_size: Results per page for list action (1-100)
    """
    from .services.memory import memory_service

    effective_user_id = _MCP_USER_ID
    if user_id != effective_user_id:
        logger.warning(
            "Ignoring MCP user_id override in manage_memory: requested=%s effective=%s",
            user_id,
            effective_user_id,
        )

    if action == "set":
        if not key or not value:
            return "Error: 'key' and 'value' are required for 'set' action"
        memory_service.set(effective_user_id, key, value)
        audit_log("mcp_memory_set", "mcp", f"key={key}")
        return f"Memory saved: {key} = {value}"

    elif action == "get":
        if not key:
            return "Error: 'key' is required for 'get' action"
        val = memory_service.get(effective_user_id, key)
        return val if val else f"Memory not found: {key}"

    elif action == "list":
        # H-16: Paginate to prevent OOM on large memory sets.
        page = max(1, page)
        page_size = max(1, min(100, page_size))
        offset = (page - 1) * page_size
        memories = memory_service.list_all(effective_user_id)
        if not memories:
            return "No memories stored."
        total = len(memories)
        page_memories = memories[offset : offset + page_size]
        lines = [f"--- Page {page} ({len(page_memories)} of {total} total) ---"]
        for m in page_memories:
            lines.append(f"- {m['key']}: {m['value']} ({m['source']})")
        if offset + page_size < total:
            lines.append(f"(use page={page + 1} for next page)")
        return "\n".join(lines)

    elif action == "delete":
        if not key:
            return "Error: 'key' is required for 'delete' action"
        memory_service.delete(effective_user_id, key)
        audit_log("mcp_memory_delete", "mcp", f"key={key}")
        return f"Memory deleted: {key}"

    else:
        return f"Unknown action: {action}. Use one of: set, get, list, delete"


@mcp.tool()
def list_skills() -> str:
    """List all available skills with their descriptions.

    Skills are special capabilities that can be triggered by user queries
    to format or structure the output in specific ways.
    """
    from skills.loader import SkillLoader

    loader = SkillLoader()
    skills = loader.load_all()

    if not skills:
        return "No skills available."

    lines = ["# Available Skills\n"]

    for skill in skills:
        lines.append(f"## {skill.display_name} (`{skill.name}`)")
        lines.append(f"{skill.description}\n")
        lines.append(f"**Examples**: {', '.join(skill.intent_matching.examples[:2])}")
        lines.append(f"**Category**: {skill.metadata.category}\n")

    return "\n".join(lines)


@mcp.tool()
async def invoke_skill(
    skill_name: str,
    query: str,
    user_id: str = "default",
    meeting_ids: list[int] | None = None,
) -> str:
    """Manually invoke a specific skill by name.

    This executes the skill using prompt-integrated generation:
    the skill configuration is passed to the LLM prompt, which then
    generates structured output following the skill's format requirements.

    Args:
        skill_name: Name of the skill to invoke
        query: The user query/context for the skill
        user_id: User identifier
        meeting_ids: Optional list of meeting IDs to restrict search
    """
    from skills.loader import SkillLoader

    from .services.chain import PipelineContext, _extract_sources
    from .services.chain._api import _run_pipeline

    loader = SkillLoader()
    skill = loader.get(skill_name)

    if not skill:
        available = [s.name for s in loader.load_all()]
        return f"Error: Skill '{skill_name}' not found. Available: {', '.join(available)}"

    effective_user_id = _MCP_USER_ID
    if user_id != effective_user_id:
        logger.warning(
            "Ignoring MCP user_id override in invoke_skill: requested=%s effective=%s",
            user_id,
            effective_user_id,
        )

    ctx = PipelineContext(
        question=query,
        user_id=effective_user_id,
        meeting_ids=meeting_ids,
    )

    await _run_pipeline(ctx, skill.model_dump())

    return json.dumps(
        {
            "skill": skill_name,
            "output": ctx.answer,
            "sources": _extract_sources(ctx.docs),
        },
        indent=2,
        ensure_ascii=False,
    )


if __name__ == "__main__":
    init_db()
    mcp.run()
