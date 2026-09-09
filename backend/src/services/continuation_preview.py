"""Read-only comparison of saved session context with current authoritative state."""

import json

from ..core import database as db
from ..core.memory_policy import is_active_memory


def build_continuation_preview(conn, session: dict, user_id: str) -> dict:
    try:
        state = json.loads(session.get("task_state_json") or "{}")
        if not isinstance(state, dict):
            state = {}
    except (ValueError, TypeError):
        state = {}
    refs = state.get("retrieved_source_refs") or []
    refs = [ref for ref in refs if isinstance(ref, dict)] if isinstance(refs, list) else []
    scope = state.get("active_scope") or state.get("scope") or state.get("effective_scope") or {}
    scope = scope if isinstance(scope, dict) else {}
    scoped_files = scope.get("file_ids", state.get("file_ids", []))
    scoped_files = scoped_files if isinstance(scoped_files, list) else []
    files = []
    seen = set()
    for ref in [
        *refs,
        *({"file_id": value} for value in scoped_files),
    ]:
        file_id = ref.get("file_id")
        if type(file_id) is not int or file_id <= 0 or file_id in seen:
            continue
        seen.add(file_id)
        current = db.get_meeting_file(conn, file_id, user_id=user_id)
        status = "unverified"
        if current is None:
            status = "deleted"
        elif current.get("approval_status") == "rejected":
            status = "rejected"
        else:
            comparisons = [
                (ref.get("content_hash"), current.get("content_hash")),
                (ref.get("index_generation"), current.get("active_index_generation")),
            ]
            known = [(old, new) for old, new in comparisons if old is not None]
            if known:
                status = "unchanged" if all(old == new for old, new in known) else "changed"
        files.append(
            {"file_id": file_id, "file_name": (current or {}).get("file_name"), "status": status}
        )
    versions = state.get("recalled_memory_versions") or []
    versions = (
        [row for row in versions if isinstance(row, dict) and isinstance(row.get("key"), str)]
        if isinstance(versions, list)
        else []
    )
    memories = (
        db.get_memories_batch(conn, user_id=user_id, keys=[row["key"] for row in versions])
        if versions
        else {}
    )
    changes = []
    for saved in versions:
        current = memories.get(saved["key"])
        if (
            current is None
            or not is_active_memory(current)
            or current.get("revision") != saved.get("revision")
        ):
            changes.append(
                {
                    "key": saved["key"],
                    "saved_revision": saved.get("revision"),
                    "current_revision": (current or {}).get("revision"),
                    "status": "deleted"
                    if current is None
                    else "changed"
                    if is_active_memory(current)
                    else "inactive",
                }
            )
    checkpoint = conn.execute(
        "SELECT through_message_id FROM chat_context_checkpoints WHERE session_id=?",
        (session["id"],),
    ).fetchone()
    count = conn.execute(
        "SELECT COUNT(*) FROM chat_messages WHERE session_id=? AND id>?",
        (session["id"], checkpoint[0] if checkpoint else 0),
    ).fetchone()[0]
    from .chain._steps_session import _snapshot_checksum

    frozen = state.get("frozen_snapshot")
    snapshot_available = (
        isinstance(frozen, dict)
        and frozen.get("schema_version") == 1
        and frozen.get("sha256") == _snapshot_checksum(frozen)
        and isinstance(frozen.get("documents"), list)
        and isinstance(frozen.get("combined_context"), str)
        and type(frozen.get("source_ai_message_id")) is int
        and conn.execute(
            "SELECT 1 FROM chat_messages WHERE id=? AND session_id=? AND role='ai'",
            (frozen["source_ai_message_id"], session["id"]),
        ).fetchone()
        is not None
    )
    return {
        "session_id": session["id"],
        "scope": scope,
        "files": files,
        "memory_changes": changes,
        "open_questions": [q for q in state.get("open_questions", []) if isinstance(q, str)]
        if isinstance(state.get("open_questions"), list)
        else [],
        "saved_snapshot_available": snapshot_available,
        "checkpoint_available": checkpoint is not None,
        "messages_since_checkpoint": count,
        "notice": "Preview only. Scope, ownership and evidence revisions "
        "are checked again when continuing.",
    }
