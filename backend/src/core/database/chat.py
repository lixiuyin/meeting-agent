"""Session, message, session-summary, and cross-session FTS5 CRUD operations."""

import json
import sqlite3
import uuid

# ---- Session CRUD ----


def create_session(
    conn: sqlite3.Connection,
    *,
    user_id: str = "default",
    title: str | None = None,
    session_id: str | None = None,
    config_json: str | None = None,
) -> str:
    """Create a new chat session.  If *session_id* is given, use it; otherwise
    generate a random UUID.  Returns the session_id."""
    sid = session_id or uuid.uuid4().hex
    if config_json is None:
        # Keep the low-level helper compatible with minimal/legacy schemas;
        # production startup still migrates the durable config columns before
        # a configured session is created.
        conn.execute(
            "INSERT INTO chat_sessions (id, user_id, title) VALUES (?, ?, ?)",
            (sid, user_id, title),
        )
    else:
        conn.execute(
            "INSERT INTO chat_sessions (id, user_id, title, config_json) VALUES (?, ?, ?, ?)",
            (sid, user_id, title, config_json),
        )
    return sid


def branch_session(
    conn: sqlite3.Connection,
    *,
    source_session_id: str,
    user_id: str,
    before_message_id: int | None,
    reason: str,
) -> str:
    """Copy the stable prefix of a conversation into a traceable new branch.

    The source session remains immutable. ``before_message_id`` must identify
    a human message and is excluded together with every later message. Passing
    ``None`` copies the complete persisted history, which is used when a
    just-started run is withdrawn before it saves a turn.
    """
    source = get_session(conn, source_session_id, user_id=user_id)
    if source is None:
        raise LookupError("Session not found")
    if reason not in {"edit", "regenerate", "withdraw"}:
        raise ValueError("Unsupported branch reason")
    if before_message_id is not None:
        target = conn.execute(
            "SELECT role FROM chat_messages WHERE id=? AND session_id=?",
            (before_message_id, source_session_id),
        ).fetchone()
        if target is None or target["role"] != "human":
            raise LookupError("User message not found")

    sid = uuid.uuid4().hex
    config_json = source.get("config_json")
    if config_json:
        try:
            branch_config = json.loads(config_json)
        except (TypeError, json.JSONDecodeError):
            branch_config = None
        if isinstance(branch_config, dict):
            # A branch deliberately has no copied task checkpoint. Retaining
            # ``saved_snapshot`` would advertise a continuation state that
            # cannot be validated against the newly assigned message IDs.
            branch_config["continuation_mode"] = "latest"
            config_json = json.dumps(branch_config, sort_keys=True, separators=(",", ":"))
    conn.execute(
        "INSERT INTO chat_sessions "
        "(id,user_id,title,config_json,parent_session_id,branched_from_message_id,branch_reason) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            sid,
            user_id,
            source.get("title"),
            config_json,
            source_session_id,
            before_message_id,
            reason,
        ),
    )
    parameters: tuple[object, ...] = (sid, source_session_id)
    boundary = ""
    if before_message_id is not None:
        boundary = " AND id<?"
        parameters = (sid, source_session_id, before_message_id)
    # Keep the copy inside SQLite. This avoids materialising an arbitrarily
    # large conversation prefix as Python objects while the write lock is held.
    conn.execute(
        "INSERT INTO chat_messages "
        "(session_id,role,content,sources_json,degradation_reason) "
        "SELECT ?,role,content,sources_json,degradation_reason FROM chat_messages "
        f"WHERE session_id=?{boundary} ORDER BY id",
        parameters,
    )
    return sid


def get_session_ancestor_ids(
    conn: sqlite3.Connection, session_id: str, *, user_id: str
) -> set[str]:
    """Return this user's branch ancestors, stopping safely on malformed cycles."""
    ancestors: set[str] = set()
    current = session_id
    while current and len(ancestors) < 100:
        row = conn.execute(
            "SELECT parent_session_id FROM chat_sessions WHERE id=? AND user_id=?",
            (current, user_id),
        ).fetchone()
        if row is None or not row["parent_session_id"]:
            break
        parent = str(row["parent_session_id"])
        if parent in ancestors or parent == session_id:
            break
        ancestors.add(parent)
        current = parent
    return ancestors


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
           ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?""",
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


def touch_session(conn: sqlite3.Connection, session_id: str, *, user_id: str | None = None) -> None:
    """Update session access time and count."""
    if user_id is None:
        conn.execute(
            """UPDATE chat_sessions
               SET last_accessed = CURRENT_TIMESTAMP, access_count = access_count + 1,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id=?""",
            (session_id,),
        )
    else:
        conn.execute(
            """UPDATE chat_sessions
               SET last_accessed = CURRENT_TIMESTAMP, access_count = access_count + 1,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id=? AND user_id=?""",
            (session_id, user_id),
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


def add_turn(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    human_content: str,
    ai_content: str,
    sources_json: str | None = None,
    degradation_reason: str | None = None,
) -> tuple[int, int]:
    """Atomically append one complete human/AI turn on the caller's transaction."""
    human_cursor = conn.execute(
        "INSERT INTO chat_messages (session_id, role, content) VALUES (?, 'human', ?)",
        (session_id, human_content),
    )
    ai_cursor = conn.execute(
        "INSERT INTO chat_messages (session_id, role, content, sources_json, degradation_reason) "
        "VALUES (?, 'ai', ?, ?, ?)",
        (session_id, ai_content, sources_json, degradation_reason),
    )
    if human_cursor.lastrowid is None or ai_cursor.lastrowid is None:
        raise RuntimeError("Failed to persist complete chat turn")
    return int(human_cursor.lastrowid), int(ai_cursor.lastrowid)


def get_messages(
    conn: sqlite3.Connection,
    session_id: str,
    limit: int | None = None,
    *,
    before_id: int | None = None,
) -> list[dict]:
    """Get messages in chronological order (oldest first).

    When limit is set, returns the N most recent messages (still in chronological order).
    """
    _MAX_MESSAGES = 10_000
    effective_limit = min(limit, _MAX_MESSAGES) if limit else _MAX_MESSAGES
    if before_id is None:
        rows = conn.execute(
            "SELECT id, role, content, sources_json, degradation_reason FROM chat_messages "
            "WHERE session_id=? ORDER BY id DESC LIMIT ?",
            (session_id, effective_limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, role, content, sources_json, degradation_reason FROM chat_messages "
            "WHERE session_id=? AND id<? ORDER BY id DESC LIMIT ?",
            (session_id, before_id, effective_limit),
        ).fetchall()
    rows = list(reversed(rows))
    return [dict(r) for r in rows]


def count_messages(conn: sqlite3.Connection, session_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM chat_messages WHERE session_id=?", (session_id,)
    ).fetchone()
    return row[0]


def clear_messages(conn: sqlite3.Connection, session_id: str) -> None:
    conn.execute("DELETE FROM chat_context_checkpoints WHERE session_id=?", (session_id,))
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

    The summary is monotonic in ``turn_count``: an older snapshot can never
    overwrite one that covered more messages merely because its LLM call
    finished later. Equal-coverage retries may replace an older result.
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
           WHERE COALESCE(session_summaries.turn_count, 0)
                 < COALESCE(excluded.turn_count, 0)
              OR (
                   COALESCE(session_summaries.turn_count, 0)
                     = COALESCE(excluded.turn_count, 0)
                   AND session_summaries.updated_at < excluded.updated_at
                 )
           RETURNING id""",
        (session_id, user_id, summary, topics, key_entities, decisions, turn_count, embedding_id),
    )
    row = cursor.fetchone()
    return int(row["id"]) if row else 0


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
        f"""SELECT ss.*, cs.title AS session_title
            FROM session_summaries ss
            JOIN chat_sessions cs ON cs.id = ss.session_id
            WHERE ss.session_id IN ({placeholders})""",
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
           ORDER BY ss.created_at DESC, ss.session_id DESC
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
    base += " GROUP BY cs.id HAVING COUNT(cm.id) >= ? ORDER BY cs.updated_at DESC, cs.id DESC"
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
    # Operators are separators, not deletions: FTS5 indexes ``ZXQ-4817`` as
    # two tokens, so converting the query to ``ZXQ4817`` would make an exact
    # incident/ticket lookup impossible.
    sanitized = "".join(" " if c in _FTS5_SPECIAL else c for c in query)
    tokens = [token for token in sanitized.split() if token]
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
