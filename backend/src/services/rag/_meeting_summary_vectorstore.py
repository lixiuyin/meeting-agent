"""Singleton Chroma collection for meeting-level summary embeddings.

This collection holds one document per meeting whose summary has been
generated (either auto or on-demand).  The broad-recall pipeline uses it
as a **meeting-level routing signal** — a cheap pre-filter that narrows
the search to the most relevant meetings before the more expensive
file-level routing and chunk-level retrieval.

Doc id is deterministic: ``ms_{meeting_id}``.  Re-indexing the same
meeting overwrites its single vector — there's no chunking at this layer.

Provenance metadata (``contributing_file_ids``, ``contributing_file_names``)
records which per-file summaries were used to generate the meeting summary,
enabling level-by-level traceability from retrieval results back to sources.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from typing import Any

from langchain_chroma import Chroma

from ...core.config import settings
from ...core.database._connection import _write_lock as _db_write_lock
from ...core.metrics import SUMMARY_VECTOR_UPSERT_FAILURES_TOTAL
from ..embedder import get_embeddings
from ._vectorstore import _ensure_collection_dimension

logger = logging.getLogger(__name__)

_COLLECTION_NAME = "meeting_summaries"

_store: Chroma | None = None
_store_embeddings_id: int | None = None
_lock = threading.Lock()


def reset_meeting_summary_vectorstore() -> None:
    """Reset the singleton.  Called when embedding settings change."""
    global _store, _store_embeddings_id
    with _lock:
        _store = None
        _store_embeddings_id = None
    logger.info("Meeting summary vectorstore singleton reset")


def get_meeting_summary_vectorstore() -> Chroma:
    """Get or create the singleton meeting summary collection (thread-safe)."""
    global _store, _store_embeddings_id
    embeddings = get_embeddings()
    embeddings_id = id(embeddings)
    if _store is None or _store_embeddings_id != embeddings_id:
        with _lock:
            if _store is None or _store_embeddings_id != embeddings_id:
                expected_dim = _resolve_dimension(embeddings, settings.EMBEDDING_DIMENSION)
                _ensure_collection_dimension(
                    str(settings.VECTOR_DB_DIR),
                    _COLLECTION_NAME,
                    embeddings,
                    expected_dim,
                )
                _store = Chroma(
                    collection_name=_COLLECTION_NAME,
                    embedding_function=embeddings,
                    persist_directory=str(settings.VECTOR_DB_DIR),
                )
                _store_embeddings_id = embeddings_id
    return _store


def meeting_summary_vectorstore_write_lock() -> threading.RLock:
    """Global write lock for meeting summary collection mutations.

    Shares the same lock as SQLite writes to prevent orphaned vectors/rows.
    """
    return _db_write_lock


def _resolve_dimension(embeddings: Any, fallback: int) -> int:
    for attr in ("embedding_dimension", "dimension", "dimensions"):
        candidate = getattr(embeddings, attr, None)
        if isinstance(candidate, int) and candidate > 0:
            return candidate
    return fallback


def _doc_id(meeting_id: int) -> str:
    return f"ms_{meeting_id}"


def upsert_meeting_summary(
    meeting_id: int,
    title: str,
    summary: str,
    *,
    contributing_file_ids: list[int] | None = None,
    contributing_file_names: list[str] | None = None,
    content_hash: str | None = None,
    user_id: str | None = None,
) -> None:
    """Upsert a meeting-level summary into the dedicated collection.

    Empty summaries are deleted instead of stored.
    """
    summary = (summary or "").strip()
    if not summary:
        delete_meeting_summary(meeting_id)
        return

    metadata: dict[str, Any] = {
        "meeting_id": int(meeting_id),
        "title": title,
        "content_type": "meeting_summary",
    }
    if user_id is not None:
        metadata["user_id"] = user_id
    if content_hash:
        metadata["content_hash"] = content_hash
    if contributing_file_ids:
        metadata["contributing_file_ids"] = ",".join(str(i) for i in contributing_file_ids)
    if contributing_file_names:
        metadata["contributing_file_names"] = ";".join(contributing_file_names)

    store = get_meeting_summary_vectorstore()
    doc_id = _doc_id(meeting_id)

    # Idempotent skip: if content_hash matches existing metadata, skip embedding.
    if content_hash:
        try:
            existing = store._collection.get(ids=[doc_id], include=["metadatas"])
            if existing and existing.get("ids") and existing["ids"][0] == doc_id:
                old_meta = (existing.get("metadatas") or [{}])[0]
                owner_matches = user_id is None or old_meta.get("user_id") == user_id
                if old_meta.get("content_hash") == content_hash and owner_matches:
                    logger.debug(
                        "Meeting %d summary unchanged (hash=%s); skipping upsert",
                        meeting_id,
                        content_hash,
                    )
                    return
        except Exception:
            pass  # fall through to full upsert

    try:
        embedding = get_embeddings().embed_documents([summary])[0]
    except Exception:
        logger.warning(
            "Embedding failed for meeting %d summary; skipping upsert",
            meeting_id,
            exc_info=True,
        )
        with contextlib.suppress(Exception):
            SUMMARY_VECTOR_UPSERT_FAILURES_TOTAL.labels(
                collection=_COLLECTION_NAME, reason="embedding"
            ).inc()
        return

    try:
        with _db_write_lock:
            store._collection.upsert(
                ids=[doc_id],
                documents=[summary],
                embeddings=[embedding],
                metadatas=[metadata],
            )
    except Exception:
        logger.warning("Upsert failed for meeting %d summary", meeting_id, exc_info=True)
        with contextlib.suppress(Exception):
            SUMMARY_VECTOR_UPSERT_FAILURES_TOTAL.labels(
                collection=_COLLECTION_NAME, reason="upsert"
            ).inc()
        return
    logger.info("Meeting summary vector upserted for meeting %d", meeting_id)


def delete_meeting_summary(meeting_id: int) -> None:
    """Remove a meeting summary vector, propagating failures for retry."""
    store = get_meeting_summary_vectorstore()
    doc_id = _doc_id(meeting_id)
    with _db_write_lock:
        store._collection.delete(ids=[doc_id])


def _vector_score_lower_is_better() -> bool:
    """Guess whether the vectorstore distance metric is lower-is-better."""
    from ._vector import _vector_score_lower_is_better as _impl

    return _impl()


def normalize_score(raw: float, lower_is_better: bool) -> float:
    """Normalize raw vector distance to uniform higher-is-better scale."""
    from ._vector import normalize_score as _impl

    return _impl(raw, lower_is_better)


def route_meetings_by_summary(
    query: str,
    *,
    top_k: int | None = None,
    min_score: float | None = None,
    user_id: str | None = None,
) -> list[tuple[int, float]] | None:
    """Return ``(meeting_id, score)`` pairs ranked by summary similarity.

    Returns ``None`` when:
      * the router is disabled in settings,
      * the collection is empty,
      * any error occurs.

    A ``None`` result tells the caller to skip meeting-level pre-filtering
    (fail-open).  An empty list means the router ran but nothing matched.
    """
    if not settings.RAG_MEETING_SUMMARY_ROUTER_ENABLED:
        return None

    query = (query or "").strip()
    if not query:
        return None

    requested_top_k = (
        top_k if top_k is not None else settings.RAG_MEETING_SUMMARY_ROUTER_TOP_MEETINGS
    )
    if requested_top_k <= 0:
        return None

    threshold = (
        min_score if min_score is not None else settings.RAG_MEETING_SUMMARY_ROUTER_MIN_SCORE
    )
    lower_is_better = _vector_score_lower_is_better()

    try:
        store = get_meeting_summary_vectorstore()
    except Exception:
        logger.warning("Meeting summary vectorstore init failed; router disabled", exc_info=True)
        return None

    try:
        if store._collection.count() == 0:
            return None
    except Exception:
        logger.debug("Meeting summary collection count check failed", exc_info=True)

    # A principal named ``default`` is still a real tenant.  Fetch extra
    # candidates for every scoped request because the authoritative ownership
    # check below happens in SQLite (older vectors may not have user metadata).
    fetch_k = requested_top_k * (5 if user_id is not None else 2)

    results: list[tuple[Any, float]] = []
    try:
        results = store.similarity_search_with_score(query, k=fetch_k)
    except Exception:
        logger.warning("Meeting summary similarity search failed", exc_info=True)
        return None

    ranked: list[tuple[int, float]] = []
    seen: set[int] = set()
    for doc, score in results:
        meta = doc.metadata or {}
        meeting_id = meta.get("meeting_id")
        if not isinstance(meeting_id, int) or meeting_id in seen:
            continue
        score_f = normalize_score(float(score), lower_is_better)
        if threshold > 0 and score_f < threshold:
            continue
        ranked.append((meeting_id, score_f))
        seen.add(meeting_id)

    # Tombstone check: remove meetings whose summary was invalidated (DB row
    # gone or status != 'ready') and compensate by deleting orphan vectors.
    if ranked:
        from ...core.database import get_connection

        valid: list[tuple[int, float]] = []
        orphans: list[int] = []
        with get_connection() as conn:
            for mid, sc in ranked:
                if user_id is not None:
                    row = conn.execute(
                        "SELECT summary_status FROM meetings WHERE id=? AND user_id=?",
                        (mid, user_id),
                    ).fetchone()
                else:
                    row = conn.execute(
                        "SELECT summary_status FROM meetings WHERE id=?", (mid,)
                    ).fetchone()
                if row and row["summary_status"] == "ready":
                    valid.append((mid, sc))
                # In a tenant-scoped lookup, a missing row may simply belong
                # to another tenant and must never be deleted as an orphan.
                elif user_id is None:
                    orphans.append(mid)
        if orphans:
            logger.info(
                "Tombstone: removing %d orphan meeting summary vectors: %s",
                len(orphans),
                orphans,
            )
            for mid in orphans:
                try:
                    delete_meeting_summary(mid)
                except Exception:
                    logger.debug("Failed to delete orphan vector for %d", mid, exc_info=True)
        ranked = valid

    ranked.sort(key=lambda x: x[1], reverse=True)
    selected = ranked[:requested_top_k]

    logger.info(
        "Meeting summary router: query='%s' -> %d/%d meetings (top_k=%d)",
        query[:60],
        len(selected),
        len(results),
        requested_top_k,
    )
    return selected
