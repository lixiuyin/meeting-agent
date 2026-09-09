"""RAG pipeline using LangChain - chunking, indexing, and retrieval.

Supports:
  - Score-based filtering (P0)
  - Reranking via Cohere / BGE (P1)
  - Parent-child chunking (P2)
  - Hybrid FTS5 + vector search (P3)
  - Batch Chroma persist: reduce write amplification
  - Adaptive top_k: simple questions -> 3 docs, complex -> 8 docs
  - Query rewrite for LLM-powered retrieval improvement
  - Structure-aware semantic chunking: splits on topic boundaries
"""

# Public API -- re-exported for backward compatibility
from ._indexer import (
    delete_meeting_chunks,
    index_meeting,
    index_meeting_pages,
    index_meeting_segments,
)
from ._query import determine_adaptive_top_k, reset_rewrite_llm, rewrite_query
from ._query_analysis import QueryAnalysis, analyze_query
from ._query_plan import QueryPlan, build_query_plan, infer_query_intent
from ._reconcile import reconcile_multimodal_index_state
from ._reranker import rerank, reset_reranker_state
from ._retriever import (
    check_and_rebuild_bm25_if_drifted,
    load_bm25_from_database,
    rebuild_bm25_from_chroma,
    retrieve,
    retrieve_sibling_chunks,
)
from ._summary_router import route_files_by_summary, route_files_with_scores
from ._summary_vectorstore import (
    delete_file_summary,
    delete_meeting_summaries,
    get_summary_vectorstore,
    reset_summary_vectorstore,
    upsert_file_summary,
)
from ._vectorstore import (
    _ensure_collection_dimension,
    get_vectorstore,
    persist_vectorstore,
    reset_vectorstore,
)

__all__ = [
    "QueryAnalysis",
    "QueryPlan",
    "_ensure_collection_dimension",
    "analyze_query",
    "build_query_plan",
    "check_and_rebuild_bm25_if_drifted",
    "delete_file_summary",
    "delete_meeting_chunks",
    "delete_meeting_summaries",
    "determine_adaptive_top_k",
    "get_summary_vectorstore",
    "get_vectorstore",
    "index_meeting",
    "index_meeting_pages",
    "index_meeting_segments",
    "infer_query_intent",
    "load_bm25_from_database",
    "persist_vectorstore",
    "rebuild_bm25_from_chroma",
    "reconcile_multimodal_index_state",
    "rerank",
    "reset_reranker_state",
    "reset_rewrite_llm",
    "reset_summary_vectorstore",
    "reset_vectorstore",
    "retrieve",
    "retrieve_sibling_chunks",
    "rewrite_query",
    "route_files_by_summary",
    "route_files_with_scores",
    "upsert_file_summary",
]
