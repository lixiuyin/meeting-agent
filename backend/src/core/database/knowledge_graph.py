"""Knowledge-graph entity and relation CRUD operations."""

import json
import sqlite3

from ._scopes import ENTITY_SCOPE_COLUMNS, add_scopes

_ENTITY_SCOPE_COLS_E = ENTITY_SCOPE_COLUMNS.format(alias="e")

# Explicit base columns: avoid ``e.*`` because the legacy CSV columns
# ``meeting_ids`` / ``file_ids`` still exist on ``memory_entities`` and would
# shadow the junction-table subquery aliases of the same name.
_ENTITY_BASE_COLS_E = (
    "e.id, e.user_id, e.name, e.entity_type, e.description, e.embedding_id, "
    "e.first_seen_session, e.last_seen_session, e.mention_count, "
    "e.created_at, e.updated_at, e.is_legacy_scope, e.aliases"
)

# ---------------------------------------------------------------------------
# Knowledge Graph: Entities
# ---------------------------------------------------------------------------


def _normalize_aliases(aliases: list[str] | None, *, exclude: str | None = None) -> list[str]:
    """Strip, lowercase, dedupe alias strings; drop empties and the canonical name."""
    if not aliases:
        return []
    seen: set[str] = set()
    result: list[str] = []
    blocked = exclude.strip().lower() if exclude else None
    for raw in aliases:
        if not isinstance(raw, str):
            continue
        cleaned = raw.strip().lower()
        if not cleaned or cleaned == blocked or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


def _encode_aliases(aliases: list[str] | None) -> str | None:
    """JSON-encode an alias list; return None when empty so the column stays NULL."""
    if not aliases:
        return None
    return json.dumps(aliases, ensure_ascii=False)


def _decode_aliases(raw: str | None) -> list[str]:
    """Decode the aliases column; tolerates legacy CSV and malformed JSON."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        # Legacy/fallback: comma-separated string
        return [s.strip() for s in raw.split(",") if s.strip()]
    if isinstance(parsed, list):
        return [str(s).strip() for s in parsed if isinstance(s, str) and str(s).strip()]
    return []


def upsert_entity(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    name: str,
    entity_type: str,
    description: str | None = None,
    session_id: str | None = None,
    meeting_ids: list[int] | None = None,
    file_ids: list[int] | None = None,
    aliases: list[str] | None = None,
) -> int:
    """Insert or update a knowledge-graph entity. Returns the entity id.

    Scope IDs (meeting_ids / file_ids) accumulate across upserts via the
    ``entity_scopes`` junction table; the legacy CSV columns are no longer
    written. Aliases (alternate surface forms) are stored as a JSON-encoded
    list and merged with the existing list on conflict (dedup preserves
    first-seen order).
    """
    name = name.strip().lower()
    new_aliases = _normalize_aliases(aliases, exclude=name)
    existing = conn.execute(
        "SELECT id, aliases FROM memory_entities WHERE user_id=? AND name=? AND entity_type=?",
        (user_id, name, entity_type),
    ).fetchone()
    if existing is not None:
        merged = _decode_aliases(existing["aliases"])
        seen = set(merged)
        for a in new_aliases:
            if a not in seen:
                merged.append(a)
                seen.add(a)
        aliases_blob = _encode_aliases(merged)
    else:
        aliases_blob = _encode_aliases(new_aliases)
    conn.execute(
        """INSERT INTO memory_entities
               (user_id, name, entity_type, description, first_seen_session,
                last_seen_session, mention_count, updated_at, aliases)
           VALUES (?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP, ?)
           ON CONFLICT(user_id, name, entity_type) DO UPDATE SET
               mention_count = mention_count + 1,
               last_seen_session = COALESCE(excluded.last_seen_session, last_seen_session),
               description = COALESCE(excluded.description, description),
               aliases = excluded.aliases,
               updated_at = CURRENT_TIMESTAMP""",
        (
            user_id,
            name,
            entity_type,
            description,
            session_id,
            session_id,
            aliases_blob,
        ),
    )
    row = conn.execute(
        "SELECT id FROM memory_entities WHERE user_id=? AND name=? AND entity_type=?",
        (user_id, name, entity_type),
    ).fetchone()
    entity_id = int(row["id"])
    if meeting_ids or file_ids:
        add_scopes(
            conn,
            kind="entity",
            owner_id=entity_id,
            meeting_ids=meeting_ids,
            file_ids=file_ids,
        )
    return entity_id


def add_entity_aliases(
    conn: sqlite3.Connection,
    *,
    entity_id: int,
    aliases: list[str],
) -> None:
    """Append new aliases to an existing entity (dedup, lowercase).

    Used when alias-merge logic decides a new surface form refers to an
    existing canonical entity; we do not bump mention_count here because
    the caller still issues a regular ``upsert_entity`` for the canonical
    name to maintain that counter and the standard scope-merging behavior.
    """
    if not aliases:
        return
    row = conn.execute(
        "SELECT name, aliases FROM memory_entities WHERE id=?",
        (entity_id,),
    ).fetchone()
    if row is None:
        return
    canonical = row["name"]
    merged = _decode_aliases(row["aliases"])
    seen = set(merged)
    appended = False
    for a in _normalize_aliases(aliases, exclude=canonical):
        if a not in seen:
            merged.append(a)
            seen.add(a)
            appended = True
    if not appended:
        return
    conn.execute(
        "UPDATE memory_entities SET aliases=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (_encode_aliases(merged), entity_id),
    )


def get_entity_by_name(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    name: str,
) -> dict | None:
    """Get the first entity matching user_id + name (any type). Name is case-insensitive."""
    name = name.strip().lower()
    row = conn.execute(
        f"SELECT {_ENTITY_BASE_COLS_E}, {_ENTITY_SCOPE_COLS_E} "
        "FROM memory_entities e WHERE e.user_id=? AND e.name=? LIMIT 1",
        (user_id, name),
    ).fetchone()
    if row is None:
        return None
    record = dict(row)
    record["aliases"] = _decode_aliases(record.get("aliases"))
    return record


def get_entity_by_id(
    conn: sqlite3.Connection,
    entity_id: int,
) -> dict | None:
    """Get entity by primary key."""
    row = conn.execute(
        f"SELECT {_ENTITY_BASE_COLS_E}, {_ENTITY_SCOPE_COLS_E} FROM memory_entities e WHERE e.id=?",
        (entity_id,),
    ).fetchone()
    if row is None:
        return None
    record = dict(row)
    record["aliases"] = _decode_aliases(record.get("aliases"))
    return record


def list_entities(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    entity_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """List entities for a user, ordered by mention_count desc."""
    if entity_type:
        rows = conn.execute(
            f"""SELECT {_ENTITY_BASE_COLS_E}, {_ENTITY_SCOPE_COLS_E}
                FROM memory_entities e
                WHERE e.user_id=? AND e.entity_type=?
                ORDER BY e.mention_count DESC, e.updated_at DESC LIMIT ? OFFSET ?""",
            (user_id, entity_type, limit, offset),
        ).fetchall()
    else:
        rows = conn.execute(
            f"""SELECT {_ENTITY_BASE_COLS_E}, {_ENTITY_SCOPE_COLS_E}
                FROM memory_entities e
                WHERE e.user_id=?
                ORDER BY e.mention_count DESC, e.updated_at DESC LIMIT ? OFFSET ?""",
            (user_id, limit, offset),
        ).fetchall()
    records: list[dict] = []
    for r in rows:
        record = dict(r)
        record["aliases"] = _decode_aliases(record.get("aliases"))
        records.append(record)
    return records


def count_entities(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    entity_type: str | None = None,
) -> int:
    """Count entities for a user using the same optional filter as ``list_entities``."""
    if entity_type:
        row = conn.execute(
            "SELECT COUNT(*) FROM memory_entities WHERE user_id=? AND entity_type=?",
            (user_id, entity_type),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) FROM memory_entities WHERE user_id=?",
            (user_id,),
        ).fetchone()
    return int(row[0])


def delete_entity(conn: sqlite3.Connection, *, entity_id: int) -> None:
    """Delete entity by id (cascades to relations)."""
    conn.execute("DELETE FROM memory_entities WHERE id=?", (entity_id,))


# ---------------------------------------------------------------------------
# Knowledge Graph: Relations
# ---------------------------------------------------------------------------


def upsert_relation(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    subject_id: int,
    predicate: str,
    object_id: int,
    confidence: float = 1.0,
    source_session: str | None = None,
    evidence_message_ids: list[int] | None = None,
    valid_from: str | None = None,
    valid_to: str | None = None,
) -> None:
    """Insert or ignore a knowledge-graph relation. Self-loops are silently skipped."""
    if subject_id == object_id:
        return
    conn.execute(
        """INSERT INTO memory_relations
               (user_id, subject_id, predicate, object_id, confidence, source_session,
                evidence_message_ids, valid_from, valid_to, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(user_id, subject_id, predicate, object_id) DO UPDATE SET
               confidence=excluded.confidence,
               source_session=COALESCE(excluded.source_session, source_session),
               evidence_message_ids=COALESCE(excluded.evidence_message_ids, evidence_message_ids),
               valid_from=COALESCE(excluded.valid_from, valid_from),
               valid_to=excluded.valid_to,
               updated_at=CURRENT_TIMESTAMP""",
        (
            user_id,
            subject_id,
            predicate,
            object_id,
            max(0.0, min(1.0, confidence)),
            source_session,
            json.dumps(evidence_message_ids) if evidence_message_ids else None,
            valid_from,
            valid_to,
        ),
    )


def list_entity_relations(
    conn: sqlite3.Connection,
    *,
    entity_id: int,
    limit: int = 20,
) -> list[dict]:
    """List all relations where entity is subject or object.

    Returns dicts with: predicate, other_id, other_name, other_type, direction.
    """
    rows = conn.execute(
        """SELECT r.predicate, r.object_id AS other_id,
                  e.name AS other_name, e.entity_type AS other_type,
                  r.confidence, r.evidence_message_ids, r.valid_from, r.valid_to,
                  'outgoing' AS direction
           FROM memory_relations r
           JOIN memory_entities e ON e.id = r.object_id
           WHERE r.subject_id = ?
           UNION ALL
           SELECT r.predicate, r.subject_id AS other_id,
                  e.name AS other_name, e.entity_type AS other_type,
                  r.confidence, r.evidence_message_ids, r.valid_from, r.valid_to,
                  'incoming' AS direction
           FROM memory_relations r
           JOIN memory_entities e ON e.id = r.subject_id
           WHERE r.object_id = ?
           LIMIT ?""",
        (entity_id, entity_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def reassign_entity_relations(
    conn: sqlite3.Connection,
    *,
    source_id: int,
    target_id: int,
    user_id: str,
) -> None:
    """Redirect all relations from source entity to target entity."""
    conn.execute(
        "UPDATE OR IGNORE memory_relations SET subject_id=? WHERE subject_id=? AND user_id=?",
        (target_id, source_id, user_id),
    )
    conn.execute(
        "UPDATE OR IGNORE memory_relations SET object_id=? WHERE object_id=? AND user_id=?",
        (target_id, source_id, user_id),
    )
