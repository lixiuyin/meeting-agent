import hashlib
import json
import threading

from langchain_core.documents import Document

from ...core.config import settings
from ._common import logger, scope_from_messages

_summary_vectorstore: "SummaryVectorStore | None" = None
_summary_vectorstore_embeddings_id: int | None = None
_summary_vectorstore_lock = threading.Lock()
_summary_vector_write_lock = threading.Lock()
_SUMMARY_SYNC_PAGE_SIZE = 500


def _summary_embedding_id(session_id: str) -> str:
    return f"sess_{hashlib.sha256(session_id.encode()).hexdigest()[:16]}"


def summary_vector_write_lock() -> threading.Lock:
    """Return the shared lock for deterministic session-vector replacement."""
    return _summary_vector_write_lock


class SummaryVectorStore:
    """Chroma-based semantic search over session summaries."""

    def __init__(self, embeddings):
        from langchain_chroma import Chroma

        from ..rag import _ensure_collection_dimension

        self._embeddings = embeddings
        self._collection_name = "session_summaries"
        summary_dir = str(settings.VECTOR_DB_DIR / "summary_vectors")
        _ensure_collection_dimension(
            summary_dir, self._collection_name, embeddings, settings.EMBEDDING_DIMENSION
        )
        self._chromadb = Chroma(
            collection_name=self._collection_name,
            embedding_function=embeddings,
            persist_directory=summary_dir,
        )

    def upsert(
        self,
        session_id: str,
        user_id: str,
        summary_text: str,
        topics: list[str] | None = None,
        meetings_covered: list[int] | None = None,
        files_covered: list[int] | None = None,
    ) -> str:
        """Add or update a session summary in the vector store. Returns embedding_id."""
        embedding_id = _summary_embedding_id(session_id)
        text = summary_text
        if topics:
            text = f"[Topics: {', '.join(topics)}] {text}"
        metadata: dict = {
            "user_id": user_id,
            "session_id": session_id,
            "type": "session_summary",
        }
        if meetings_covered:
            metadata["meetings_covered"] = ",".join(str(mid) for mid in meetings_covered)
        if files_covered:
            metadata["files_covered"] = ",".join(str(fid) for fid in files_covered)
        doc = Document(
            page_content=text,
            metadata=metadata,
        )
        self._chromadb.add_documents([doc], ids=[embedding_id])
        return embedding_id

    def delete(self, embedding_id: str) -> None:
        """Delete a summary vector, propagating failures to the retry owner."""
        self._chromadb.delete(ids=[embedding_id])

    def similarity_search(
        self,
        query: str,
        user_id: str,
        top_k: int = 5,
        *,
        raise_on_error: bool = False,
    ) -> list[dict]:
        """Semantic search over session summaries for a user.

        Interactive callers historically receive an empty best-effort result.
        The hybrid session-memory service opts into ``raise_on_error`` so it can
        distinguish a genuine empty result from vector-backend degradation and
        report/use its FTS fallback accurately.
        """
        try:
            results = self._chromadb.similarity_search_with_score(
                query, k=top_k * 2, filter={"user_id": user_id}
            )
            summaries = []
            for doc, score in results:
                meta = doc.metadata
                raw_mids = meta.get("meetings_covered")
                raw_fids = meta.get("files_covered")
                summaries.append(
                    {
                        "session_id": meta.get("session_id", ""),
                        "content": doc.page_content,
                        # Chroma returns distance for cosine and L2; expose a
                        # consistent higher-is-better relevance score.
                        "score": 1.0 / (1.0 + max(float(score), 0.0)),
                        "meetings_covered": (
                            [int(x) for x in raw_mids.split(",") if x.strip()] if raw_mids else None
                        ),
                        "files_covered": (
                            [int(x) for x in raw_fids.split(",") if x.strip()] if raw_fids else None
                        ),
                    }
                )
                if len(summaries) >= top_k:
                    break
            return summaries
        except Exception as e:
            logger.warning("Summary semantic search failed: %s", e, exc_info=True)
            if raise_on_error:
                raise
            return []


def get_summary_vectorstore() -> SummaryVectorStore:
    """Get or create the singleton summary vector store."""
    global _summary_vectorstore, _summary_vectorstore_embeddings_id
    from ..embedder import get_embeddings

    embeddings = get_embeddings()
    embeddings_id = id(embeddings)
    if _summary_vectorstore is None or _summary_vectorstore_embeddings_id != embeddings_id:
        with _summary_vectorstore_lock:
            if _summary_vectorstore is None or _summary_vectorstore_embeddings_id != embeddings_id:
                _summary_vectorstore = SummaryVectorStore(embeddings)
                _summary_vectorstore_embeddings_id = embeddings_id
                logger.info("SummaryVectorStore initialized")
    return _summary_vectorstore


def reset_summary_vectorstore() -> None:
    """Drop the cached wrapper after embedding configuration changes."""
    global _summary_vectorstore, _summary_vectorstore_embeddings_id
    with _summary_vectorstore_lock:
        _summary_vectorstore = None
        _summary_vectorstore_embeddings_id = None


def reconcile_orphan_summary_vectors() -> int:
    """Remove session-summary vectors with no live SQLite summary row."""
    from ...core import database as db

    vs = get_summary_vectorstore()
    try:
        vector_ids = set(vs._chromadb.get(include=[]).get("ids") or [])
        with db.get_connection() as conn:
            rows = conn.execute(
                "SELECT embedding_id FROM session_summaries WHERE embedding_id IS NOT NULL"
            ).fetchall()
        database_ids = {row["embedding_id"] for row in rows}
    except Exception:
        logger.warning("Cannot reconcile session-summary vectors", exc_info=True)
        return 0
    removed = 0
    for embedding_id in vector_ids - database_ids:
        try:
            vs.delete(embedding_id)
            removed += 1
        except Exception:
            logger.warning("Failed to delete orphan summary vector %s", embedding_id, exc_info=True)
    return removed


def sync_missing_summary_vectors(*, repair_limit: int = 1000) -> int:
    """Rebuild missing session-summary vectors from authoritative SQLite rows.

    The scan is keyset-paginated so healthy rows at the start of a large table
    cannot starve later repairs. Each candidate is revalidated under the same
    lock used by live summarization before replacing its deterministic vector,
    preventing a stale scan snapshot from overwriting a concurrent newer
    summary. Safe for startup and periodic best-effort execution.
    """
    from ...core import database as db
    from ...core.database import get_write_connection

    if repair_limit <= 0:
        return 0
    try:
        vs = get_summary_vectorstore()
        existing_ids = set(vs._chromadb.get(include=[]).get("ids") or [])
    except Exception:
        logger.warning("Cannot enumerate session-summary vectors for sync", exc_info=True)
        return 0

    repaired = 0
    last_id = 0
    while repaired < repair_limit:
        try:
            with db.get_connection() as conn:
                rows = conn.execute(
                    "SELECT id, session_id, user_id, summary, topics, turn_count, embedding_id "
                    "FROM session_summaries WHERE id>? ORDER BY id LIMIT ?",
                    (last_id, _SUMMARY_SYNC_PAGE_SIZE),
                ).fetchall()
        except Exception:
            logger.warning("Cannot enumerate SQLite session summaries for sync", exc_info=True)
            break
        if not rows:
            break

        for scanned in rows:
            last_id = int(scanned["id"])
            expected_id = _summary_embedding_id(scanned["session_id"])
            if scanned["embedding_id"] == expected_id and expected_id in existing_ids:
                continue
            if repaired >= repair_limit:
                break

            acquired = _summary_vector_write_lock.acquire(timeout=30.0)
            if not acquired:
                logger.warning("Session-summary vector sync lock timed out")
                return repaired
            try:
                # Re-read after acquiring the shared vector lock. A live
                # summarizer may have advanced this row since the page scan.
                with db.get_connection() as conn:
                    current = db.get_session_summary(
                        conn,
                        scanned["session_id"],
                        user_id=scanned["user_id"],
                    )
                    messages = db.get_messages(conn, scanned["session_id"]) if current else []
                if not current:
                    continue
                current_id = _summary_embedding_id(current["session_id"])
                if current.get("embedding_id") == current_id and current_id in existing_ids:
                    continue
                raw_topics = current.get("topics")
                try:
                    topics = json.loads(raw_topics) if raw_topics else None
                except (json.JSONDecodeError, TypeError):
                    topics = None
                if not isinstance(topics, list):
                    topics = None
                # Repaired vectors must carry the same scope metadata as live
                # writes. Otherwise strict meeting/file recall would needlessly
                # recover provenance from every message on each query.
                meetings_covered, files_covered = scope_from_messages(messages)
                embedding_id = vs.upsert(
                    current["session_id"],
                    current["user_id"],
                    current.get("summary", ""),
                    topics,
                    meetings_covered=meetings_covered,
                    files_covered=files_covered,
                )
                with get_write_connection() as conn:
                    cursor = conn.execute(
                        "UPDATE session_summaries SET embedding_id=? "
                        "WHERE session_id=? AND user_id=? AND turn_count IS ? AND summary=?",
                        (
                            embedding_id,
                            current["session_id"],
                            current["user_id"],
                            current.get("turn_count"),
                            current.get("summary", ""),
                        ),
                    )
                if cursor.rowcount:
                    existing_ids.add(embedding_id)
                    repaired += 1
            except Exception:
                logger.warning(
                    "Failed to sync session-summary vector for %s",
                    scanned["session_id"],
                    exc_info=True,
                )
            finally:
                _summary_vector_write_lock.release()

        if len(rows) < _SUMMARY_SYNC_PAGE_SIZE:
            break

    if repaired:
        logger.info("Rebuilt %d missing session-summary vectors", repaired)
    return repaired
