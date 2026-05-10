"""Session, message, session-summary, and cross-session FTS5 CRUD operations."""

import sqlite3
import uuid

# ---- Session CRUD ----


def create_session(
    conn: sqlite3.Connection,
    *,
    user_id: str = "default",
    title: str | None = None,
    session_id: str | None = None,
) -> str:
    """Create a new chat session.  If *session_id* is given, use it; otherwise
    generate a random UUID.  Returns the session_id."""
    sid = session_id or uuid.uuid4().hex
    conn.execute(
        "INSERT INTO chat_sessions (id, user_id, title) VALUES (?, ?, ?)",
        (sid, user_id, title),
    )
    return sid


def ensure_session(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    user_id: str = "default",
    title: str | None = None,
) -> str:
    """Ensure a session with *session_id* exists, creating it if needed.

    M-23: Uses ``INSERT OR IGNORE`` so concurrent ensure calls for the same
    session_id are safe — exactly one row is created, and both callers get
    the same session_id back.
    """
    conn.execute(
        "INSERT OR IGNORE INTO chat_sessions (id, user_id, title) VALUES (?, ?, ?)",
        (session_id, user_id, title),
    )
    return session_id


def get_session(
    conn: sqlite3.Connection, session_id: str, *, user_id: str | None = None
) -> dict | None:
    if user_id is not None:
        row = conn.execute(
            "SELECT * FROM chat_sessions WHERE id=? AND user_id=?", (session_id, user_id)
        ).fetchone()
    else:
        row = conn.execute("SELECT * FROM chat_sessions WHERE id=?", (session_id,)).fetchone()
    return dict(row) if row else None


def list_sessions(
    conn: sqlite3.Connection, *, user_id: str = "default", limit: int = 50, offset: int = 0
) -> list[dict]:
    rows = conn.execute(
        """SELECT * FROM chat_sessions WHERE user_id=?
           ORDER BY updated_at DESC LIMIT ? OFFSET ?""",
        (user_id, limit, offset),
    ).fetchall()
    return [dict(r) for r in rows]


def count_sessions(conn: sqlite3.Connection, *, user_id: str = "default") -> int:
    row = conn.execute("SELECT COUNT(*) FROM chat_sessions WHERE user_id=?", (user_id,)).fetchone()
    return row[0]


def delete_session(
    conn: sqlite3.Connection, session_id: str, *, user_id: str | None = None
) -> None:
    if user_id is not None:
        conn.execute("DELETE FROM chat_sessions WHERE id=? AND user_id=?", (session_id, user_id))
    else:
        conn.execute("DELETE FROM chat_sessions WHERE id=?", (session_id,))


def touch_session(conn: sqlite3.Connection, session_id: str) -> None:
    """Update session access time and count."""
    conn.execute(
        """UPDATE chat_sessions
           SET last_accessed = CURRENT_TIMESTAMP, access_count = access_count + 1,
               updated_at = CURRENT_TIMESTAMP
           WHERE id=?""",
        (session_id,),
    )


# ---- Conversational anchor I/O ----

# Default TTL in seconds (configurable via RAG_ANCHOR_TTL_MINUTES)
_DEFAULT_ANCHOR_TTL_SECONDS = 30 * 60


def read_anchor(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    ttl_seconds: int = _DEFAULT_ANCHOR_TTL_SECONDS,
) -> dict | None:
    """Read anchor data for a session if it exists and is fresh.

    Returns ``{"meeting_ids": [...], "file_ids": [...]}`` or None if
    missing / stale / empty.  The function is read-only; sliding TTL is
    implemented by the caller via :func:`touch_anchor` after a successful
    read so write contention stays in the dedicated write connection.
    """
    row = conn.execute(
        "SELECT anchor_data, anchor_updated_at FROM chat_sessions WHERE id=?", (session_id,)
    ).fetchone()
    if not row or not row["anchor_data"] or row["anchor_updated_at"] is None:
        return None
    import json
    from datetime import UTC, datetime

    updated_at = datetime.fromisoformat(row["anchor_updated_at"])
    age = (datetime.now(UTC) - updated_at.replace(tzinfo=UTC)).total_seconds()
    if age > ttl_seconds:
        return None
    try:
        data = json.loads(row["anchor_data"])
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    return None


def touch_anchor(conn: sqlite3.Connection, session_id: str) -> None:
    """Refresh ``anchor_updated_at`` for the given session.

    Used by callers implementing a sliding-TTL anchor policy.  Always uses
    a write connection to avoid contention with the read path.  Silent
    no-op when the session row is missing.
    """
    conn.execute(
        "UPDATE chat_sessions SET anchor_updated_at = CURRENT_TIMESTAMP WHERE id=?",
        (session_id,),
    )


def write_anchor(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    meeting_ids: list[int] | None = None,
    file_ids: list[int] | None = None,
    max_ids: int = 8,
) -> None:
    """Persist anchor data from the final reranked result set.

    Each list is capped at *max_ids* to prevent runaway growth.
    """
    import json

    mids = list(meeting_ids or [])[:max_ids]
    fids = list(file_ids or [])[:max_ids]
    payload = json.dumps({"meeting_ids": mids, "file_ids": fids})
    conn.execute(
        """UPDATE chat_sessions
           SET anchor_data = ?, anchor_updated_at = CURRENT_TIMESTAMP,
               updated_at = CURRENT_TIMESTAMP
           WHERE id=?""",
        (payload, session_id),
    )


# ---- Message CRUD ----


def add_message(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    role: str,
    content: str,
    sources_json: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO chat_messages (session_id, role, content, sources_json) VALUES (?, ?, ?, ?)",
        (session_id, role, content, sources_json),
    )


def get_messages(conn: sqlite3.Connection, session_id: str, limit: int | None = None) -> list[dict]:
    """Get messages in chronological order (oldest first).

    When limit is set, returns the N most recent messages (still in chronological order).
    """
    _MAX_MESSAGES = 10_000
    effective_limit = min(limit, _MAX_MESSAGES) if limit else _MAX_MESSAGES
    rows = conn.execute(
        (
            "SELECT role, content, sources_json FROM chat_messages "
            "WHERE session_id=? ORDER BY id DESC LIMIT ?"
        ),
        (session_id, effective_limit),
    ).fetchall()
    rows = list(reversed(rows))
    return [dict(r) for r in rows]


def count_messages(conn: sqlite3.Connection, session_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM chat_messages WHERE session_id=?", (session_id,)
    ).fetchone()
    return row[0]


def clear_messages(conn: sqlite3.Connection, session_id: str) -> None:
    conn.execute("DELETE FROM chat_messages WHERE session_id=?", (session_id,))


# ---------------------------------------------------------------------------
# Session summaries (episodic cross-session memory)
# ---------------------------------------------------------------------------


def upsert_session_summary(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    user_id: str,
    summary: str,
    topics: str | None = None,
    key_entities: str | None = None,
    decisions: str | None = None,
    turn_count: int | None = None,
    embedding_id: str | None = None,
) -> int:
    """Insert or update a session summary. Returns the row ID.

    H-9: The ON CONFLICT update only fires when the new row is fresher than
    the existing one (``updated_at < excluded.updated_at``), preventing
    concurrent summarizations from overwriting newer data with stale results.
    """
    cursor = conn.execute(
        """INSERT INTO session_summaries
           (session_id, user_id, summary, topics, key_entities, decisions,
            turn_count, embedding_id, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(session_id) DO UPDATE SET
             summary=excluded.summary,
             topics=excluded.topics,
             key_entities=excluded.key_entities,
             decisions=excluded.decisions,
             turn_count=excluded.turn_count,
             embedding_id=excluded.embedding_id,
             updated_at=excluded.updated_at
           WHERE updated_at < excluded.updated_at""",
        (session_id, user_id, summary, topics, key_entities, decisions, turn_count, embedding_id),
    )
    return cursor.lastrowid or 0


def get_session_summary(
    conn: sqlite3.Connection, session_id: str, *, user_id: str | None = None
) -> dict | None:
    """Get the summary for a specific session."""
    if user_id is not None:
        row = conn.execute(
            "SELECT * FROM session_summaries WHERE session_id=? AND user_id=?",
            (session_id, user_id),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM session_summaries WHERE session_id=?",
            (session_id,),
        ).fetchone()
    return dict(row) if row else None


def get_session_summaries_batch(
    conn: sqlite3.Connection, session_ids: list[str]
) -> dict[str, dict]:
    """Fetch multiple session summaries in a single SELECT.

    Returns a ``{session_id: row_dict}`` mapping.  Missing IDs are silently
    omitted.
    """
    if not session_ids:
        return {}
    placeholders = ",".join("?" for _ in session_ids)
    rows = conn.execute(
        f"SELECT * FROM session_summaries WHERE session_id IN ({placeholders})",
        session_ids,
    ).fetchall()
    return {row["session_id"]: dict(row) for row in rows}


def list_session_summaries(
    conn: sqlite3.Connection,
    *,
    user_id: str = "default",
    limit: int = 10,
    offset: int = 0,
) -> list[dict]:
    """List session summaries for a user, most recent first."""
    rows = conn.execute(
        """SELECT ss.*, cs.title AS session_title
           FROM session_summaries ss
           JOIN chat_sessions cs ON ss.session_id = cs.id
           WHERE ss.user_id=?
           ORDER BY ss.created_at DESC
           LIMIT ? OFFSET ?""",
        (user_id, limit, offset),
    ).fetchall()
    return [dict(r) for r in rows]


def count_session_summaries(conn: sqlite3.Connection, *, user_id: str = "default") -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM session_summaries WHERE user_id=?", (user_id,)
    ).fetchone()
    return row[0]


def delete_session_summary(conn: sqlite3.Connection, session_id: str) -> None:
    """Delete the summary for a session."""
    conn.execute("DELETE FROM session_summaries WHERE session_id=?", (session_id,))


def get_unsummarized_sessions(
    conn: sqlite3.Connection,
    *,
    user_id: str | None = None,
    min_messages: int = 4,
) -> list[dict]:
    """Find sessions that have enough messages but no summary yet."""
    base = """
        SELECT cs.id, cs.user_id, cs.title, cs.created_at, cs.updated_at,
               COUNT(cm.id) AS message_count
        FROM chat_sessions cs
        JOIN chat_messages cm ON cs.id = cm.session_id
        LEFT JOIN session_summaries ss ON cs.id = ss.session_id
        WHERE ss.id IS NULL
    """
    params: list = []
    if user_id:
        base += " AND cs.user_id=?"
        params.append(user_id)
    base += " GROUP BY cs.id HAVING COUNT(cm.id) >= ? ORDER BY cs.updated_at DESC"
    params.append(min_messages)
    rows = conn.execute(base, params).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Cross-session chat message search (FTS5)
# ---------------------------------------------------------------------------


def search_chat_messages(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    query: str,
    limit: int = 20,
) -> list[dict]:
    """Full-text search over chat messages across all sessions for a user.

    Returns messages with their session context, ranked by relevance.
    """
    # Tokenise and strip FTS5 operators for defense-in-depth (same approach as
    # bm25.py fts5_search).
    _FTS5_SPECIAL = set('*+-><(){}|:="^#')
    tokens = [
        "".join(c for c in t if c not in _FTS5_SPECIAL)
        for t in query.replace('"', '""').split()
        if t
    ]
    escaped_query = " OR ".join(f'"{t}"' for t in tokens) if tokens else ""
    if not escaped_query:
        return []
    rows = conn.execute(
        """SELECT cm.id, cm.session_id, cm.role, cm.content, cm.created_at,
                  cs.title AS session_title, cs.created_at AS session_created_at,
                  rank
           FROM chat_messages_fts fts
           JOIN chat_messages cm ON cm.id = fts.rowid
           JOIN chat_sessions cs ON cm.session_id = cs.id
           WHERE chat_messages_fts MATCH ? AND cs.user_id=?
            ORDER BY rank
            LIMIT ?""",
        (escaped_query, user_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def backfill_chat_messages_fts(conn: sqlite3.Connection) -> int:
    """Backfill FTS5 index with existing chat messages not yet indexed.

    Safe to call on startup — only inserts rows missing from FTS.
    Returns count of rows inserted.
    """
    cursor = conn.execute(
        """INSERT INTO chat_messages_fts(rowid, content)
           SELECT cm.id, cm.content
           FROM chat_messages cm
           LEFT JOIN chat_messages_fts fts ON fts.rowid = cm.id
           WHERE fts.rowid IS NULL"""
    )
    return cursor.rowcount
