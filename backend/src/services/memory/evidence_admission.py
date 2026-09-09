"""Validate document-derived facts before they can enter model context."""

import json

from ...core import database as db
from ...core.memory_admission import file_memory_policy
from ...core.source_revision_fence import meeting_file_source_tokens
from ...core.untrusted_material import has_embedded_directive
from ...models.schemas.evidence import EvidenceLocationRequest
from ..evidence_location import resolve_evidence_location


def _list(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return []
    return value if isinstance(value, list) else []


def admissible_memories(conn, entries: list[dict], user_id: str) -> list[dict]:
    accepted, files = [], {}
    for entry in entries:
        # User-authored state has its own authority. Do not retroactively erase
        # a manual correction because its optional supporting document changed.
        automatic = entry.get("source") in {"auto_extracted", "consolidated"}
        if not automatic:
            accepted.append(entry)
            continue
        if has_embedded_directive(entry.get("value")) or has_embedded_directive(
            entry.get("evidence_excerpt")
        ):
            continue
        refs = _list(entry.get("evidence_refs"))
        if not refs:
            if not _list(entry.get("file_ids")):
                accepted.append(entry)  # conversational, not document-derived
            continue
        for ref in refs:
            if not isinstance(ref, dict) or type(ref.get("file_id")) is not int:
                continue
            fid = ref["file_id"]
            if fid not in files:
                files[fid] = db.get_meeting_file(conn, fid, user_id=user_id)
            file = files[fid]
            if not file or file_memory_policy(file) != "project_state":
                continue
            if ref.get("meeting_id") not in (None, file["meeting_id"]):
                continue
            revisions = meeting_file_source_tokens(file)
            if ref.get("source_revision") and str(ref["source_revision"]) not in revisions:
                continue
            if not entry.get("evidence_excerpt"):
                continue
            try:
                location = resolve_evidence_location(
                    file.get("transcript") or "",
                    {"kind": "text"},
                    EvidenceLocationRequest(
                        excerpt=entry["evidence_excerpt"],
                        window_start=ref.get("window_start"),
                        window_end=ref.get("window_end"),
                    ),
                )
            except ValueError:
                continue
            if location["status"] == "exact":
                accepted.append(entry)
                break
    return accepted


def filter_context_memories(entries: list[dict], user_id: str) -> list[dict]:
    with db.get_connection() as conn:
        return admissible_memories(conn, entries, user_id)


def requalify_file_memories(conn, user_id: str, file_id: int) -> list[str]:
    """Move unsupported automatic facts to review; retain immutable history."""
    from ...core.database.memories import (
        _close_recorded_validity,
        _database_now,
        _record_memory_version,
        write_memory_audit,
    )

    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT DISTINCT m.* FROM user_memories m JOIN memory_scopes s ON s.memory_id=m.id "
            "WHERE m.user_id=? AND s.scope_type='file' AND s.scope_id=? "
            "AND m.source IN ('auto_extracted','consolidated') AND m.assertion_status='confirmed'",
            (user_id, file_id),
        )
    ]
    valid = {r["key"] for r in admissible_memories(conn, rows, user_id)}
    changed = []
    for row in rows:
        if row["key"] in valid:
            continue
        _record_memory_version(conn, row["id"])
        now = _database_now(conn)
        conn.execute(
            "UPDATE user_memories SET assertion_status='pending', revision=revision+1, "
            "vector_state='inactive', updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (row["id"],),
        )
        _close_recorded_validity(conn, memory_id=row["id"], revision=row["revision"], valid_to=now)
        _record_memory_version(conn, row["id"])
        write_memory_audit(
            conn,
            user_id=user_id,
            memory_key=row["key"],
            action="source_requires_review",
            detail=f"file_id={file_id}",
        )
        changed.append(row["key"])
    return changed
