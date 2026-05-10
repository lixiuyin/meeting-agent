import hashlib
import threading

from langchain_core.documents import Document

from ...core.config import settings
from ._common import logger

_summary_vectorstore: "SummaryVectorStore | None" = None
_summary_vectorstore_lock = threading.Lock()


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
    ) -> str:
        """Add or update a session summary in the vector store. Returns embedding_id."""
        embedding_id = f"sess_{hashlib.sha256(session_id.encode()).hexdigest()[:16]}"
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
        doc = Document(
            page_content=text,
            metadata=metadata,
        )
        self._chromadb.add_documents([doc], ids=[embedding_id])
        return embedding_id

    def delete(self, embedding_id: str) -> None:
        """Delete a summary vector."""
        try:
            self._chromadb.delete(ids=[embedding_id])
        except Exception as e:
            logger.warning("Failed to delete summary vector %s: %s", embedding_id, e)

    def similarity_search(self, query: str, user_id: str, top_k: int = 5) -> list[dict]:
        """Semantic search over session summaries for a user."""
        try:
            results = self._chromadb.similarity_search_with_score(
                query, k=top_k * 2, filter={"user_id": user_id}
            )
            summaries = []
            for doc, score in results:
                meta = doc.metadata
                raw_mids = meta.get("meetings_covered")
                summaries.append(
                    {
                        "session_id": meta.get("session_id", ""),
                        "content": doc.page_content,
                        "score": float(score),
                        "meetings_covered": (
                            [int(x) for x in raw_mids.split(",") if x.strip()] if raw_mids else None
                        ),
                    }
                )
                if len(summaries) >= top_k:
                    break
            return summaries
        except Exception as e:
            logger.warning("Summary semantic search failed: %s", e)
            return []


def get_summary_vectorstore() -> SummaryVectorStore:
    """Get or create the singleton summary vector store."""
    global _summary_vectorstore
    if _summary_vectorstore is None:
        with _summary_vectorstore_lock:
            if _summary_vectorstore is None:
                from ..embedder import get_embeddings

                _summary_vectorstore = SummaryVectorStore(get_embeddings())
                logger.info("SummaryVectorStore initialized")
    return _summary_vectorstore
