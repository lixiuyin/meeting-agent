"""Memory management API - CRUD for long-term user memory with semantic search"""

import asyncio
import datetime
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel

from ...api.dependencies import (
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
    EntityListResponse,
    EntityWithRelationsResponse,
    MemoryBatchImportRequest,
    MemoryBatchImportResponse,
    MemoryDecayResponse,
    MemoryExportResponse,
    MemoryListResponse,
    MemoryResponse,
    MemorySearchRequest,
    MemorySearchResponse,
    MemorySearchResultItem,
    MemorySetRequest,
    MemoryUpdateRequest,
    MessageResponse,
)
from ...services.knowledge_graph import kg_service
from ...services.memory import memory_service

router = APIRouter(prefix="/memory", tags=["memory"], dependencies=[Depends(verify_api_key)])
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request/response models for entity endpoints
# ---------------------------------------------------------------------------


class EntityMergeRequest(BaseModel):
    source_names: list[str]
    target_name: str


@router.get("", response_model=MemoryListResponse)
async def list_memories(
    principal: dict = Depends(verify_api_key),
    category: str | None = Query(None, description="Filter by category"),
    include_expired: bool = Query(False, description="Include expired memories"),
    limit: int = Query(50, ge=1, le=100, description="Maximum items to return"),
    cursor: str | None = Query(None),
    offset: int | None = Query(None, ge=0),
):
    """List all stored memories for a user, optionally filtered by category."""
    resolved_offset = decode_cursor(cursor) if cursor else (offset or 0)

    def _fetch():
        with db.get_connection() as conn:
            items, total = db.list_and_count_memories(
                conn,
                user_id=principal["user_id"],
                include_expired=include_expired,
                category=category,
                limit=limit + 1,
                offset=resolved_offset,
            )
            return items, total

    memories, total = await asyncio.to_thread(_fetch)
    has_next = len(memories) > limit
    page = memories[:limit]
    next_cursor = encode_cursor(resolved_offset + limit) if has_next else None
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

    await asyncio.to_thread(
        memory_service.set,
        principal["user_id"],
        body.key,
        body.value,
        importance=body.importance,
        category=body.category,
        expires_at=expires_at,
    )
    audit_log("set", "memory", body.key, user_id=principal["user_id"])
    mems = await asyncio.to_thread(memory_service.list_all, principal["user_id"])
    for m in mems:
        if m["key"] == body.key:
            response = MemoryResponse(**m)
            await guard.save(response.model_dump(mode="json"))
            return response
    response = MemoryResponse(
        key=body.key,
        value=body.value,
        source="manual",
        importance=body.importance,
        category=body.category,
        updated_at=datetime.datetime.now(datetime.UTC).isoformat(),
    )
    await guard.save(response.model_dump(mode="json"))
    return response


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
            return db.update_memory(
                conn,
                user_id=principal["user_id"],
                key=body.key,
                value=body.value,
                importance=body.importance,
                category=body.category,
            )

    updated = await asyncio.to_thread(_update)
    if not updated:
        raise HTTPException(status_code=404, detail="Memory not found")
    audit_log("update", "memory", body.key, user_id=principal["user_id"])
    mems = await asyncio.to_thread(memory_service.list_all, principal["user_id"])
    for m in mems:
        if m["key"] == body.key:
            response = MemoryResponse(**m)
            await guard.save(response.model_dump(mode="json"))
            return response
    raise HTTPException(status_code=404, detail="Memory not found")


@router.post("/batch", response_model=MemoryBatchImportResponse)
async def batch_import_memories(
    request: Request,
    body: MemoryBatchImportRequest,
    principal: dict = Depends(verify_api_key),
    idempotency_key: str | None = Depends(idempotency_key_header),
):
    """Bulk import up to 100 memories for a user. Existing keys are updated (upsert).

    Uses a single SQL transaction for all writes.  Vector store upserts
    happen before the SQL commit; on SQL failure the vectors are cleaned
    up.  The idempotency cache is only written on full success — partial
    failures allow retries to make forward progress.
    """
    # MEDIUM-8: Reject oversized batch imports before any processing.
    _MAX_BATCH_IMPORT_ITEMS = 1000
    if len(body.memories) > _MAX_BATCH_IMPORT_ITEMS:
        raise HTTPException(
            status_code=413,
            detail=f"Batch import limit is {_MAX_BATCH_IMPORT_ITEMS} items per request",
        )

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
        if getattr(item, "expires_at", None):
            expires_at = getattr(item, "expires_at", None)
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
                "expires_at": expires_at,
            }
        )

    # ── Phase 2: Vector upserts (best-effort, collected) ────────────────
    # vs.upsert is sync and embeds through the (sync) embedder, which refuses
    # to run from a coroutine context. Offload via to_thread per CLAUDE.md.
    embedding_ids: dict[str, str | None] = {}  # key → embedding_id
    failed_vector_keys: set[str] = set()
    for item in prepared:
        try:
            from ...services.memory._vectorstore import get_memory_vectorstore

            vs = get_memory_vectorstore()
            eid = await asyncio.to_thread(
                vs.upsert,
                user_id,
                item["key"],
                item["value"],
                item["importance"],
                item["category"],
            )
            embedding_ids[item["key"]] = eid
        except Exception:
            logger.warning("Vector upsert failed for key %s", item["key"], exc_info=True)
            embedding_ids[item["key"]] = None
            failed_vector_keys.add(item["key"])

    # ── Phase 3: Single SQL transaction for all writes ──────────────────
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
                        embedding_id=embedding_ids.get(item["key"]),
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

    # ── Phase 4: Clean up vectors for items that failed SQL write ───────
    written_set = set(written_keys)
    for key in embedding_ids:
        eid = embedding_ids[key]
        if eid and key not in written_set:
            try:
                from ...services.memory._vectorstore import get_memory_vectorstore

                vs = get_memory_vectorstore()
                vs.delete(eid)
            except Exception:
                logger.warning("Failed to clean up orphan vector for key %s", key, exc_info=True)

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
    offset: int | None = Query(None, ge=0),
):
    """Export all memories for a user as JSON (suitable for re-import via /batch)."""
    resolved_offset = decode_cursor(cursor) if cursor else (offset or 0)
    resolved_user_id = principal["user_id"]
    if is_dev_user(resolved_user_id) and x_api_key:
        resolved_user_id = _derive_user_id_from_api_key(x_api_key)
    memories = await asyncio.to_thread(
        memory_service.list_all,
        resolved_user_id,
        include_expired=include_expired,
        limit=limit + 1,
        offset=resolved_offset,
    )
    # Backward compatibility in dev-mode tests that seeded legacy "default"
    # user records directly.
    if not memories and resolved_user_id != principal["user_id"]:
        memories = await asyncio.to_thread(
            memory_service.list_all,
            principal["user_id"],
            include_expired=include_expired,
            limit=limit + 1,
            offset=resolved_offset,
        )
    page = memories[:limit]
    export_items = [
        {
            "key": m["key"],
            "value": m["value"],
            "importance": m.get("importance", 3),
            "category": m.get("category"),
            "expires_at": m.get("expires_at"),  # Absolute TTL for round-tripping
        }
        for m in page
    ]
    response = MemoryExportResponse(
        user_id=resolved_user_id, total=len(export_items), memories=export_items
    )
    return response


@router.post("/search", response_model=MemorySearchResponse)
async def search_memories_semantic(
    request: MemorySearchRequest, principal: dict = Depends(verify_api_key)
):
    """Semantic search over user memories using vector similarity."""
    results = await memory_service.search_semantic(
        user_id=principal["user_id"],
        query=request.query,
        limit=request.limit,
        min_importance=request.min_importance,
    )
    return MemorySearchResponse(
        memories=[
            MemorySearchResultItem(
                key=r.key,
                value=r.value,
                importance=r.importance,
                category=r.category,
                combined_score=round(r.combined_score, 4),
                decay_score=round(r.decay_score, 4),
            )
            for r in results
        ],
        total=len(results),
    )


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


# ---------------------------------------------------------------------------
# Knowledge Graph entity endpoints
# ---------------------------------------------------------------------------


@router.get("/entities", response_model=EntityListResponse)
async def list_entities(
    principal: dict = Depends(verify_api_key),
    entity_type: str | None = Query(None, description="Filter by entity type"),
    limit: int = Query(50, ge=1, le=100),
):
    """List knowledge-graph entities for a user, sorted by mention count."""
    entities = await asyncio.to_thread(
        kg_service.get_entities, principal["user_id"], entity_type=entity_type, limit=limit
    )
    from ...models.schemas import EntityResponse

    return EntityListResponse(
        entities=[EntityResponse(**e) for e in entities],
        total=len(entities),
    )


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
