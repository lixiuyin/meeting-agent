import asyncio
import json
import re
import sqlite3

from fastapi import Depends, HTTPException, Query, Request

from ....api.middleware import limiter
from ....core import database as db
from ....core.database.bm25 import fts5_search
from ....core.security import verify_api_key
from ....models.schemas import FileType, MeetingSearchResponse
from ._common import _ownership_filter, logger, router

_MAX_QUERY_LENGTH = 200
_SAFE_QUERY_RE = re.compile(r"^[\w\s\-\.,:;!?@#%&*()+/\"'`~\[\]\u4e00-\u9fff]+$", re.UNICODE)


@router.get("/search/content", response_model=MeetingSearchResponse)
@limiter.limit("60/minute")
async def search_meetings(
    request: Request,
    q: str = Query(..., min_length=1, description="Search query"),
    limit: int = Query(10, ge=1, le=50),
    principal: dict = Depends(verify_api_key),
):
    """Search within meeting content using FTS5 full-text search with BM25 ranking"""
    _ = request
    user_id = _ownership_filter(principal)
    query = q.strip()
    if len(query) > _MAX_QUERY_LENGTH:
        raise HTTPException(400, f"Query too long (max {_MAX_QUERY_LENGTH} characters)")
    if not _SAFE_QUERY_RE.fullmatch(query):
        raise HTTPException(400, "Query contains unsupported characters")
    try:

        def _search():
            with db.get_connection() as conn:
                # Scope search to user's meetings when user_id is set
                meeting_ids: list[int] | None = None
                if user_id is not None:
                    rows = conn.execute(
                        "SELECT id FROM meetings WHERE user_id=?", (user_id,)
                    ).fetchall()
                    meeting_ids = [r["id"] for r in rows]
                return fts5_search(conn, query, meeting_ids=meeting_ids, limit=limit)

        rows = await asyncio.to_thread(_search)

        search_results = []
        for row in rows:
            snippet = _extract_snippet(row.get("content", ""), q)
            file_type = _resolve_file_type(row)
            search_results.append(
                {
                    "meeting_id": row["meeting_id"],
                    "title": "",  # FTS5 results don't carry meeting title
                    "file_type": file_type,
                    "snippet": snippet,
                    "score": row.get("rank", 0.0),
                }
            )

        return MeetingSearchResponse(
            query=query,
            total=len(search_results),
            results=search_results,
        )
    except Exception as e:
        logger.error("Search failed: %s", e, exc_info=True)
        raise HTTPException(500, "Search failed") from e


def _resolve_file_type(row: dict) -> FileType:
    """Resolve file type from BM25 metadata or fall back to database lookup."""
    metadata = row.get("metadata")
    if metadata:
        try:
            parsed = json.loads(metadata)
            ft = parsed.get("file_type")
            if ft:
                return FileType(ft)
        except (json.JSONDecodeError, KeyError, ValueError):
            logger.warning(
                "Failed to parse file_type from metadata for meeting %s",
                row.get("meeting_id"),
                exc_info=True,
            )
    # Fallback: lookup from meeting files
    try:
        with db.get_connection() as conn:
            files = db.list_meeting_files(conn, row["meeting_id"])
        if files:
            return FileType(files[0]["file_type"])
    except (sqlite3.Error, KeyError, ValueError):
        logger.warning(
            "Failed to resolve file_type from meeting_files for meeting %s",
            row.get("meeting_id"),
            exc_info=True,
        )
    return FileType.PDF


def _extract_snippet(text: str | None, query: str, context: int = 100) -> str:
    """Extract a snippet of text around the query match"""
    if not text:
        return ""

    text_lower = text.lower()
    query_lower = query.lower()

    # Find the match position
    pos = text_lower.find(query_lower)
    if pos == -1:
        # Return first part of text if no exact match
        return text[:200] + "..." if len(text) > 200 else text

    # Calculate snippet boundaries
    start = max(0, pos - context)
    end = min(len(text), pos + len(query) + context)

    snippet = text[start:end]
    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet = snippet + "..."

    return snippet
