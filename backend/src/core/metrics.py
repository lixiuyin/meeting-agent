"""Prometheus metric definitions for the Meeting Agent.

All metrics are lazily registered on first import. The /metrics endpoint in
``api/metrics.py`` calls ``generate_latest()`` to expose them in the standard
Prometheus text exposition format.

Labels:
    - status: success | error | timeout
    - intent: casual | retrieval | search
    - provider: openai | anthropic | ollama | ...
    - method: GET | POST | PUT | DELETE
    - path: URL path template
"""

from prometheus_client import Counter, Gauge, Histogram

# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------

HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    labelnames=["method", "path", "status"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

# ---------------------------------------------------------------------------
# Meeting uploads
# ---------------------------------------------------------------------------

MEETING_UPLOAD_TOTAL = Counter(
    "meeting_upload_total",
    "Total meeting file uploads",
    labelnames=["status"],
)

# ---------------------------------------------------------------------------
# Chat / RAG
# ---------------------------------------------------------------------------

CHAT_REQUEST_TOTAL = Counter(
    "chat_request_total",
    "Total chat/RAG requests",
    labelnames=["intent"],
)

# ---------------------------------------------------------------------------
# LLM calls
# ---------------------------------------------------------------------------

LLM_REQUEST_DURATION = Histogram(
    "llm_request_duration_seconds",
    "LLM invoke latency in seconds",
    labelnames=["provider"],
    buckets=(0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0),
)

LLM_REQUEST_TOTAL = Counter(
    "llm_request_total",
    "Total LLM requests",
    labelnames=["provider", "status"],
)

# ---------------------------------------------------------------------------
# Traffic controller / circuit breaker
# ---------------------------------------------------------------------------

BREAKER_STATE = Gauge(
    "traffic_controller_breaker_state",
    "Circuit breaker state (1=closed, 0.5=half-open, 0=open)",
)

TRAFFIC_INFLIGHT = Gauge(
    "traffic_controller_inflight",
    "Current in-flight LLM requests",
)

BACKGROUND_TASK_FAILURES_TOTAL = Counter(
    "background_task_failures_total",
    "Total supervised background task failures",
    labelnames=["name", "error_type"],
)

SQLITE_BUSY_TIMEOUTS_TOTAL = Counter(
    "sqlite_busy_timeouts_total",
    "Total SQLite busy timeout errors during write operations",
)

CONTEXT_STEP_TIMEOUT_TOTAL = Counter(
    "context_step_timeout_total",
    "Total best-effort context branch timeouts",
    labelnames=["step"],
)

CONTEXT_STEP_ERROR_TOTAL = Counter(
    "context_step_error_total",
    "Total best-effort context branch errors (non-timeout)",
    labelnames=["step"],
)

STARTUP_BEST_EFFORT_FAILURES_TOTAL = Counter(
    "startup_best_effort_failures_total",
    "Total startup best-effort task failures",
    labelnames=["task"],
)

# ---------------------------------------------------------------------------
# RAG routing & retrieval quality
# ---------------------------------------------------------------------------

SUMMARY_ROUTER_REQUEST_TOTAL = Counter(
    "summary_router_request_total",
    "Summary router invocations by result type",
    labelnames=["result"],  # hit | miss | error | disabled
)

SUMMARY_ROUTER_FILES_ROUTED = Histogram(
    "summary_router_files_routed",
    "Number of files returned by summary router on hit",
    buckets=(0, 2, 4, 8, 12, 16, 24, 50),
)

ANCHOR_HIT_TOTAL = Counter(
    "anchor_hit_total",
    "Conversational anchor state at retrieval time",
    labelnames=["result"],  # fresh | missing | disabled
)

FAIR_RETRIEVE_CHUNKS_PER_FILE = Histogram(
    "fair_retrieve_chunks_per_file",
    "Chunks allocated per file in fair retrieval",
    buckets=(1, 2, 4, 8, 12, 16),
)

FAIR_RETRIEVE_CACHE_HITS = Counter(
    "fair_retrieve_cache_hits_total",
    "Cache hits in fair retrieval (Chroma call skipped)",
    labelnames=["result"],  # hit | miss
)

FUNNEL_NARROW_ANCHOR_EVICT = Counter(
    "funnel_narrow_anchor_evict_total",
    "Files evicted to make room for anchor files in funnel narrow",
)

FUNNEL_NARROW_ROUTER_OVERLAP_RATIO = Histogram(
    "funnel_narrow_router_overlap_ratio",
    "Overlap ratio between router scope and funnel filtered scope "
    "(|router ∩ funnel| / target_files); long-term low values signal "
    "summary/chunk drift",
    buckets=(0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.75, 0.9, 1.0),
)

PRE_RERANK_DEDUP_DROPPED = Counter(
    "pre_rerank_dedup_dropped_total",
    "Chunks dropped by the pre-rerank near-duplicate filter (cost saver)",
)

SUMMARY_VECTOR_UPSERT_FAILURES_TOTAL = Counter(
    "summary_vector_upsert_failures_total",
    "Total failed summary vector upserts (fail-open: router falls back to funnel)",
    labelnames=["collection", "reason"],
)

SUMMARY_COVERAGE_RATIO = Gauge(
    "summary_coverage_ratio",
    "Ratio of meeting files with a summary vector present vs total files with a DB summary",
)

MEETING_SUMMARY_ROUTER_HITS = Counter(
    "meeting_summary_router_hits_total",
    "Number of meetings matched by the meeting summary router (Phase 0)",
    labelnames=["result"],  # narrowed | fail_open
)

ANCHOR_TTL_REFRESH_TOTAL = Counter(
    "anchor_ttl_refresh_total",
    "Anchor TTL refreshed events (sliding mode only)",
    labelnames=["result"],  # refreshed | skipped
)

BROAD_RECALL_MQ_VARIANT_RUNS = Counter(
    "broad_recall_mq_variant_runs_total",
    "Per-variant funnel runs in broad-recall multi-query mode",
)

FUNNEL_NARROW_MULTIMODAL_DOCS = Histogram(
    "funnel_narrow_multimodal_docs",
    "Number of multimodal docs merged into the funnel wide-fetch pool",
    buckets=(0, 1, 2, 4, 8, 16, 32, 64),
)

FUNNEL_NARROW_EVIDENCE_FILTER_RATIO = Histogram(
    "funnel_narrow_evidence_filter_ratio",
    "Fraction of funnel candidates surviving the evidence threshold filter "
    "(len(filtered) / len(candidates)); low values signal an over-aggressive threshold",
    buckets=(0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
)

FUNNEL_NARROW_WIDE_K_USED = Histogram(
    "funnel_narrow_wide_k_used",
    "Actual wide_k used in the funnel wide-fetch (base x log_factor, clamped)",
    buckets=(10, 20, 40, 60, 80, 100, 120, 150, 200),
)

FUNNEL_NARROW_SCOPE_SIZE = Histogram(
    "funnel_narrow_scope_size",
    "Files in the final broad-recall scope after funnel narrow + anchor inject",
    buckets=(1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 16),
)

RERANKER_DURATION_SECONDS = Histogram(
    "reranker_duration_seconds",
    "Reranker (Cohere/BGE) call latency in seconds",
    buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
)

BROAD_RECALL_MQ_UNIQUE_FILES_ADDED = Histogram(
    "broad_recall_mq_unique_files_added",
    "Files added to the merged scope purely from multi-query variant diversity "
    "(not in primary-query scope); measures actual H2 contribution",
    buckets=(0, 1, 2, 3, 4, 5, 6, 8),
)

FUNNEL_NARROW_ROUTER_ONLY_FILES = Histogram(
    "funnel_narrow_router_only_files",
    "Files in the final scope that came from router only (no funnel evidence); "
    "persistently high values mean funnel is not contributing to file selection",
    buckets=(0, 1, 2, 3, 4, 5, 6, 8),
)

# ---------------------------------------------------------------------------
# Application info
# ---------------------------------------------------------------------------

APP_INFO = Counter(
    "app_info_total",
    "Application metadata (always 1, use labels for version info)",
    labelnames=["version"],
)

# ---------------------------------------------------------------------------
# Memory lifecycle
# ---------------------------------------------------------------------------

MEMORY_EXTRACT_TOTAL = Counter(
    "memory_extract_total",
    "Total memory fact extractions",
    labelnames=["status"],  # success | error | skipped | circuit_open
)

MEMORY_EXTRACT_FACTS = Histogram(
    "memory_extract_facts",
    "Number of facts extracted per turn",
    buckets=(0, 1, 2, 3, 5, 8, 12),
)

MEMORY_MERGE_TOTAL = Counter(
    "memory_merge_total",
    "Total memory merge operations",
    labelnames=["status"],  # success | error | skipped
)

MEMORY_EVICT_TOTAL = Counter(
    "memory_evict_total",
    "Total memories evicted by cap enforcement",
)

MEMORY_DECAY_RUN_TOTAL = Counter(
    "memory_decay_run_total",
    "Total decay cycle runs",
    labelnames=["status"],  # success | error
)

MEMORY_DECAY_PURGED = Counter(
    "memory_decay_purged_total",
    "Total memories purged by decay (importance fell below threshold)",
)

MEMORY_SEARCH_TOTAL = Counter(
    "memory_search_total",
    "Total memory semantic searches",
    labelnames=["status"],  # success | error
)

MEMORY_CIRCUIT_BREAKER_TRIPS = Counter(
    "memory_circuit_breaker_trips_total",
    "Total times the extraction circuit breaker opened",
)

MEMORY_ACTIVE_GAUGE = Gauge(
    "memory_active_count",
    "Current number of active (non-expired) memories per user",
    labelnames=["user_id"],
)

# ---------------------------------------------------------------------------
# Observability: background task health, WAL, vector stores
# ---------------------------------------------------------------------------

BG_TASK_AGE_SECONDS = Gauge(
    "bg_task_age_seconds",
    "Age of the oldest in-flight background task (stuck detection)",
    labelnames=["kind"],
)

DB_READ_TX_AGE_SECONDS = Histogram(
    "db_read_tx_age_seconds",
    "Duration of read transactions (long reads block WAL truncation)",
    buckets=(0.01, 0.1, 1.0, 5.0, 15.0, 30.0, 60.0, 120.0),
)

WAL_CHECKPOINT_FAILURES_TOTAL = Counter(
    "wal_checkpoint_failures_total",
    "Total WAL checkpoint failures (timeout or error)",
)

VECTORSTORE_ORPHAN_TOTAL = Counter(
    "vectorstore_orphan_total",
    "Total orphaned vectors detected during reconciliation",
    labelnames=["collection"],
)

EMBEDDER_CACHE_HIT_TOTAL = Counter(
    "embedder_cache_hit_total",
    "Total embedding query cache hits",
)

EMBEDDER_CACHE_MISS_TOTAL = Counter(
    "embedder_cache_miss_total",
    "Total embedding query cache misses",
)

RAGANYTHING_FALLBACK_TOTAL = Counter(
    "raganything_fallback_total",
    "Times RAGAnything retrieval failed and fell back to native hybrid",
)

RERANKER_REQUESTS_TOTAL = Counter(
    "reranker_requests_total",
    "Total reranker invocations (all backends)."
    "  Use with RERANKER_FAILURE_TOTAL for success rate SLO.",
    ["backend"],
)

RERANKER_FAILURE_TOTAL = Counter(
    "reranker_failure_total",
    "Total reranker failures (all backends)",
    ["backend"],
)

RERANKER_LOW_QUALITY_FALLBACK_TOTAL = Counter(
    "reranker_low_quality_fallback_total",
    "Times reranker returned results below min_score and fell back to top_n",
    ["collection"],
)

RAG_DOCS_AT_STAGE = Histogram(
    "rag_docs_at_stage",
    "Number of documents at each RAG pipeline stage, for funnel SLO."
    "  Labels: stage=retrieved|deduped|reranked|truncated",
    ["stage"],
    buckets=(0, 1, 2, 4, 8, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256),
)

MEMORY_EXTRACT_DROPPED_TOTAL = Counter(
    "memory_extract_dropped_total",
    "Facts dropped during memory extraction",
    ["reason"],
)

RAG_ZERO_RESULT_TOTAL = Counter(
    "rag_zero_result_total",
    "Total RAG queries returning zero results",
    ["scoped"],
)

RRF_FALLBACK_KEY_TOTAL = Counter(
    "rrf_fallback_key_total",
    "Total RRF dedup fallbacks to content hash (chunk_id missing in metadata)",
)

# ---------------------------------------------------------------------------
# SSE streaming heartbeat
# ---------------------------------------------------------------------------

SSE_HEARTBEAT_STALLED_TOTAL = Counter(
    "meeting_agent_sse_heartbeat_stalled_total",
    "Total times a heartbeat could not be enqueued (queue full), indicating consumer stall",
)

PENDING_VECTOR_DELETIONS_DEAD_LETTER_TOTAL = Counter(
    "pending_vector_deletions_dead_letter_total",
    "Total vector deletions moved to dead letter after exceeding max attempts",
)

# ---------------------------------------------------------------------------
# Knowledge graph (audit: HIGH-14, MED-12)
# ---------------------------------------------------------------------------

KG_UNKNOWN_TYPE_TOTAL = Counter(
    "kg_unknown_entity_type_total",
    "Total times LLM returned an unrecognized entity_type, falling back to 'concept'",
    labelnames=["raw_type"],
)

KG_CANONICAL_RESOLVE_FAILED_TOTAL = Counter(
    "kg_canonical_resolve_failed_total",
    "Total failures resolving canonical entity via vector similarity",
)

KG_VECTOR_UPSERT_FAILED_TOTAL = Counter(
    "kg_vector_upsert_failed_total",
    "Total failed KG vector upserts (SQL row committed but vector missing)",
)

# ---------------------------------------------------------------------------
# Recovery (audit: HIGH-3)
# ---------------------------------------------------------------------------

RECOVERY_KILLED_ACTIVE_SUSPECT_TOTAL = Counter(
    "meeting_recovery_killed_active_suspect_total",
    "Total meetings recovered to 'failed' that may have had an active worker",
)

FUNNEL_NARROW_EMPTY_SCOPE_TOTAL = Counter(
    "funnel_narrow_empty_scope_total",
    "Times funnel_narrow returned an empty ScopeSelection (all candidates filtered out)",
)

VISION_DEDUP_FAILURES_TOTAL = Counter(
    "vision_dedup_failures_total",
    "Times the vision caption/OCR dedup LLM call failed",
)
