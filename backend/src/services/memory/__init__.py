"""Memory service - conversation history and long-term user memory with semantic search."""

from ...core.config import settings  # noqa: F401
from ._decay import (
    _get_active_user_ids,
    start_memory_decay_loop,
    stop_memory_decay_loop,
)
from ._entry import MemoryEntry
from ._history import (
    SQLiteChatMessageHistory,
    _load_session_cache,
    _persist_session_cache,
    get_session_history,
    invalidate_session,
)
from ._parsers import (
    _parse_consolidation_json,
    _semantic_cluster_memories,
    _text_cluster_memories,
)
from ._service import MemoryService
from ._summary_service import SessionSummaryService
from ._summary_vectorstore import SummaryVectorStore, get_summary_vectorstore
from ._vectorstore import MemoryVectorStore, get_memory_vectorstore

# ---- Singletons ----
memory_service = MemoryService()
session_summary_service = SessionSummaryService()

__all__ = [
    "MemoryEntry",
    "MemoryService",
    "MemoryVectorStore",
    "SQLiteChatMessageHistory",
    "SessionSummaryService",
    "SummaryVectorStore",
    "_get_active_user_ids",
    "_load_session_cache",
    "_parse_consolidation_json",
    "_persist_session_cache",
    "_semantic_cluster_memories",
    "_text_cluster_memories",
    "get_memory_vectorstore",
    "get_session_history",
    "get_summary_vectorstore",
    "invalidate_session",
    "memory_service",
    "session_summary_service",
    "start_memory_decay_loop",
    "stop_memory_decay_loop",
]
