"""BM25 index persistence and FTS5 search operations."""

import logging
import sqlite3
from json import JSONDecodeError, dumps, loads
from typing import Any

logger = logging.getLogger(__name__)


def add_bm25_chunk(
    conn: sqlite3.Connection,
    chunk_id: str,
    meeting_id: int,
    content: str,
    tokenized: str = "[]",  # unused placeholder; kept for schema compat
    metadata: str | None = None,  # JSON object string
) -> None:
    """Add a BM25 index chunk."""
    conn.execute(
        """INSERT OR REPLACE INTO bm25_index
           (chunk_id, meeting_id, content, tokenized, metadata)
           VALUES (?, ?, ?, ?, ?)""",
        (chunk_id, meeting_id, content, tokenized, metadata),
    )


def delete_bm25_chunks_by_meeting(conn: sqlite3.Connection, meeting_id: int) -> None:
    """Delete all BM25 chunks for a meeting."""
    conn.execute("DELETE FROM bm25_index WHERE meeting_id=?", (meeting_id,))


def delete_bm25_chunks_by_file(conn: sqlite3.Connection, meeting_id: int, file_id: int) -> None:
    """Delete BM25 chunks for one file under a meeting."""
    prefix = f"meeting_{meeting_id}_file_{file_id}_chunk_%"
    conn.execute(
        "DELETE FROM bm25_index WHERE meeting_id=? AND chunk_id LIKE ?",
        (meeting_id, prefix),
    )


def get_all_bm25_chunks(conn: sqlite3.Connection, limit: int = 50_000) -> list[dict]:
    """Get all BM25 chunks for rebuilding the index."""
    rows = conn.execute(
        """SELECT chunk_id, meeting_id, content, tokenized, metadata
           FROM bm25_index ORDER BY id LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_bm25_stats(conn: sqlite3.Connection) -> dict[str, float]:
    """Get BM25 statistics (total_docs, avg_doc_len)."""
    rows = conn.execute("SELECT key, value FROM bm25_stats").fetchall()
    return {row["key"]: row["value"] for row in rows}


def update_bm25_stats(conn: sqlite3.Connection, total_docs: int, avg_doc_len: float) -> None:
    """Update BM25 statistics."""
    conn.execute(
        "INSERT OR REPLACE INTO bm25_stats (key, value) VALUES (?, ?)",
        ("total_docs", total_docs),
    )
    conn.execute(
        "INSERT OR REPLACE INTO bm25_stats (key, value) VALUES (?, ?)",
        ("avg_doc_len", avg_doc_len),
    )


def clear_bm25_index(conn: sqlite3.Connection) -> None:
    """Clear all BM25 index data."""
    conn.execute("DELETE FROM bm25_index")
    conn.execute("DELETE FROM bm25_stats")
    conn.execute("INSERT INTO bm25_stats (key, value) VALUES ('total_docs', 0)")
    conn.execute("INSERT INTO bm25_stats (key, value) VALUES ('avg_doc_len', 0)")


def check_fts5_integrity(conn: sqlite3.Connection) -> bool:
    """Run FTS5 integrity check on the bm25_chunks virtual table.

    Uses the FTS5-specific integrity-check command which validates that
    the FTS index matches the external content table (bm25_index).

    Returns True if healthy, False if corrupted or missing.
    """
    try:
        # FTS5 external content table integrity check via special insert
        conn.execute("INSERT INTO bm25_chunks(bm25_chunks, rank) VALUES('integrity-check', 1)")
        return True
    except Exception as exc:
        logger.warning("FTS5 integrity check failed: %s", exc)
        return False


def fts5_search(
    conn: sqlite3.Connection,
    query: str,
    meeting_ids: list[int] | None = None,
    file_ids: list[int] | None = None,
    limit: int = 10,
    speaker_names: list[str] | None = None,
) -> list[dict]:
    """Search bm25_chunks FTS5 virtual table with BM25 ranking.

    Returns list of dicts with chunk_id, meeting_id, content, metadata, rank.
    When speaker_names is provided, results are filtered to chunks whose
    ``speakers_in_chunk`` metadata field contains at least one of the names.
    """
    if not query.strip():
        return []

    # Tokenise query into individual quoted terms joined with OR so that
    # a document only needs to match one term to be considered.  FTS5's
    # BM25 ranker then sorts by relevance.  This avoids zero-result
    # queries caused by long natural-language questions when AND would
    # require every word to appear in the same short chunk.
    tokens = [t for t in query.replace('"', '""').split() if t]
    # Strip FTS5 operator characters from each token for defense-in-depth.
    _FTS5_SPECIAL = set('*+-><(){}|:="^#')
    tokens = ["".join(c for c in t if c not in _FTS5_SPECIAL) for t in tokens]
    tokens = [t for t in tokens if t]
    safe_query = " OR ".join(f'"{t}"' for t in tokens) if tokens else ""
    meeting_ids_json = dumps(meeting_ids or [])
    file_ids_json = dumps(file_ids or [])
    speaker_names_json = dumps([n.lower() for n in speaker_names] if speaker_names else [])

    sql = """
        SELECT chunk_id, meeting_id, content, metadata, rank
        FROM bm25_chunks
        WHERE bm25_chunks MATCH ?
          AND (
            ? = 0
            OR meeting_id IN (
                SELECT CAST(value AS INTEGER) FROM json_each(?)
            )
          )
          AND (
            ? = 0
            OR CAST(json_extract(metadata, '$.file_id') AS INTEGER) IN (
                SELECT CAST(value AS INTEGER) FROM json_each(?)
            )
          )
          AND (
            ? = 0
            OR EXISTS (
                SELECT 1 FROM json_each(?) AS sn
                WHERE INSTR(
                    LOWER(COALESCE(json_extract(metadata, '$.speakers_in_chunk'), '')),
                    sn.value
                ) > 0
            )
          )
        ORDER BY rank
        LIMIT ?
    """
    params: list[Any] = [
        safe_query,
        1 if meeting_ids else 0,
        meeting_ids_json,
        1 if file_ids else 0,
        file_ids_json,
        1 if speaker_names else 0,
        speaker_names_json,
        limit,
    ]

    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        logger.warning("FTS5 search failed for query: %s", query, exc_info=True)
        return []


def get_page_sibling_chunks(
    conn: sqlite3.Connection,
    *,
    meeting_id: int,
    file_id: int | None,
    page_number: int | None,
    exclude_chunk_index: int | None,
    content_types: list[str] | None = None,
    limit: int = 2,
) -> list[dict]:
    """Fetch sibling chunks in the same meeting/file/page from bm25_index metadata."""
    sql = """
        SELECT chunk_id, meeting_id, content, metadata
        FROM bm25_index
        WHERE meeting_id = ?
    """
    params: list[Any] = [meeting_id]
    if file_id is not None:
        sql += " AND CAST(json_extract(metadata, '$.file_id') AS INTEGER) = ?"
        params.append(file_id)
    if page_number is not None:
        sql += " AND CAST(json_extract(metadata, '$.page_number') AS INTEGER) = ?"
        params.append(page_number)
    if exclude_chunk_index is not None:
        sql += " AND CAST(json_extract(metadata, '$.chunk_index') AS INTEGER) != ?"
        params.append(exclude_chunk_index)
    content_types_json = dumps(content_types or [])
    sql += """
        AND (
            ? = 0
            OR COALESCE(json_extract(metadata, '$.content_type'), 'text') IN (
                SELECT value FROM json_each(?)
            )
        )
    """
    params.extend([1 if content_types else 0, content_types_json])
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.Error:
        logger.warning(
            "Failed sibling chunk query for meeting_id=%s file_id=%s page_number=%s",
            meeting_id,
            file_id,
            page_number,
            exc_info=True,
        )
        return []
    out: list[dict] = []
    for row in rows:
        meta_raw = row["metadata"]
        meta: dict[str, Any]
        if isinstance(meta_raw, str) and meta_raw:
            try:
                parsed = loads(meta_raw)
                meta = parsed if isinstance(parsed, dict) else {}
            except JSONDecodeError:
                meta = {}
        else:
            meta = {}
        out.append(
            {
                "content": row["content"],
                "metadata": meta,
                "score": 0.0,
            }
        )
    return out


# ---------------------------------------------------------------------------
# File-level summary BM25 (for hybrid routing)
# ---------------------------------------------------------------------------


def upsert_file_summary_bm25(
    conn: sqlite3.Connection,
    file_id: int,
    meeting_id: int,
    summary: str,
) -> None:
    """Insert or update a file summary in the BM25 index."""
    if not summary or not summary.strip():
        delete_file_summary_bm25(conn, file_id)
        return
    conn.execute(
        """INSERT OR REPLACE INTO file_summary_bm25 (file_id, meeting_id, summary)
           VALUES (?, ?, ?)""",
        (file_id, meeting_id, summary),
    )


def delete_file_summary_bm25(conn: sqlite3.Connection, file_id: int) -> None:
    """Delete a file summary from the BM25 index."""
    conn.execute("DELETE FROM file_summary_bm25 WHERE file_id=?", (file_id,))


def delete_file_summaries_bm25_by_meeting(
    conn: sqlite3.Connection,
    meeting_id: int,
) -> None:
    """Delete all file summaries for a meeting from the BM25 index."""
    conn.execute("DELETE FROM file_summary_bm25 WHERE meeting_id=?", (meeting_id,))


def fts5_search_file_summaries(
    conn: sqlite3.Connection,
    query: str,
    meeting_ids: list[int] | None = None,
    limit: int = 20,
) -> list[dict]:
    """Search file summaries via FTS5 BM25.

    Returns list of dicts with file_id, meeting_id, score (negated rank,
    higher is better).
    """
    if not query.strip():
        return []

    # Tokenise query into individual quoted terms joined with OR so that
    # a document only needs to match one term to be considered.  FTS5's
    # BM25 ranker then sorts by relevance.  This avoids zero-result
    # queries caused by long natural-language questions when AND would
    # require every word to appear in the same short chunk.
    tokens = [t for t in query.replace('"', '""').split() if t]
    safe_query = " OR ".join(f'"{t}"' for t in tokens) if tokens else ""
    meeting_ids_json = dumps(meeting_ids or [])

    sql = """
        SELECT fsb.file_id, fsb.meeting_id, rank
        FROM file_summary_fts fsft
        JOIN file_summary_bm25 fsb ON fsb.id = fsft.rowid
        WHERE file_summary_fts MATCH ?
          AND (
            ? = 0
            OR fsb.meeting_id IN (
                SELECT CAST(value AS INTEGER) FROM json_each(?)
            )
          )
        ORDER BY rank
        LIMIT ?
    """
    params: list[Any] = [
        safe_query,
        1 if meeting_ids else 0,
        meeting_ids_json,
        limit,
    ]

    try:
        rows = conn.execute(sql, params).fetchall()
        return [
            {
                "file_id": row["file_id"],
                "meeting_id": row["meeting_id"],
                "score": -row["rank"],
            }
            for row in rows
        ]
    except sqlite3.Error:
        logger.warning("File summary FTS5 search failed for query: %s", query, exc_info=True)
        return []
