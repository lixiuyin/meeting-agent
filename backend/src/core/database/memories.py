"""User memory CRUD operations."""

import contextlib
import sqlite3
from typing import Any

from ._scopes import MEMORY_SCOPE_COLUMNS, add_scopes


def write_memory_audit(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    memory_key: str,
    action: str,
    old_value: str | None = None,
    new_value: str | None = None,
    detail: str | None = None,
) -> None:
    """Write a row to the memory_audit_log table (best-effort).

    Sets expires_at to 90 days from now so stale rows can be swept.
    """
    with contextlib.suppress(Exception):
        conn.execute(
            "INSERT INTO memory_audit_log"
            " (user_id, memory_key, action, old_value, new_value, detail, expires_at)"
            " VALUES (?, ?, ?, ?, ?, ?, strftime('%Y-%m-%d %H:%M:%S', 'now', '+90 days'))",
            (user_id, memory_key, action, old_value, new_value, detail),
        )


def cleanup_expired_audit_logs(conn: sqlite3.Connection) -> int:
    """Delete expired memory_audit_log rows. Returns count deleted."""
    try:
        cursor = conn.execute(
            "DELETE FROM memory_audit_log "
            "WHERE expires_at IS NOT NULL "
            "AND expires_at <= strftime('%Y-%m-%d %H:%M:%S', 'now')"
        )
        return cursor.rowcount
    except Exception:
        return 0


def _encode_id_list(ids: list[int] | None) -> str | None:
    if not ids:
        return None
    return ",".join(str(i) for i in ids)


def _decode_id_list(raw: str | None) -> list[int] | None:
    """Decode comma-separated ID list, deduplicating while preserving order."""
    if not raw:
        return None
    seen: set[int] = set()
    result: list[int] = []
    for x in raw.split(","):
        x = x.strip()
        if not x:
            continue
        val = int(x)
        if val in seen:
            continue
        seen.add(val)
        result.append(val)
    return result or None


_MEMORY_SCOPE_COLS_M = MEMORY_SCOPE_COLUMNS.format(alias="m")

# Explicit base columns for SELECTs that join the scope subqueries. We avoid
# ``SELECT m.*`` here because the legacy CSV columns ``meeting_ids`` /
# ``file_ids`` still exist on ``user_memories`` and would shadow our junction
# subquery aliases of the same name in sqlite3.Row lookups.
_MEMORY_BASE_COLS_M = (
    "m.id, m.user_id, m.key, m.value, m.source, m.created_at, m.updated_at, "
    "m.importance, m.expires_at, m.last_accessed, m.access_count, m.category, "
    "m.embedding_id, m.session_id, m.turn_index, m.superseded_by, "
    "m.relevance_score, m.is_legacy_scope"
)


def set_memory(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    key: str,
    value: str,
    source: str = "manual",
    importance: float = 3,
    expires_at: str | None = None,
    category: str | None = None,
    embedding_id: str | None = None,
    meeting_ids: list[int] | None = None,
    file_ids: list[int] | None = None,
) -> None:
    """Upsert a memory row. Scope IDs are stored in the ``memory_scopes`` table.

    On conflict, scope rows are unioned via ``INSERT OR IGNORE`` — a memory
    touched across multiple meetings accumulates scope rather than being
    overwritten. The legacy CSV columns are no longer written.
    """
    cursor = conn.execute(
        """INSERT INTO user_memories
           (user_id, key, value, source, importance, expires_at, category,
            embedding_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(user_id, key) DO UPDATE SET
               value=?, source=?, importance=?, expires_at=?, category=?,
               embedding_id=COALESCE(?, embedding_id),
               updated_at=CURRENT_TIMESTAMP
           RETURNING id""",
        (
            user_id,
            key,
            value,
            source,
            importance,
            expires_at,
            category,
            embedding_id,
            value,
            source,
            importance,
            expires_at,
            category,
            embedding_id,
        ),
    )
    row = cursor.fetchone()
    if row is not None and (meeting_ids or file_ids):
        add_scopes(
            conn,
            kind="memory",
            owner_id=int(row["id"]),
            meeting_ids=meeting_ids,
            file_ids=file_ids,
        )

    write_memory_audit(
        conn,
        user_id=user_id,
        memory_key=key,
        action="upsert",
        new_value=value,
        detail=f"source={source} importance={importance}",
    )


def get_memory(conn: sqlite3.Connection, *, user_id: str, key: str) -> str | None:
    row = conn.execute(
        "SELECT value FROM user_memories WHERE user_id=? AND key=?",
        (user_id, key),
    ).fetchone()
    return row["value"] if row else None


def get_memory_full(conn: sqlite3.Connection, *, user_id: str, key: str) -> dict | None:
    """Get full memory record including importance, category, etc.

    The returned dict includes ``meeting_ids`` / ``file_ids`` as legacy CSV
    strings sourced from the ``memory_scopes`` junction table.
    """
    row = conn.execute(
        "SELECT " + _MEMORY_BASE_COLS_M + ", " + _MEMORY_SCOPE_COLS_M + " "
        "FROM user_memories m WHERE m.user_id=? AND m.key=?",
        (user_id, key),
    ).fetchone()
    return dict(row) if row else None


def get_memories_batch(
    conn: sqlite3.Connection, *, user_id: str, keys: list[str]
) -> dict[str, dict]:
    """Fetch multiple memory records in a single SELECT.

    Returns a ``{key: row_dict}`` mapping.  Missing keys are silently
    omitted from the result.
    """
    if not keys:
        return {}
    placeholders = ",".join("?" for _ in keys)
    rows = conn.execute(
        "SELECT " + _MEMORY_BASE_COLS_M + ", " + _MEMORY_SCOPE_COLS_M + " FROM user_memories m "
        "WHERE m.user_id=? AND m.key IN (" + placeholders + ")",
        (user_id, *keys),
    ).fetchall()
    return {row["key"]: dict(row) for row in rows}


def _build_memory_where(
    user_id: str,
    category: str | None = None,
    include_expired: bool = False,
) -> tuple[str, list[Any]]:
    """Build the WHERE clause shared by list and count queries."""
    clauses = ["m.user_id=?"]
    params: list[Any] = [user_id]
    if category:
        clauses.append("m.category=?")
        params.append(category)
    if not include_expired:
        clauses.append("(m.expires_at IS NULL OR m.expires_at > CURRENT_TIMESTAMP)")
    return " AND ".join(clauses), params


def list_memories(
    conn: sqlite3.Connection,
    *,
    user_id: str = "default",
    include_expired: bool = False,
    category: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """List memories for a user, optionally filtering by category and excluding expired."""
    cols = (
        "m.key, m.value, m.source, m.importance, m.category, m.last_accessed, "
        "m.access_count, m.expires_at, m.updated_at, m.relevance_score, m.superseded_by, "
        "m.session_id, m.is_legacy_scope, " + _MEMORY_SCOPE_COLS_M
    )
    where, params = _build_memory_where(user_id, category, include_expired)
    query = (
        "SELECT " + cols + " FROM user_memories m WHERE " + where + " "
        "ORDER BY m.importance DESC, m.updated_at DESC LIMIT ? OFFSET ?"
    )
    params.extend([limit, offset])
    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def count_memories(
    conn: sqlite3.Connection,
    *,
    user_id: str = "default",
    include_expired: bool = False,
    category: str | None = None,
) -> int:
    """Count memories for a user, optionally filtering by category and excluding expired."""
    where, params = _build_memory_where(user_id, category, include_expired)
    query = "SELECT COUNT(*) FROM user_memories m WHERE " + where
    row = conn.execute(query, params).fetchone()
    return row[0]


def list_and_count_memories(
    conn: sqlite3.Connection,
    *,
    user_id: str = "default",
    include_expired: bool = False,
    category: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """List memories with total count in a single connection use.

    Avoids the extra round-trip of calling list_memories + count_memories
    separately (merges two connections into one).
    """
    items = list_memories(
        conn,
        user_id=user_id,
        include_expired=include_expired,
        category=category,
        limit=limit,
        offset=offset,
    )
    total = count_memories(
        conn,
        user_id=user_id,
        include_expired=include_expired,
        category=category,
    )
    return items, total


def delete_memory(conn: sqlite3.Connection, *, user_id: str, key: str) -> str | None:
    """Delete memory and return its embedding_id for Chroma cleanup."""
    mem = get_memory_full(conn, user_id=user_id, key=key)
    conn.execute(
        "DELETE FROM user_memories WHERE user_id=? AND key=?",
        (user_id, key),
    )
    if mem:
        write_memory_audit(
            conn,
            user_id=user_id,
            memory_key=key,
            action="delete",
            old_value=mem.get("value"),
            detail="hard_delete",
        )
    return mem.get("embedding_id") if mem else None


def touch_memory_access(conn: sqlite3.Connection, *, user_id: str, key: str) -> None:
    """Record memory access for importance decay tracking."""
    conn.execute(
        """UPDATE user_memories
           SET last_accessed = CURRENT_TIMESTAMP, access_count = access_count + 1
           WHERE user_id=? AND key=?""",
        (user_id, key),
    )


def get_expired_memory_ids(conn: sqlite3.Connection) -> list[dict]:
    """Get embedding_id and key for all expired memories (before deletion)."""
    rows = conn.execute(
        "SELECT embedding_id, user_id, key FROM user_memories "
        "WHERE expires_at IS NOT NULL AND expires_at <= CURRENT_TIMESTAMP"
    ).fetchall()
    return [dict(r) for r in rows]


def delete_expired_memories(conn: sqlite3.Connection) -> int:
    """Delete all expired memories. Returns count of deleted rows."""
    cursor = conn.execute(
        "DELETE FROM user_memories WHERE expires_at IS NOT NULL AND expires_at <= CURRENT_TIMESTAMP"
    )
    return cursor.rowcount


def search_memories_by_importance(
    conn: sqlite3.Connection, *, user_id: str, min_importance: float = 1, limit: int = 10
) -> list[dict]:
    """Get top active (non-superseded) memories by importance score."""
    rows = conn.execute(
        "SELECT m.key, m.value, m.source, m.importance, m.category, m.last_accessed, "
        "m.access_count, m.expires_at, m.updated_at, m.relevance_score, "
        "m.superseded_by, m.is_legacy_scope, " + _MEMORY_SCOPE_COLS_M + " "
        "FROM user_memories m "
        "WHERE m.user_id=? AND m.importance >= ? AND m.superseded_by IS NULL "
        "AND (m.expires_at IS NULL OR m.expires_at > CURRENT_TIMESTAMP) "
        "ORDER BY COALESCE(m.relevance_score, m.importance) DESC, m.last_accessed DESC "
        "LIMIT ?",
        (user_id, min_importance, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def mark_memory_superseded(
    conn: sqlite3.Connection, *, user_id: str, key: str, superseded_by: str
) -> None:
    """Mark a memory as superseded by a newer/consolidated memory."""
    conn.execute(
        (
            "UPDATE user_memories SET superseded_by=?, "
            "updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND key=?"
        ),
        (superseded_by, user_id, key),
    )
    write_memory_audit(
        conn,
        user_id=user_id,
        memory_key=key,
        action="supersede",
        detail=f"superseded_by={superseded_by}",
    )


def get_memory_timeline(
    conn: sqlite3.Connection, *, user_id: str, key: str, max_depth: int = 20
) -> list[dict]:
    """Walk the supersede chain for a memory, oldest → newest.

    Follows ``superseded_by`` forward from the supplied key and backward from
    any row whose ``superseded_by`` points to it, then returns the chain
    sorted by ``updated_at``. Useful for answering "what did the user use
    before / what changed" without polluting active recall.

    Includes entries filtered out of normal queries (superseded rows retained
    in the table by design).
    """
    visited: set[str] = set()
    to_visit: list[str] = [key]
    collected: list[dict] = []
    while to_visit and len(visited) < max_depth:
        current = to_visit.pop()
        if current in visited:
            continue
        visited.add(current)
        row = conn.execute(
            "SELECT id, user_id, key, value, source, created_at, updated_at, "
            "importance, expires_at, last_accessed, access_count, category, "
            "embedding_id, session_id, turn_index, superseded_by, "
            "relevance_score, is_legacy_scope "
            "FROM user_memories WHERE user_id=? AND key=?",
            (user_id, current),
        ).fetchone()
        if not row:
            continue
        collected.append(dict(row))
        # Follow forward pointer
        if row["superseded_by"] and row["superseded_by"] not in visited:
            to_visit.append(row["superseded_by"])
        # Follow reverse pointers: anything that superseded this key
        rev = conn.execute(
            "SELECT key FROM user_memories WHERE user_id=? AND superseded_by=?",
            (user_id, current),
        ).fetchall()
        for rev_row in rev:
            if rev_row["key"] not in visited:
                to_visit.append(rev_row["key"])

    collected.sort(key=lambda r: r.get("updated_at") or "")
    return collected


def update_memory_relevance_score(
    conn: sqlite3.Connection, *, user_id: str, key: str, relevance_score: float
) -> None:
    """Update the float relevance score used for continuous decay."""
    conn.execute(
        (
            "UPDATE user_memories SET relevance_score=?, "
            "updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND key=?"
        ),
        (relevance_score, user_id, key),
    )


def update_memory_importance(
    conn: sqlite3.Connection, *, user_id: str, key: str, importance: float
) -> None:
    """Update decayed importance score for a single memory row."""
    conn.execute(
        (
            "UPDATE user_memories SET importance=?, "
            "updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND key=?"
        ),
        (importance, user_id, key),
    )


def update_memory(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    key: str,
    value: str | None = None,
    importance: float | None = None,
    category: str | None = None,
) -> bool:
    """Update specific fields of an existing memory. Returns True if row was found."""
    updates: list[str] = []
    params: list[Any] = []
    if value is not None:
        updates.append("value=?")
        params.append(value)
    if importance is not None:
        updates.append("importance=?")
        params.append(importance)
    if category is not None:
        updates.append("category=?")
        params.append(category)
    if not updates:
        return False
    for part in updates:
        col = part.split("=")[0]
        if not col.isidentifier():
            raise ValueError(f"Invalid column name: {col}")
    updates.append("updated_at=CURRENT_TIMESTAMP")
    params.extend([user_id, key])
    cursor = conn.execute(
        "UPDATE user_memories SET " + ", ".join(updates) + " WHERE user_id=? AND key=?",
        params,
    )
    return cursor.rowcount > 0


def get_memories_for_consolidation(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    category: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Get active memories grouped for consolidation analysis."""
    base = """
        SELECT key, value, source, importance, category, last_accessed,
               access_count, expires_at, updated_at, embedding_id
        FROM user_memories
        WHERE user_id=? AND superseded_by IS NULL
          AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
    """
    params: list = [user_id]
    if category:
        base += " AND category=?"
        params.append(category)
    base += " ORDER BY importance DESC, last_accessed DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(base, params).fetchall()
    return [dict(r) for r in rows]
