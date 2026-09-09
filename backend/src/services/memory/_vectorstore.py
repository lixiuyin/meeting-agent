import hashlib
import threading
import time
from typing import Any, cast

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from . import settings
from ._common import logger

_memory_vectorstore: "MemoryVectorStore | None" = None
_memory_vectorstore_embeddings_id: int | None = None
_vectorstore_lock = threading.Lock()

# Per-user circuit breaker with half-open probe and exponential backoff.
# States: closed → open → half_open → closed (or back to open on probe failure).
_VECTOR_CB_MAX_FAILURES = 5
_VECTOR_CB_INITIAL_WAIT_S = 30.0  # Initial wait before half-open probe
_VECTOR_CB_MAX_WAIT_S = 1800.0  # 30 minutes cap

# State per key: (failure_count, last_failure_ts, current_wait_s, half_open)
_vector_cb_state: dict[str, tuple[int, float, float, bool]] = {}
_vector_cb_lock = threading.Lock()


def _vector_cb_key(collection: str, user_id: str | None = None) -> str:
    return f"{collection}:{user_id or 'global'}"


def _vector_cb_fail(collection: str, user_id: str | None = None) -> None:
    """Record a vector store failure for circuit-breaking.

    Transitions: closed → open, or half_open → open (with doubled wait).
    """
    key = _vector_cb_key(collection, user_id)
    with _vector_cb_lock:
        entry = _vector_cb_state.get(key)
        if entry is None:
            _vector_cb_state[key] = (1, time.monotonic(), _VECTOR_CB_INITIAL_WAIT_S, False)
        else:
            count, _ts, wait, _half = entry
            new_wait = min(wait * 2, _VECTOR_CB_MAX_WAIT_S)
            _vector_cb_state[key] = (count + 1, time.monotonic(), new_wait, False)


def _vector_cb_reset(collection: str, user_id: str | None = None) -> None:
    """Reset the circuit breaker after a successful operation."""
    key = _vector_cb_key(collection, user_id)
    with _vector_cb_lock:
        _vector_cb_state.pop(key, None)


def _vector_cb_open(collection: str, user_id: str | None = None) -> bool:
    """Check if the circuit breaker is open (blocking) for this collection/user.

    Returns True if the breaker is fully open (should skip).
    When the cooldown expires, transitions to half_open and returns False
    to allow a single probe request through.
    """
    key = _vector_cb_key(collection, user_id)
    with _vector_cb_lock:
        entry = _vector_cb_state.get(key)
        if entry is None:
            return False
        count, last_ts, wait, half_open = entry
        if count < _VECTOR_CB_MAX_FAILURES:
            return False
        # A probe is already in flight.  Block every other caller until that
        # probe either resets the breaker or records another failure.
        if half_open:
            return True
        # Check if cooldown has elapsed
        elapsed = time.monotonic() - last_ts
        if elapsed >= wait:
            # Transition to half_open: allow one probe
            _vector_cb_state[key] = (count, last_ts, wait, True)
            logger.info(
                "Circuit breaker for %s entering half-open after %.0fs wait",
                key,
                wait,
            )
            return False
        return True


class MemoryVectorStore:
    """Chroma-based semantic memory search with SQLite as authoritative store."""

    def __init__(self, embeddings: Embeddings):
        from langchain_chroma import Chroma

        from ..rag import _ensure_collection_dimension

        self._embeddings = embeddings
        self._collection_name = "user_memories"
        memory_dir = str(settings.VECTOR_DB_DIR / "memory_vectors")
        _ensure_collection_dimension(
            memory_dir, self._collection_name, embeddings, settings.EMBEDDING_DIMENSION
        )
        self._chromadb = Chroma(
            collection_name=self._collection_name,
            embedding_function=embeddings,
            persist_directory=memory_dir,
        )

    def _memory_id(self, user_id: str, key: str) -> str:
        """Generate deterministic memory ID for Chroma."""
        h = hashlib.sha256(f"{user_id}:{key}".encode()).hexdigest()[:16]
        return f"mem_{user_id}_{h}"

    def upsert(
        self,
        user_id: str,
        key: str,
        value: str,
        importance: float = 3,
        category: str | None = None,
        meeting_ids: list[int] | None = None,
        file_ids: list[int] | None = None,
        generation: str | None = None,
    ) -> str | None:
        """Add or update a memory in the vector store. Returns embedding_id.

        When the vector store circuit breaker is open, returns None gracefully
        instead of failing — the SQL row is still written and the vector will
        be backfilled later by ``sync_missing_vectors``.
        """
        cb_collection = self._collection_name
        if _vector_cb_open(cb_collection, user_id):
            logger.debug(
                "Vector store circuit breaker open for user %s; skipping memory upsert",
                user_id,
            )
            return None
        try:
            embedding_id = self._memory_id(user_id, key)
            if generation is not None:
                embedding_id += f"_v{generation}"
            text = f"[{category or 'general'}] {key}: {value}" if category else f"{key}: {value}"
            metadata: dict = {"user_id": user_id, "key": key, "importance": importance}
            if generation is not None:
                metadata["generation"] = generation
            if category:
                metadata["category"] = category
            if meeting_ids:
                metadata["meeting_ids"] = ",".join(str(mid) for mid in meeting_ids)
            if file_ids:
                metadata["file_ids"] = ",".join(str(fid) for fid in file_ids)
            doc = Document(
                page_content=text,
                metadata=metadata,
            )
            self._chromadb.add_documents([doc], ids=[embedding_id])
            _vector_cb_reset(cb_collection, user_id)
            return embedding_id
        except Exception as e:
            _vector_cb_fail(cb_collection, user_id)
            logger.warning(
                "Failed to upsert memory vector for user %s: %s", user_id, e, exc_info=True
            )
            raise

    def bump_importance(
        self,
        user_id: str,
        key: str,
        importance: float,
        *,
        embedding_id: str | None,
        category: str | None = None,
        meeting_ids: list[int] | None = None,
        file_ids: list[int] | None = None,
    ) -> bool:
        """Update a memory vector's metadata WITHOUT re-embedding.

        Used by the recall-boost hot path: ``boost_recalled`` is invoked
        after every chat answer for each retrieved memory, and re-embedding
        every record per query is an N+1 cost (1 embedding API call per
        recalled memory). Chroma's ``_collection.update`` skips embedding
        computation entirely when neither ``documents`` nor ``embeddings``
        are passed, so we use it for metadata-only updates.

        Returns True on success, False if the breaker was open or the
        underlying call raised. Failures are logged but never propagated —
        the SQL importance is the source of truth, the metadata is just a
        post-filter hint that will be reconciled on next full upsert.
        """
        cb_collection = self._collection_name
        if _vector_cb_open(cb_collection, user_id):
            logger.debug(
                "Vector store circuit breaker open for user %s; skipping importance bump",
                user_id,
            )
            return False
        if not embedding_id:
            logger.debug(
                "Memory %s/%s has no published embedding; importance sync deferred",
                user_id,
                key,
            )
            return False
        try:
            metadata: dict = {"user_id": user_id, "key": key, "importance": importance}
            if category:
                metadata["category"] = category
            if meeting_ids:
                metadata["meeting_ids"] = ",".join(str(mid) for mid in meeting_ids)
            if file_ids:
                metadata["file_ids"] = ",".join(str(fid) for fid in file_ids)
            self._chromadb._collection.update(  # type: ignore[attr-defined]
                ids=[embedding_id],
                metadatas=[metadata],
            )
            _vector_cb_reset(cb_collection, user_id)
            return True
        except Exception as e:
            _vector_cb_fail(cb_collection, user_id)
            logger.warning(
                "Failed to bump memory importance for user %s key %s: %s",
                user_id,
                key,
                e,
                exc_info=True,
            )
            return False

    def delete(self, embedding_id: str) -> None:
        """Delete a memory vector, propagating failures to the retry owner."""
        self._chromadb.delete(ids=[embedding_id])

    def similarity_search(
        self,
        query: str,
        user_id: str,
        top_k: int = 5,
        min_importance: float = 1,
        *,
        fetch_multiplier: int = 2,
        allowed_keys: list[str] | None = None,
    ) -> list[dict]:
        """Semantic search over user memories.

        ``fetch_multiplier`` controls over-fetching from Chroma before the
        Python-side importance and scope post-filters are applied. Callers
        that post-filter by meeting/file scope should pass a larger value
        (via ``MEMORY_SEARCH_OVERSAMPLE_FACTOR``) to avoid filter-induced
        recall collapse when top-K is dominated by out-of-scope memories.
        """
        cb_collection = self._collection_name
        if _vector_cb_open(cb_collection, user_id):
            from ...core.metrics import MEMORY_SEARCH_TOTAL

            MEMORY_SEARCH_TOTAL.labels(status="vector_degraded").inc()
            logger.debug(
                "Vector store circuit breaker open for user %s; using SQL fallback",
                user_id,
            )
            return []
        if allowed_keys is not None and not allowed_keys:
            return []
        try:
            fetch_k = max(top_k * max(fetch_multiplier, 1), top_k)
            base_filters: list[dict] = [
                {"user_id": user_id},
                {"key": {"$ne": "__profile__"}},
            ]
            # Chroma's `$in` filter is bounded in batches so large user stores
            # do not create oversized metadata expressions.  Query every
            # eligible batch and globally merge by distance.
            key_batches: list[list[str] | None]
            if allowed_keys is None:
                key_batches = [None]
            else:
                key_batches = [allowed_keys[i : i + 200] for i in range(0, len(allowed_keys), 200)]
            results = []
            for key_batch in key_batches:
                filters = list(base_filters)
                if key_batch is not None:
                    filters.append({"key": {"$in": key_batch}})
                results.extend(
                    cast(Any, self._chromadb).similarity_search_with_score(
                        query,
                        k=min(fetch_k, len(key_batch)) if key_batch is not None else fetch_k,
                        filter={"$and": filters},
                    )
                )
            results.sort(key=lambda item: float(item[1]))
            memories = []
            for doc, score in results:
                meta = doc.metadata
                # Filter by minimum importance
                if meta.get("importance", 3) < min_importance:
                    continue
                # Parse meeting_ids/file_ids from comma-separated string (stored by upsert)
                raw_mids = meta.get("meeting_ids")
                raw_fids = meta.get("file_ids")
                entry = {
                    "key": meta["key"],
                    "generation": meta.get("generation"),
                    "content": doc.page_content,
                    "score": float(score),
                    "importance": meta.get("importance", 3),
                    "category": meta.get("category"),
                    "meeting_ids": (
                        [int(x) for x in raw_mids.split(",") if x.strip()] if raw_mids else None
                    ),
                    "file_ids": (
                        [int(x) for x in raw_fids.split(",") if x.strip()] if raw_fids else None
                    ),
                }
                memories.append(entry)
                if len(memories) >= top_k:
                    break
            _vector_cb_reset(cb_collection, user_id)
            return memories
        except Exception as e:
            _vector_cb_fail(cb_collection, user_id)
            # The SQL importance fallback remains available, but surface the
            # degraded vector path instead of reporting a silent success.
            from ...core.metrics import MEMORY_SEARCH_TOTAL

            MEMORY_SEARCH_TOTAL.labels(status="vector_degraded").inc()
            logger.error("Memory semantic search failed: %s", e, exc_info=True)
            return []

    def is_empty(self) -> bool:
        """Check if vector store has any memories."""
        try:
            return self._chromadb._collection.count() == 0  # type: ignore
        except Exception:
            return True


def get_memory_vectorstore() -> MemoryVectorStore:
    """Get or create the singleton memory vector store."""
    global _memory_vectorstore, _memory_vectorstore_embeddings_id
    from ..embedder import get_embeddings

    embeddings = get_embeddings()
    embeddings_id = id(embeddings)
    if _memory_vectorstore is None or _memory_vectorstore_embeddings_id != embeddings_id:
        with _vectorstore_lock:
            if _memory_vectorstore is None or _memory_vectorstore_embeddings_id != embeddings_id:
                _memory_vectorstore = MemoryVectorStore(embeddings)
                _memory_vectorstore_embeddings_id = embeddings_id
                logger.info("MemoryVectorStore initialized")
    return _memory_vectorstore


def reset_memory_vectorstore() -> None:
    """Drop the cached wrapper after embedding configuration changes."""
    global _memory_vectorstore, _memory_vectorstore_embeddings_id
    with _vectorstore_lock:
        _memory_vectorstore = None
        _memory_vectorstore_embeddings_id = None


def reconcile_orphan_memory_vectors() -> int:
    """Remove memory vectors that have no corresponding SQLite record.

    If a crash occurs between Phase 2 (vector upsert) and Phase 3 (SQL commit)
    during batch import, orphaned vectors are left in Chroma.  This reconciler
    detects and cleans them up.  Best-effort only — failures are logged but
    never raised.

    Returns count of orphan vectors removed.
    """
    from ...core import database as db

    vs = get_memory_vectorstore()
    try:
        all_vec = vs._chromadb.get(include=[])
    except Exception:
        logger.debug("Cannot reconcile memory vectors — Chroma unavailable")
        return 0

    vec_ids = set(all_vec.get("ids", []) or [])
    if not vec_ids:
        return 0

    try:
        with db.get_connection() as conn:
            rows = conn.execute(
                "SELECT embedding_id FROM user_memories WHERE embedding_id IS NOT NULL"
            ).fetchall()
        db_ids = {row["embedding_id"] for row in rows}
    except Exception:
        logger.debug("Cannot reconcile memory vectors — SQLite unavailable")
        return 0

    orphans = vec_ids - db_ids
    if not orphans:
        return 0

    removed = 0
    for eid in orphans:
        try:
            vs.delete(eid)
            removed += 1
        except Exception:
            logger.warning("Failed to delete orphan memory vector %s", eid, exc_info=True)
    if removed:
        logger.info("Reconciled %d orphan memory vectors", removed)
    return removed
