"""Chroma-backed semantic search over knowledge-graph entities."""

import logging
import threading
import time

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from ...core.config import settings

logger = logging.getLogger(__name__)

_entity_vectorstore: "EntityVectorStore | None" = None
_entity_vectorstore_lock = threading.Lock()


class EntityVectorStore:
    """Chroma-backed semantic search over knowledge-graph entities."""

    def __init__(self, embeddings: Embeddings) -> None:
        from langchain_chroma import Chroma

        from ..rag import _ensure_collection_dimension

        self._embeddings = embeddings
        self._collection_name = "memory_entities"
        entity_dir = str(settings.VECTOR_DB_DIR / "entity_vectors")
        _ensure_collection_dimension(
            entity_dir,
            self._collection_name,
            embeddings,
            settings.EMBEDDING_DIMENSION,
        )
        self._chromadb = Chroma(
            collection_name=self._collection_name,
            embedding_function=embeddings,
            persist_directory=entity_dir,
        )

    def upsert(
        self,
        entity_id: int,
        user_id: str,
        name: str,
        entity_type: str,
        description: str | None,
        meeting_ids: list[int] | None = None,
        file_ids: list[int] | None = None,
    ) -> str:
        """Add or replace the embedding for an entity. Returns embedding_id.

        Retries up to 3 times with exponential backoff on Chroma failures.
        Raises on final failure so the caller can mark the entity as failed.
        """
        embedding_id = f"ent_{user_id[:8]}_{entity_id}"
        text = f"{entity_type}: {name}"
        if description:
            text += f" — {description}"
        metadata: dict = {
            "user_id": user_id,
            "entity_id": entity_id,
            "name": name,
            "entity_type": entity_type,
        }
        if meeting_ids:
            metadata["meeting_ids"] = ",".join(str(mid) for mid in meeting_ids)
        if file_ids:
            metadata["file_ids"] = ",".join(str(fid) for fid in file_ids)
        doc = Document(
            page_content=text,
            metadata=metadata,
        )
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                self._chromadb.add_documents([doc], ids=[embedding_id])
                return embedding_id
            except Exception as e:
                last_exc = e
                if attempt < 2:
                    delay = min(0.5 * (2**attempt), 1.0)
                    logger.debug(
                        "Entity vector upsert attempt %d failed, retrying in %.1fs: %s",
                        attempt + 1,
                        delay,
                        e,
                    )
                    time.sleep(delay)
        logger.error("Entity vector upsert failed after 3 attempts for entity %d", entity_id)
        raise last_exc  # type: ignore[misc]

    def delete(self, embedding_id: str) -> None:
        """Remove entity embedding."""
        import contextlib

        with contextlib.suppress(Exception):
            self._chromadb.delete(ids=[embedding_id])

    def similarity_search(
        self,
        query: str,
        user_id: str,
        top_k: int = 5,
        *,
        fetch_multiplier: int = 2,
    ) -> list[dict]:
        """Return top-k entities semantically similar to query.

        ``fetch_multiplier`` over-fetches before the caller applies scope
        post-filtering, avoiding recall collapse when many top hits are
        out of the selected meeting/file scope.
        """
        try:
            fetch_k = max(top_k * max(fetch_multiplier, 1), top_k)
            results = self._chromadb.similarity_search_with_score(
                query, k=fetch_k, filter={"user_id": user_id}
            )
            entities = []
            for doc, score in results:
                meta = doc.metadata
                raw_mids = meta.get("meeting_ids")
                raw_fids = meta.get("file_ids")
                entities.append(
                    {
                        "entity_id": meta.get("entity_id"),
                        "name": meta.get("name", ""),
                        "entity_type": meta.get("entity_type", ""),
                        "score": float(score),
                        "meeting_ids": (
                            [int(x) for x in raw_mids.split(",") if x.strip()] if raw_mids else None
                        ),
                        "file_ids": (
                            [int(x) for x in raw_fids.split(",") if x.strip()] if raw_fids else None
                        ),
                    }
                )
                if len(entities) >= top_k:
                    break
            return entities
        except Exception:
            return []


def get_entity_vectorstore() -> EntityVectorStore:
    """Get or create the singleton entity vector store (thread-safe)."""
    global _entity_vectorstore
    if _entity_vectorstore is None:
        with _entity_vectorstore_lock:
            if _entity_vectorstore is None:
                from ..embedder import get_embeddings

                _entity_vectorstore = EntityVectorStore(get_embeddings())
    return _entity_vectorstore
