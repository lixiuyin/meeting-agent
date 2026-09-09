"""Memory management API - CRUD for long-term user memory with semantic search"""

import asyncio
import base64
import datetime
import hashlib
import json
import logging
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field, PositiveInt

from ...api.dependencies import (
    MAX_PAGE_OFFSET,
    IdempotencyGuard,
    decode_cursor,
    encode_cursor,
    idempotency_key_header,
)
from ...api.middleware import limiter
from ...core import database as db
from ...core.audit import audit_log
from ...core.database import get_write_connection
from ...core.security import _derive_user_id_from_api_key, is_dev_user, verify_api_key
from ...models.schemas import (
    BatchDeleteResponse,
    EntityBatchDeleteRequest,
    EntityListResponse,
    EntityWithRelationsResponse,
    MemoryBatchDeleteRequest,
    MemoryBatchImportRequest,
    MemoryBatchImportResponse,
    MemoryConflictResolveRequest,
    MemoryConflictResolveResponse,
    MemoryDecayResponse,
    MemoryExportResponse,
    MemoryFeedbackRequest,
    MemoryFeedbackResponse,
    MemoryListResponse,
    MemoryResponse,
    MemorySearchRequest,
    MemorySearchResponse,
    MemorySearchResultItem,
    MemorySetRequest,
    MemoryUpdateRequest,
    MemoryVersionResponse,
    MessageResponse,
)
from ...models.schemas.fact_query import (
    FactChangesRequest,
    FactChangesResponse,
    FactQueryRequest,
    FactQueryResponse,
)
from ...services.knowledge_graph import kg_service
from ...services.memory import memory_service

router = APIRouter(prefix="/memory", tags=["memory"], dependencies=[Depends(verify_api_key)])
logger = logging.getLogger(__name__)


def _db_timestamp(value: datetime.datetime | None) -> str | None:
    return value.astimezone(datetime.UTC).isoformat() if value is not None else None


def _encode_memory_cursor(row: dict) -> str:
    payload = json.dumps(
        [float(row.get("salience", 3)), str(row["updated_at"]), str(row["key"])],
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def _decode_memory_cursor(cursor: str) -> tuple[float, str, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded).decode())
        if not isinstance(value, list) or len(value) != 3:
            raise ValueError
        return float(value[0]), str(value[1]), str(value[2])
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(422, "Invalid memory cursor") from exc


# ---------------------------------------------------------------------------
# Request/response models for entity endpoints
# ---------------------------------------------------------------------------


class EntityMergeRequest(BaseModel):
    source_names: list[str]
    target_name: str


class ProjectRequest(BaseModel):
    expected_revision: int = Field(0, ge=0)
    project_id: str = Field(min_length=1, max_length=120, pattern=r"^\S(?:.*\S)?$")
    name: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=50)
    file_ids: list[PositiveInt] = Field(default_factory=list, max_length=200)


@router.get("/projects")
async def project_directory(principal: dict = Depends(verify_api_key)):
    from ...core.database.projects import list_projects

    def read():
        with db.get_connection() as conn:
            return list_projects(conn, principal["user_id"])

    return await asyncio.to_thread(read)


@router.get("/projects/materials")
async def project_materials(
    q: str = Query("", max_length=200), principal: dict = Depends(verify_api_key)
):
    def read():
        with db.get_connection() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT f.id,f.file_name,f.meeting_id,m.title AS meeting_title "
                    "FROM meeting_files f "
                    "JOIN meetings m ON m.id=f.meeting_id WHERE m.user_id=? "
                    "AND (instr(lower(f.file_name),lower(?))>0 "
                    "OR instr(lower(m.title),lower(?))>0) "
                    "ORDER BY f.id DESC LIMIT 200",
                    (principal["user_id"], q, q),
                )
            ]

    return await asyncio.to_thread(read)


@router.put("/projects")
async def update_project(body: ProjectRequest, principal: dict = Depends(verify_api_key)):
    from ...core.database.projects import ProjectConflict, save_project

    def save():
        with get_write_connection() as conn:
            return save_project(
                conn,
                principal["user_id"],
                body.project_id,
                body.name,
                body.aliases,
                body.file_ids,
                expected_revision=body.expected_revision,
            )

    try:
        revision = await asyncio.to_thread(save)
    except ProjectConflict as exc:
        raise HTTPException(
            409, {"message": str(exc), "details": {"current": exc.current}}
        ) from exc
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"project_id": body.project_id, "revision": revision}


def _facts_snapshot(
    conn,
    user_id: str,
    body: BaseModel,
    *,
    stale_message: str = "Facts changed or pagination snapshot is missing; refresh the list",
) -> str:
    """Fence paging against content, ordering, lifecycle and clock-boundary changes."""
    digest = hashlib.sha256(
        json.dumps(
            [user_id, body.model_dump(mode="json", exclude={"snapshot", "limit", "offset"})],
            sort_keys=True,
        ).encode()
    )
    epoch = conn.execute(
        "SELECT epoch FROM memory_query_epochs WHERE user_id=?", (user_id,)
    ).fetchone()
    digest.update(str(epoch[0] if epoch else 0).encode())
    # Indexed predecessor lookups change only as a clock boundary is crossed.
    # This avoids scanning and hashing every fact on every requested page.
    for column in ("expires_at", "valid_from", "valid_to", "due_at"):
        row = conn.execute(
            f"SELECT MAX(julianday({column})) FROM user_memories "
            f"WHERE user_id=? AND julianday({column})<=julianday('now')",
            (user_id,),
        ).fetchone()
        digest.update(str(row[0]).encode())
    snapshot = digest.hexdigest()
    offset = int(getattr(body, "offset", 0))
    requested_snapshot = getattr(body, "snapshot", None)
    if (offset and not requested_snapshot) or (
        requested_snapshot and requested_snapshot != snapshot
    ):
        raise HTTPException(409, stale_message)
    return snapshot


def _fact_constraints(body: FactQueryRequest):
    from ...core.memory_query import ActionConstraints, parse_action_constraints

    parsed = parse_action_constraints(body.query)
    return ActionConstraints(
        tuple(body.action_status) or parsed.included,
        tuple(sorted(set(parsed.excluded) | ({"done", "cancelled"} if body.overdue else set()))),
        body.overdue or parsed.overdue,
    )


class ReviewQueryRequest(BaseModel):
    meeting_id: PositiveInt | None = None
    project_id: str | None = Field(None, max_length=200)
    limit: int = Field(25, ge=1, le=100)
    offset: int = Field(0, ge=0, le=MAX_PAGE_OFFSET)
    snapshot: str | None = Field(None, max_length=64)


class ReviewQueryResponse(BaseModel):
    items: list[MemoryResponse]
    conflicts: dict[str, list[MemoryResponse]] = Field(default_factory=dict)
    total: int
    next_offset: int | None
    snapshot: str
    extraction_progress: dict[str, int] = Field(default_factory=dict)


@router.post("/review/query", response_model=ReviewQueryResponse)
async def review_candidates(body: ReviewQueryRequest, principal: dict = Depends(verify_api_key)):
    """Human review of pending, conflicting and automatically confirmed assertions."""

    def read():
        from ...core.database.extraction_progress import extraction_progress
        from ...core.memory_admission import reference_memory_sql

        with db.get_connection() as conn, conn:
            if not conn.in_transaction:
                conn.execute("BEGIN")
            snapshot = _facts_snapshot(
                conn,
                principal["user_id"],
                body,
                stale_message="Review items changed; refresh the list",
            )
            clauses = [
                "m.user_id=?",
                "m.fact_type IN ('decision','action_item','project_fact')",
                "(m.assertion_status IN ('pending','disputed') OR "
                "(m.assertion_status='confirmed' "
                "AND m.source IN ('auto_extracted','consolidated')))",
                f"NOT {reference_memory_sql()}",
            ]
            values: list = [principal["user_id"]]
            if body.meeting_id:
                clauses.append(
                    "EXISTS (SELECT 1 FROM memory_scopes s WHERE s.memory_id=m.id "
                    "AND s.scope_type='meeting' AND s.scope_id=?)"
                )
                values.append(body.meeting_id)
            if body.project_id:
                clauses.append("m.project_id=?")
                values.append(body.project_id)
            where = " AND ".join(clauses)
            total = conn.execute(
                "SELECT COUNT(*) FROM user_memories m WHERE " + where, values
            ).fetchone()[0]
            keys = conn.execute(
                "SELECT m.key FROM user_memories m WHERE "
                + where
                + " ORDER BY m.updated_at DESC,m.key LIMIT ? OFFSET ?",
                [*values, body.limit, body.offset],
            ).fetchall()
            rows = [
                memory
                for row in keys
                if (
                    memory := db.get_memory_full(conn, user_id=principal["user_id"], key=row["key"])
                )
                is not None
            ]
            conflicts = {}
            for row in rows:
                keys = json.loads(row.get("conflicts_with") or "[]")
                conflicts[row["key"]] = [
                    other
                    for key in keys
                    if (other := db.get_memory_full(conn, user_id=principal["user_id"], key=key))
                ]
            return {
                "items": rows,
                "conflicts": conflicts,
                "total": total,
                "next_offset": body.offset + len(rows) if body.offset + len(rows) < total else None,
                "snapshot": snapshot,
                "extraction_progress": extraction_progress(
                    conn,
                    principal["user_id"],
                    meeting_id=body.meeting_id,
                    project_id=body.project_id,
                ),
            }

    return await asyncio.to_thread(read)


@router.post("/facts/query", response_model=FactQueryResponse)
async def query_recorded_facts(body: FactQueryRequest, principal: dict = Depends(verify_api_key)):
    """A paginated, revision-fenced set of authoritative facts for UI/export."""
    user_id = principal["user_id"]

    def query():
        with db.get_connection() as conn, conn:
            # End our read transaction before returning this pooled connection.
            if not conn.in_transaction:
                conn.execute("BEGIN")
            snapshot = _facts_snapshot(conn, user_id, body)
            rows, total = db.search_structured_memories(
                conn,
                user_id=user_id,
                fact_types=list[str](body.fact_types),
                project_id=body.project_id,
                meeting_ids=body.meeting_ids,
                file_ids=body.file_ids,
                query_text=body.query,
                action_constraints=_fact_constraints(body),
                assignee=body.assignee,
                as_of=_db_timestamp(body.valid_at),
                known_at=_db_timestamp(body.known_at),
                limit=body.limit,
                offset=body.offset,
            )
            next_offset = body.offset + len(rows)
            return FactQueryResponse(
                items=[MemoryResponse.model_validate(row) for row in rows],
                total=total,
                returned=len(rows),
                next_offset=next_offset if next_offset < total else None,
                snapshot=snapshot,
                recorded_set_complete=body.offset == 0 and len(rows) == total,
                scope={
                    "project_id": body.project_id,
                    "meeting_ids": body.meeting_ids,
                    "file_ids": body.file_ids,
                    "valid_at": _db_timestamp(body.valid_at),
                    "known_at": _db_timestamp(body.known_at),
                },
            )

    return await asyncio.to_thread(query)


@router.post("/facts/changes", response_model=FactChangesResponse)
async def compare_recorded_facts(
    body: FactChangesRequest,
    principal: dict = Depends(verify_api_key),
):
    """Compare business-time states using one system-time cutoff and read transaction."""

    def compare():
        with db.get_connection() as conn, conn:
            if not conn.in_transaction:
                conn.execute("BEGIN")
            snapshot = _facts_snapshot(conn, principal["user_id"], body)
            states = []
            for cutoff in (body.before, body.after):
                rows, total = db.search_structured_memories(
                    conn,
                    user_id=principal["user_id"],
                    fact_types=list[str](body.fact_types),
                    project_id=body.project_id,
                    meeting_ids=body.meeting_ids,
                    file_ids=body.file_ids,
                    query_text=body.query,
                    action_constraints=_fact_constraints(body),
                    assignee=body.assignee,
                    as_of=_db_timestamp(cutoff),
                    known_at=_db_timestamp(body.known_at),
                    limit=10001,
                )
                if total > 10000:
                    raise HTTPException(
                        422, "Narrow the project or source scope to compare at most 10000 facts"
                    )
                states.append({row["key"]: row for row in rows})
            fields = (
                "value",
                "action_status",
                "assignee",
                "due_at",
                "project_id",
                "subject",
                "predicate",
                "object_value",
                "evidence_refs",
                "assertion_status",
            )
            changes = []
            for key in sorted(states[0].keys() | states[1].keys()):
                before, after = states[0].get(key), states[1].get(key)
                changed = [
                    name for name in fields if (before or {}).get(name) != (after or {}).get(name)
                ]
                if before is not None and after is not None and not changed:
                    continue
                changes.append(
                    {
                        "key": key,
                        "kind": "added"
                        if before is None
                        else "removed"
                        if after is None
                        else "changed",
                        "changed_fields": changed,
                        "before": before,
                        "after": after,
                    }
                )
            end = body.offset + body.limit
            return FactChangesResponse(
                items=changes[body.offset : end],
                total=len(changes),
                next_offset=end if end < len(changes) else None,
                snapshot=snapshot,
            )

    return await asyncio.to_thread(compare)


@router.get("", response_model=MemoryListResponse)
async def list_memories(
    principal: dict = Depends(verify_api_key),
    category: str | None = Query(None, description="Filter by category"),
    include_expired: bool = Query(False, description="Include expired memories"),
    limit: int = Query(50, ge=1, le=100, description="Maximum items to return"),
    cursor: str | None = Query(None),
    offset: int | None = Query(None, ge=0, le=MAX_PAGE_OFFSET),
    q: str | None = Query(None, max_length=500, description="Literal key/value search"),
    fact_type: Literal["fact", "preference", "project_fact", "decision", "action_item"]
    | None = Query(None),
    assertion_status: Literal["pending", "confirmed", "disputed", "superseded", "retracted"]
    | None = Query(None),
    project_id: str | None = Query(None, max_length=200),
    memory_kind: Literal["all", "personal", "reference"] = Query("all"),
):
    """List all stored memories for a user, optionally filtered by category."""
    resolved_offset = offset or 0
    after = _decode_memory_cursor(cursor) if cursor else None

    def _fetch():
        with db.get_connection() as conn:
            items, total = db.list_and_count_memories(
                conn,
                user_id=principal["user_id"],
                include_expired=include_expired,
                category=category,
                limit=limit + 1,
                offset=resolved_offset,
                after=after,
                text_query=q.strip() if q and q.strip() else None,
                fact_type=fact_type,
                assertion_status=assertion_status,
                project_id=project_id,
                memory_kind=memory_kind,
            )
            return items, total

    memories, total = await asyncio.to_thread(_fetch)
    has_next = len(memories) > limit
    page = memories[:limit]
    next_cursor = _encode_memory_cursor(page[-1]) if has_next and page else None
    items = [MemoryResponse(**m) for m in page]
    return MemoryListResponse(items=items, next_cursor=next_cursor, total=total, memories=items)


@router.post("", response_model=MemoryResponse)
async def set_memory(
    request: Request,
    body: MemorySetRequest,
    principal: dict = Depends(verify_api_key),
    idempotency_key: str | None = Depends(idempotency_key_header),
):
    """Store a key-value memory with importance, category, and optional TTL."""
    guard = IdempotencyGuard(idempotency_key, request, principal["user_id"])
    cached = await guard.check()
    if cached:
        return MemoryResponse(**cached)

    expires_at: str | None = None
    if body.expires_in_days is not None:
        if body.expires_in_days == -1:
            expires_at = None  # Never expires
        else:
            expires_at = (
                datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=body.expires_in_days)
            ).strftime("%Y-%m-%d %H:%M:%S")

    def _write():
        with get_write_connection() as conn:
            db.set_memory(
                conn,
                user_id=principal["user_id"],
                key=body.key,
                value=body.value,
                importance=body.importance,
                category=body.category,
                expires_at=expires_at,
                confidence=body.confidence,
                valid_from=_db_timestamp(body.valid_from),
                valid_to=_db_timestamp(body.valid_to),
                fact_type=body.fact_type,
                assertion_status=body.assertion_status,
                project_id=body.project_id,
                action_status=body.action_status,
                assignee=body.assignee,
                due_at=_db_timestamp(body.due_at),
            )
            conn.execute(
                "UPDATE user_memories SET vector_state=? WHERE user_id=? AND key=?",
                (
                    "pending" if body.assertion_status == "confirmed" else "inactive",
                    principal["user_id"],
                    body.key,
                ),
            )
            row = db.get_memory_full(conn, user_id=principal["user_id"], key=body.key)
            if row is None:
                raise RuntimeError("Memory disappeared during transactional creation")
            response = MemoryResponse(**row)
            guard.save_in_transaction(conn, response.model_dump(mode="json"))
            return response

    response = await asyncio.to_thread(_write)
    await guard.finish_transaction()
    from ...services.memory._service._index_sync import wake_memory_index_reconcile

    wake_memory_index_reconcile()
    await asyncio.to_thread(memory_service._enforce_memory_cap, principal["user_id"])
    audit_log("set", "memory", body.key, user_id=principal["user_id"])
    return response


@router.post("/resolve-conflict", response_model=MemoryConflictResolveResponse)
async def resolve_memory_conflict(
    body: MemoryConflictResolveRequest,
    principal: dict = Depends(verify_api_key),
):
    """Choose one disputed fact and atomically invalidate its declared alternatives."""
    user_id = principal["user_id"]

    def _resolve() -> tuple[MemoryResponse, list[str]]:
        try:
            with get_write_connection() as conn:
                superseded = db.resolve_memory_conflict(
                    conn,
                    user_id=user_id,
                    winner_key=body.winner_key,
                    expected_revision=body.expected_revision,
                    conflicting_keys=body.conflicting_keys,
                    expected_conflict_revisions=(
                        body.expected_conflict_revisions
                        if "expected_conflict_revisions" in body.model_fields_set
                        else None
                    ),
                )
                row = db.get_memory_full(conn, user_id=user_id, key=body.winner_key)
                if row is None:
                    raise RuntimeError("Conflict winner disappeared during resolution")
                return MemoryResponse(**row), superseded
        except db.MemoryRevisionConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    winner, superseded = await asyncio.to_thread(_resolve)
    from ...services.memory._service._index_sync import wake_memory_index_reconcile

    wake_memory_index_reconcile()
    audit_log("resolve_conflict", "memory", body.winner_key, user_id=user_id)
    return MemoryConflictResolveResponse(winner=winner, superseded_keys=superseded)


@router.post("/batch-delete", response_model=BatchDeleteResponse)
async def batch_delete_memories(
    body: MemoryBatchDeleteRequest,
    principal: dict = Depends(verify_api_key),
):
    """Delete up to 100 memories without one rate-limited request per row."""
    deleted, missing = await asyncio.to_thread(
        memory_service.delete_many,
        principal["user_id"],
        body.keys,
    )
    audit_log(
        "batch_delete",
        "memory",
        f"{len(deleted)} items",
        user_id=principal["user_id"],
    )
    return BatchDeleteResponse(deleted=len(deleted), missing=missing)


@router.delete("", response_model=MessageResponse)
@limiter.limit("10/minute")
async def delete_memory(request: Request, key: str, principal: dict = Depends(verify_api_key)):
    """Delete a specific memory."""
    await asyncio.to_thread(memory_service.delete, principal["user_id"], key)
    audit_log("delete", "memory", key, user_id=principal["user_id"])
    return MessageResponse(message="Memory deleted")


@router.put("", response_model=MemoryResponse)
@limiter.limit("10/minute")
async def update_memory(
    request: Request,
    body: MemoryUpdateRequest,
    principal: dict = Depends(verify_api_key),
    idempotency_key: str | None = Depends(idempotency_key_header),
):
    """Update value, importance, or category of an existing memory."""
    guard = IdempotencyGuard(idempotency_key, request, principal["user_id"])
    cached = await guard.check()
    if cached:
        return MemoryResponse(**cached)

    def _update():
        with get_write_connection() as conn:
            updated = db.update_memory(
                conn,
                user_id=principal["user_id"],
                key=body.key,
                value=body.value,
                importance=body.importance,
                category=body.category,
                confidence=body.confidence,
                valid_from=_db_timestamp(body.valid_from),
                valid_to=_db_timestamp(body.valid_to),
                expected_revision=body.expected_revision,
                fact_type=body.fact_type,
                assertion_status=body.assertion_status,
                project_id=body.project_id,
                action_status=body.action_status,
                assignee=body.assignee,
                due_at=_db_timestamp(body.due_at),
                fields=set(body.model_fields_set),
            )
            if not updated:
                current = db.get_memory_full(conn, user_id=principal["user_id"], key=body.key)
                if current:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "Memory was modified by another client "
                            f"(expected revision {body.expected_revision}, "
                            f"current revision {current['revision']})"
                        ),
                        headers={"X-Current-Revision": str(current["revision"])},
                    )
                raise HTTPException(status_code=404, detail="Memory not found")
            row = db.get_memory_full(conn, user_id=principal["user_id"], key=body.key)
            if row is None:
                raise RuntimeError("Memory disappeared during transactional update")
            response = MemoryResponse(**row)
            guard.save_in_transaction(conn, response.model_dump(mode="json"))
            return response

    response = await asyncio.to_thread(_update)
    await guard.finish_transaction()
    from ...services.memory._service._index_sync import wake_memory_index_reconcile

    wake_memory_index_reconcile()
    audit_log("update", "memory", body.key, user_id=principal["user_id"])
    return response


@router.get("/versions", response_model=list[MemoryVersionResponse])
async def list_memory_versions(
    key: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(50, ge=1, le=100),
    principal: dict = Depends(verify_api_key),
):
    """Return immutable lifecycle snapshots for a memory fact."""
    with db.get_connection() as conn:
        current = db.get_memory_full(conn, user_id=principal["user_id"], key=key)
        if current is None:
            raise HTTPException(status_code=404, detail="Memory not found")
        rows = db.list_memory_versions(conn, user_id=principal["user_id"], key=key, limit=limit)
    return [MemoryVersionResponse(**row) for row in rows]


@router.post("/batch", response_model=MemoryBatchImportResponse)
async def batch_import_memories(
    request: Request,
    body: MemoryBatchImportRequest,
    principal: dict = Depends(verify_api_key),
    idempotency_key: str | None = Depends(idempotency_key_header),
):
    """Bulk import up to 100 memories for a user. Existing keys are updated (upsert).

    Each request accepts at most 100 items. Facts are committed first with a
    durable pending-index state; versioned vector publication happens only
    afterwards. A provider outage therefore cannot create an authoritative
    orphan vector or roll back an otherwise valid fact import.
    """
    guard = IdempotencyGuard(idempotency_key, request, principal["user_id"])
    cached = await guard.check()
    if cached:
        return MemoryBatchImportResponse(**cached)

    user_id = principal["user_id"]
    now = datetime.datetime.now(datetime.UTC)

    # ── Phase 1: Validate & prepare all items ────────────────────────────
    seen_keys: set[str] = set()
    prepared: list[dict] = []
    for item in body.memories:
        # Detect duplicate keys within the same batch so the caller knows
        # which value will win (last-write-wins) instead of silently
        # discarding earlier rows (M-H4).
        if item.key in seen_keys:
            logger.warning(
                "Batch import contains duplicate key '%s'; only the last occurrence "
                "will be persisted.",
                item.key,
            )
        seen_keys.add(item.key)
        expires_at: str | None = None
        # Prefer absolute expires_at (from re-imported exports) over relative
        # expires_in_days so TTLs survive round-tripping.
        if item.expires_at:
            expires_at = _db_timestamp(item.expires_at)
        elif item.expires_in_days is not None and item.expires_in_days != -1:
            expires_at = (now + datetime.timedelta(days=item.expires_in_days)).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        prepared.append(
            {
                "key": item.key,
                "value": item.value,
                "importance": item.importance,
                "category": item.category,
                "confidence": item.confidence,
                "fact_type": item.fact_type,
                "assertion_status": item.assertion_status,
                "project_id": item.project_id,
                "subject": item.subject,
                "predicate": item.predicate,
                "object_value": item.object_value,
                "action_status": item.action_status,
                "assignee": item.assignee,
                "due_at": _db_timestamp(item.due_at),
                "evidence_message_ids": item.evidence_message_ids,
                "evidence_excerpt": item.evidence_excerpt,
                "evidence_refs": item.evidence_refs,
                "conflicts_with": item.conflicts_with,
                "meeting_ids": item.meeting_ids,
                "file_ids": item.file_ids,
                "valid_from": _db_timestamp(item.valid_from),
                "valid_to": _db_timestamp(item.valid_to),
                "expires_at": expires_at,
            }
        )

    # ── Phase 2: Single SQL transaction for all authoritative writes ─────
    imported = 0
    errors: list[str] = []
    written_keys: list[str] = []

    def _write_all():
        nonlocal imported
        with get_write_connection() as conn:
            for item in prepared:
                try:
                    db.set_memory(
                        conn,
                        user_id=user_id,
                        key=item["key"],
                        value=item["value"],
                        source="batch_import",
                        importance=item["importance"],
                        expires_at=item["expires_at"],
                        category=item["category"],
                        embedding_id=None,
                        confidence=item["confidence"],
                        fact_type=item["fact_type"],
                        assertion_status=item["assertion_status"],
                        project_id=item["project_id"],
                        subject=item["subject"],
                        predicate=item["predicate"],
                        object_value=item["object_value"],
                        action_status=item["action_status"],
                        assignee=item["assignee"],
                        due_at=item["due_at"],
                        evidence_message_ids=item["evidence_message_ids"],
                        evidence_excerpt=item["evidence_excerpt"],
                        evidence_refs=item["evidence_refs"],
                        conflicts_with=item["conflicts_with"],
                        meeting_ids=item["meeting_ids"],
                        file_ids=item["file_ids"],
                        valid_from=item["valid_from"],
                        valid_to=item["valid_to"],
                    )
                    written_keys.append(item["key"])
                    imported += 1
                except Exception as exc:
                    errors.append(f"{item['key']}: {exc}")
                    logger.warning(
                        "Batch import SQL write failed for key %s: %s",
                        item["key"],
                        exc,
                        exc_info=True,
                    )

    await asyncio.to_thread(_write_all)

    # ── Phase 3: Wake durable publication without waiting for a provider ──
    from ...services.memory._service._index_sync import wake_memory_index_reconcile

    if written_keys:
        wake_memory_index_reconcile()

    audit_log("batch_import", "memory", f"{imported} items", user_id=user_id)
    response = MemoryBatchImportResponse(imported=imported, failed=len(errors), errors=errors)

    # Only cache on full success — partial failures should allow retries
    if not errors:
        await guard.save(response.model_dump(mode="json"))
    return response


@router.get("/export", response_model=MemoryExportResponse)
async def export_memories(
    principal: dict = Depends(verify_api_key),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    include_expired: bool = Query(False),
    limit: int = Query(50, ge=1, le=100, description="Maximum items to export"),
    cursor: str | None = Query(None),
    offset: int | None = Query(None, ge=0, le=MAX_PAGE_OFFSET),
):
    """Export one page of memories as JSON suitable for re-import via ``/batch``."""
    resolved_offset = offset or 0
    after = _decode_memory_cursor(cursor) if cursor else None
    resolved_user_id = principal["user_id"]
    if is_dev_user(resolved_user_id) and x_api_key:
        resolved_user_id = _derive_user_id_from_api_key(x_api_key)

    def _fetch(export_user_id: str):
        with db.get_connection() as conn:
            return db.list_and_count_memories(
                conn,
                user_id=export_user_id,
                include_expired=include_expired,
                limit=limit + 1,
                offset=resolved_offset,
                after=after,
            )

    memories, total = await asyncio.to_thread(_fetch, resolved_user_id)
    # Backward compatibility in dev-mode tests that seeded legacy "default"
    # user records directly.
    if not memories and resolved_user_id != principal["user_id"]:
        memories, total = await asyncio.to_thread(_fetch, principal["user_id"])
    has_next = len(memories) > limit
    page = memories[:limit]
    export_items = [
        {
            "key": m["key"],
            "value": m["value"],
            "importance": m.get("importance", 3),
            "confidence": m.get("confidence", 1.0),
            "fact_type": m.get("fact_type", "fact"),
            "assertion_status": m.get("assertion_status", "confirmed"),
            "project_id": m.get("project_id"),
            "subject": m.get("subject"),
            "predicate": m.get("predicate"),
            "object_value": m.get("object_value"),
            "evidence_message_ids": m.get("evidence_message_ids"),
            "evidence_excerpt": m.get("evidence_excerpt"),
            "evidence_refs": m.get("evidence_refs"),
            "conflicts_with": m.get("conflicts_with"),
            "meeting_ids": m.get("meeting_ids"),
            "file_ids": m.get("file_ids"),
            "valid_from": m.get("valid_from"),
            "valid_to": m.get("valid_to"),
            "category": m.get("category"),
            "expires_at": m.get("expires_at"),  # Absolute TTL for round-tripping
        }
        for m in page
    ]
    response = MemoryExportResponse(
        user_id=resolved_user_id,
        total=total,
        memories=export_items,
        next_cursor=_encode_memory_cursor(page[-1]) if has_next and page else None,
    )
    return response


@router.post("/search", response_model=MemorySearchResponse)
async def search_memories_semantic(
    request: MemorySearchRequest, principal: dict = Depends(verify_api_key)
):
    """Semantic search over user memories using vector similarity."""
    from ...core.memory_search_filters import MemorySearchFilters

    filters = {}
    if request.memory_kind != "all" or request.fact_type or request.assertion_status:
        filters["filters"] = MemorySearchFilters(
            request.memory_kind, request.fact_type, request.assertion_status
        )
    if request.project_id:
        filters["project_ids"] = (request.project_id,)
    results = await memory_service.search_semantic(
        user_id=principal["user_id"],
        query=request.query,
        limit=request.limit,
        min_importance=request.min_importance,
        meeting_ids=request.meeting_ids,
        file_ids=request.file_ids,
        **filters,
    )
    return MemorySearchResponse(
        memories=[
            MemorySearchResultItem.model_validate(
                {
                    **r.metadata,
                    "meeting_ids": r.meeting_ids,
                    "file_ids": r.file_ids,
                    "is_legacy_scope": r.is_legacy_scope,
                    "key": r.key,
                    "revision": int(r.metadata.get("revision", 1)),
                    "value": r.value,
                    "importance": r.importance,
                    "salience": r.salience,
                    "confidence": r.confidence,
                    "freshness_score": r.freshness_score,
                    "usefulness_score": r.usefulness_score,
                    "usefulness_count": r.usefulness_count,
                    "category": r.category,
                    "source": r.source,
                    "last_accessed": r.last_accessed,
                    "access_count": r.access_count,
                    "expires_at": r.expires_at,
                    "updated_at": r.updated_at,
                    "combined_score": round(r.combined_score, 4),
                    "decay_score": round(r.decay_score, 4),
                }
            )
            for r in results
        ],
        total=len(results),
    )


@router.post("/retry-index", response_model=MemoryResponse)
@limiter.limit("10/minute")
async def retry_memory_index(request: Request, key: str, principal: dict = Depends(verify_api_key)):
    """Requeue this principal's current fact without modifying its revision."""
    from ...core.memory_policy import is_active_memory

    def requeue():
        with db.get_write_connection() as conn:
            row = db.get_memory_full(conn, user_id=principal["user_id"], key=key)
            if row is None:
                raise HTTPException(404, "Memory not found")
            if not is_active_memory(row):
                raise HTTPException(409, "Only current memories can be indexed")
            conn.execute(
                "UPDATE user_memories SET "
                "vector_state='pending',vector_attempts=0,vector_retry_at=NULL WHERE user_id=? "
                "AND key=?",
                (principal["user_id"], key),
            )
            return MemoryResponse(**{**row, "vector_state": "pending"})

    await asyncio.to_thread(requeue)
    from ...services.memory._service._index_sync import wake_memory_index_reconcile

    wake_memory_index_reconcile()

    def refreshed():
        with db.get_connection() as conn:
            row = db.get_memory_full(conn, user_id=principal["user_id"], key=key)
        if row is None:
            raise HTTPException(404, "Memory not found")
        return MemoryResponse(**row)

    return await asyncio.to_thread(refreshed)


@router.post("/decay", response_model=MemoryDecayResponse)
@limiter.limit("1/hour")
async def trigger_memory_decay(
    request: Request,
    principal: dict = Depends(verify_api_key),
    idempotency_key: str | None = Depends(idempotency_key_header),
):
    """Manually trigger importance decay for all user memories.

    M-8: Rate-limited to 1/hour to prevent abuse. The decay operation
    is O(N) in memory count and should not be triggered casually.
    """
    guard = IdempotencyGuard(idempotency_key, request, principal["user_id"])
    cached = await guard.check()
    if cached:
        return MemoryDecayResponse(**cached)

    count = await asyncio.to_thread(memory_service.decay_memories, principal["user_id"])
    response = MemoryDecayResponse(decayed_count=count)
    await guard.save(response.model_dump(mode="json"))
    return response


@router.post("/feedback", response_model=MemoryFeedbackResponse)
@limiter.limit("30/minute")
async def record_memory_feedback(
    request: Request,
    body: MemoryFeedbackRequest,
    principal: dict = Depends(verify_api_key),
    idempotency_key: str | None = Depends(idempotency_key_header),
):
    """Record whether a recalled fact was useful for a downstream answer."""

    guard = IdempotencyGuard(idempotency_key, request, principal["user_id"])
    cached = await guard.check()
    if cached:
        return MemoryFeedbackResponse(**cached)

    def _record() -> dict[str, float | int] | None:
        with get_write_connection() as conn:
            return db.record_memory_usefulness(
                conn,
                user_id=principal["user_id"],
                key=body.key,
                useful=body.useful,
            )

    updated = await asyncio.to_thread(_record)
    if updated is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    audit_log(
        "feedback",
        "memory",
        body.key,
        user_id=principal["user_id"],
        detail=f"useful={body.useful}",
    )
    response = MemoryFeedbackResponse(
        message="Memory feedback recorded",
        key=body.key,
        usefulness_score=float(updated["usefulness_score"]),
        usefulness_count=int(updated["usefulness_count"]),
    )
    await guard.save(response.model_dump(mode="json"))
    return response


# ---------------------------------------------------------------------------
# Knowledge Graph entity endpoints
# ---------------------------------------------------------------------------


@router.get("/entities", response_model=EntityListResponse)
async def list_entities(
    principal: dict = Depends(verify_api_key),
    entity_type: str | None = Query(None, description="Filter by entity type"),
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = Query(None),
    offset: int | None = Query(None, ge=0, le=MAX_PAGE_OFFSET),
):
    """List knowledge-graph entities for a user, sorted by mention count."""
    resolved_offset = decode_cursor(cursor) if cursor else (offset or 0)

    def _fetch_entities():
        with db.get_connection() as conn:
            entities = db.list_entities(
                conn,
                user_id=principal["user_id"],
                entity_type=entity_type,
                limit=limit + 1,
                offset=resolved_offset,
            )
            total = db.count_entities(
                conn,
                user_id=principal["user_id"],
                entity_type=entity_type,
            )
            return entities, total

    entities, total = await asyncio.to_thread(_fetch_entities)
    has_next = len(entities) > limit
    page = entities[:limit]
    from ...models.schemas import EntityResponse

    return EntityListResponse(
        entities=[EntityResponse(**e) for e in page],
        total=total,
        next_cursor=encode_cursor(resolved_offset + limit) if has_next else None,
    )


@router.post("/entities/batch-delete", response_model=BatchDeleteResponse)
async def batch_delete_entities(
    body: EntityBatchDeleteRequest,
    principal: dict = Depends(verify_api_key),
):
    """Delete up to 100 entities and their cascading relations in one request."""
    deleted, missing = await asyncio.to_thread(
        kg_service.delete_entities,
        principal["user_id"],
        body.names,
    )
    audit_log(
        "batch_delete",
        "entity",
        f"{len(deleted)} items",
        user_id=principal["user_id"],
    )
    return BatchDeleteResponse(deleted=len(deleted), missing=missing)


@router.get("/entities/{name}", response_model=EntityWithRelationsResponse)
async def get_entity(
    name: str,
    principal: dict = Depends(verify_api_key),
):
    """Get an entity and all its direct relations."""
    name = name.strip().lower()
    result = kg_service.get_entity_with_relations(principal["user_id"], name)
    if not result:
        raise HTTPException(status_code=404, detail="Entity not found")
    return result


@router.delete("/entities/{name}", response_model=MessageResponse)
@limiter.limit("10/minute")
async def delete_entity(
    request: Request,
    name: str,
    principal: dict = Depends(verify_api_key),
):
    """Delete an entity and all its relations."""
    name = name.strip().lower()
    deleted = await asyncio.to_thread(kg_service.delete_entity, principal["user_id"], name)
    if not deleted:
        raise HTTPException(status_code=404, detail="Entity not found")
    audit_log("delete", "entity", name, user_id=principal["user_id"])
    return MessageResponse(message=f"Entity '{name}' deleted")


@router.post("/entities/merge", response_model=MessageResponse)
async def merge_entities(
    request: Request,
    body: EntityMergeRequest,
    principal: dict = Depends(verify_api_key),
    idempotency_key: str | None = Depends(idempotency_key_header),
):
    """Merge source entities into a target entity, reassigning all relations."""
    guard = IdempotencyGuard(idempotency_key, request, principal["user_id"])
    cached = await guard.check()
    if cached:
        return MessageResponse(**cached)

    ok = await asyncio.to_thread(
        kg_service.merge_entities,
        principal["user_id"],
        source_names=body.source_names,
        target_name=body.target_name,
    )
    if not ok:
        raise HTTPException(status_code=400, detail="Merge failed: target entity not found")
    audit_log("merge", "entity", body.target_name, user_id=principal["user_id"])
    response = MessageResponse(
        message=f"Merged {len(body.source_names)} entities into '{body.target_name}'"
    )
    await guard.save(response.model_dump(mode="json"))
    return response
