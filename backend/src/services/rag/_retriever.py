"""Document retrieval: user-scoped vector, BM25, and hybrid RRF strategies."""

from __future__ import annotations

import concurrent.futures
import contextvars
import datetime
import logging
import threading
import time
from typing import Any, cast

from ...core._config_snapshot import submit_with_context
from ...core.config import settings
from ...core.database import get_connection, get_page_sibling_chunks
from ...core.trace import TraceContext
from ._bm25 import _bm25_retrieve

# Re-export submodules for backward compatibility with tests and callers
from ._bm25_maintenance import (  # noqa: F401
    check_and_rebuild_bm25_if_drifted,
    load_bm25_from_database,
    rebuild_bm25_from_chroma,
)
from ._filters import (
    _build_filters,
    _extract_eq_filter,
    _extract_in_filter,
    _resolve_provider,
    _warn_scoped_zero_results,
    _warn_unscoped_zero_results,
)
from ._publication import consistent_index_read
from ._query_analysis import QueryAnalysis, analyze_query
from ._raganything import retrieve_with_raganything
from ._rrf import _rrf_dedup_key, _rrf_merge, _rrf_merge_multi  # noqa: F401
from ._strategies import (
    HybridMultimodalStrategy,
    HybridStrategy,
    MultimodalStrategy,
    NativeStrategy,
    select_strategy,
)
from ._vector import (
    _resolve_parent_chunks,
    _vector_score_lower_is_better,
    normalize_document_scores,
)
from ._vectorstore import get_vectorstore

logger = logging.getLogger(__name__)

_current_speaker_names: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar(
    "_current_speaker_names", default=None
)
_current_lexical_query: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_current_lexical_query", default=None
)


def _trace_retriever_decision(
    trace: TraceContext | None,
    *,
    provider: str,
    scoped: bool,
    fallback_reason: str | None = None,
) -> None:
    if trace is None:
        return
    span = trace.start_span(
        "retriever_decision",
        "retrieve",
        parent_label="retrieve",
        provider=provider,
        scoped=scoped,
        fallback_reason=fallback_reason or "",
    )
    span.finish("success")


@consistent_index_read
def retrieve(
    query: str,
    meeting_ids: list[int] | None = None,
    file_ids: list[int] | None = None,
    top_k: int | None = None,
    fetch_multiplier: int = 1,
    file_types: list[str] | None = None,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    trace: TraceContext | None = None,
    rag_mode: str | None = None,
    known_speakers: list[str] | None = None,
    _apply_diversity: bool = True,
    user_id: str | None = None,
    analysis_query: str | None = None,
    lexical_query: str | None = None,
) -> tuple[list[dict], QueryAnalysis]:
    """Retrieve the most relevant text chunks for a query.

    Supports hybrid search (BM25 + vector) when enabled.
    Performs lightweight query analysis to detect speaker names and
    temporal hints, which are used for filtering and post-retrieval
    boosting.

    Args:
        query: user question
        meeting_ids: restrict to these meetings (empty = all)
        file_ids: restrict to these files (empty = all)
        top_k: number of results to return
        fetch_multiplier: over-fetch factor (for reranking)
        file_types: restrict to these file types (e.g. ["pdf", "video"])
        date_from: ISO date string, restrict to meetings on or after this date
        date_to: ISO date string, restrict to meetings on or before this date
        known_speakers: speaker names from meeting metadata for precise matching

    Returns:
        (docs, query_analysis) where docs is
        [{"content": str, "metadata": dict, "score": float}]
    """
    qa = analyze_query(analysis_query or query, known_speakers)
    if qa.speaker_names:
        logger.info("Query analysis: speakers=%s temporal=%s", qa.speaker_names, qa.temporal_hint)

    k = (top_k or settings.TOP_K) * fetch_multiplier
    threshold = settings.SCORE_THRESHOLD
    pushdown_speakers = (
        qa.speaker_names if qa.speaker_names and settings.RAG_SPEAKER_FILTER_PUSHDOWN else None
    )
    filters = _build_filters(
        meeting_ids,
        file_ids,
        file_types,
        date_from,
        date_to,
        speakers=pushdown_speakers,
        user_id=user_id,
    )

    provider = _resolve_provider(rag_mode)
    scoped = bool(file_ids or meeting_ids)
    # Unscoped diversity mode: over-fetch and cap per-meeting contribution so
    # one hot meeting cannot monopolise the pre-rerank pool on broad queries.
    apply_diversity = (
        _apply_diversity
        and not scoped
        and getattr(settings, "UNSCOPED_DIVERSITY_ENABLED", False)
        and getattr(settings, "UNSCOPED_MAX_PER_MEETING", 0) > 0
    )
    if apply_diversity:
        k = max(k, (top_k or settings.TOP_K) * settings.UNSCOPED_FETCH_MULTIPLIER)
    # MEDIUM-2: Per-file cap for scoped queries prevents single-file monopoly.
    scoped_diversify = scoped and getattr(settings, "SCOPED_MAX_PER_FILE", 0) > 0
    if scoped_diversify:
        k = max(k, (top_k or settings.TOP_K) * 2)

    # The request-scoped fast path uses the already-built local BM25 index.
    # This avoids a remote embedding round trip while retaining all SQL
    # tenant/scope filters.  It is an internal runtime mode; the configured
    # default provider remains vector/hybrid and cannot be set to bm25 through
    # production settings validation.
    if provider == "bm25":
        _trace_retriever_decision(trace, provider="bm25", scoped=scoped)
        token = _current_speaker_names.set(qa.speaker_names if qa.speaker_names else None)
        lexical_token = _current_lexical_query.set(lexical_query or analysis_query or query)
        try:
            results = _bm25_retrieve(
                _current_lexical_query.get(None) or query,
                _extract_in_filter(filters, "meeting_id"),
                _extract_in_filter(filters, "file_id"),
                k,
                trace=trace,
                speaker_names=_current_speaker_names.get(None),
                user_id=_extract_eq_filter(filters, "user_id"),
            )
        finally:
            _current_speaker_names.reset(token)
            _current_lexical_query.reset(lexical_token)
        results = normalize_document_scores(results)
        if not results:
            from ...core.metrics import RAG_ZERO_RESULT_TOTAL

            RAG_ZERO_RESULT_TOTAL.labels(scoped="yes" if scoped else "no").inc()
        return results, qa

    strategy = select_strategy(
        provider,
        native=NativeStrategy(name="vector", run=_run_native_strategy),
        hybrid=HybridStrategy(name="hybrid", run=_run_hybrid_strategy),
        multimodal=MultimodalStrategy(name="multimodal", run=_run_multimodal_strategy),
        hybrid_multimodal=HybridMultimodalStrategy(
            name="hybrid_multimodal",
            run=_run_hybrid_multimodal_strategy,
        ),
    )
    _trace_retriever_decision(trace, provider=strategy.name, scoped=scoped)

    token = _current_speaker_names.set(qa.speaker_names if qa.speaker_names else None)
    lexical_token = _current_lexical_query.set(lexical_query or analysis_query or query)
    try:
        results = strategy.retrieve(
            query=query,
            filters=filters,
            k=k,
            top_k=top_k or settings.TOP_K,
            threshold=threshold,
            scoped=scoped,
            trace=trace,
        )
    finally:
        _current_speaker_names.reset(token)
        _current_lexical_query.reset(lexical_token)

    # Public retrieval boundary: every downstream consumer sees a single,
    # explicit higher-is-better score contract regardless of backend.
    results = normalize_document_scores(results)

    if apply_diversity and results:
        # H-RAG-4: Treat None meeting_id as a unique sentinel so diversity
        # still applies when metadata is incomplete.
        _SENTINEL = object()
        distinct_meetings = {
            doc.get("metadata", {}).get("meeting_id", _SENTINEL) for doc in results
        }
        if len(distinct_meetings) > 1:
            results = _diversify_by_meeting(
                results,
                max_per_meeting=settings.UNSCOPED_MAX_PER_MEETING,
            )
        elif None in distinct_meetings and len(distinct_meetings - {None}) > 0:
            logger.warning(
                "Some docs have meeting_id=None — diversity cap applied with sentinel grouping"
            )
    elif scoped_diversify:
        # MEDIUM-2: Cap per-file chunks in scoped queries to prevent
        # single-file monopoly when one file has vastly more chunks.
        # FUNNEL-5: Skip cap when only one file in scope — capping is
        # counterproductive when all chunks come from the same file.
        distinct_files = {
            (doc.get("metadata") or {}).get("file_id")
            for doc in results
            if isinstance((doc.get("metadata") or {}).get("file_id"), int)
        }
        max_per_file = getattr(settings, "SCOPED_MAX_PER_FILE", 4)
        if max_per_file > 0 and len(distinct_files) > 1:
            results = _diversify_by_meeting(results, max_per_meeting=max_per_file)

    if not results:
        from ...core.metrics import RAG_ZERO_RESULT_TOTAL

        RAG_ZERO_RESULT_TOTAL.labels(scoped="yes" if scoped else "no").inc()
    return results, qa


def _diversify_by_meeting(results: list[dict], *, max_per_meeting: int) -> list[dict]:
    """Cap the number of chunks any single *file* contributes.

    Uses ``(meeting_id, file_id)`` as the key so multi-file meetings
    are fairly represented. Preserves original ranking order within
    each file's allocation.
    """
    if max_per_meeting <= 0:
        return results
    per_file: dict[tuple, int] = {}
    head: list[dict] = []
    tail: list[dict] = []
    for doc in results:
        meta = doc.get("metadata", {})
        mid = meta.get("meeting_id")
        fid = meta.get("file_id")
        key = (mid if isinstance(mid, int) else -1, fid if isinstance(fid, int) else -1)
        count = per_file.get(key, 0)
        if count < max_per_meeting:
            per_file[key] = count + 1
            head.append(doc)
        else:
            tail.append(doc)
    return head + tail


def _run_native_strategy(
    *,
    query: str,
    filters: dict[str, Any],
    k: int,
    top_k: int,
    threshold: float,
    trace: TraceContext | None,
) -> list[dict]:
    _ = top_k
    try:
        return _vector_retrieve(query, filters, k, threshold, trace=trace)
    except Exception:
        logger.warning(
            "Vector retrieval failed, falling back to user-scoped BM25",
            exc_info=True,
        )
        meeting_ids = _extract_in_filter(filters, "meeting_id")
        file_ids = _extract_in_filter(filters, "file_id")
        return _bm25_retrieve(
            _current_lexical_query.get(None) or query,
            meeting_ids,
            file_ids,
            k,
            trace=trace,
            speaker_names=_current_speaker_names.get(None),
            user_id=_extract_eq_filter(filters, "user_id"),
        )


def _run_hybrid_strategy(
    *,
    query: str,
    filters: dict[str, Any],
    k: int,
    top_k: int,
    threshold: float,
    trace: TraceContext | None,
) -> list[dict]:
    alpha = float(settings.HYBRID_ALPHA)
    if alpha <= 0.0:
        return _bm25_retrieve(
            _current_lexical_query.get(None) or query,
            _extract_in_filter(filters, "meeting_id"),
            _extract_in_filter(filters, "file_id"),
            k,
            trace=trace,
            speaker_names=_current_speaker_names.get(None),
            user_id=_extract_eq_filter(filters, "user_id"),
        )
    if alpha >= 1.0:
        return _run_native_strategy(
            query=query,
            filters=filters,
            k=k,
            top_k=top_k,
            threshold=threshold,
            trace=trace,
        )
    return _hybrid_retrieve(query, filters, k, top_k, threshold, trace=trace)


def _run_multimodal_strategy(
    *,
    query: str,
    filters: dict[str, Any],
    k: int,
    top_k: int,
    threshold: float,
    scoped: bool,
    trace: TraceContext | None,
) -> list[dict]:
    if scoped and settings.RAGANYTHING_ENABLED:
        logger.info("Scoped query: bypassing RAGAnything for strict meeting/file filters")
        _trace_retriever_decision(
            trace,
            provider="multimodal",
            scoped=scoped,
            fallback_reason="scoped_bypass",
        )
        return _run_native_strategy(
            query=query, filters=filters, k=k, top_k=top_k, threshold=threshold, trace=trace
        )

    if not settings.RAGANYTHING_ENABLED:
        return _run_native_strategy(
            query=query, filters=filters, k=k, top_k=top_k, threshold=threshold, trace=trace
        )

    try:
        if trace:
            trace.start_span("raganything_search", "retrieve", parent_label="retrieve")
        result = retrieve_with_raganything(query, top_k=k, filters=filters)
        if trace:
            trace.finish_span("raganything_search")
        if result:
            # Post-filter: RAGAnything may not fully honour the filter
            # contract, so ensure every result belongs to the requested
            # scope as a safety net (R-H4).
            if scoped:
                allowed_meeting_ids = filters.get("meeting_id")
                if allowed_meeting_ids:
                    if isinstance(allowed_meeting_ids, int):
                        allowed = {allowed_meeting_ids}
                    else:
                        allowed = set(allowed_meeting_ids)
                    result = [
                        r for r in result if r.get("metadata", {}).get("meeting_id") in allowed
                    ]
            return result
        _trace_retriever_decision(
            trace,
            provider="multimodal",
            scoped=scoped,
            fallback_reason="raganything_empty",
        )
        if settings.RAGANYTHING_FALLBACK_TO_NATIVE:
            logger.warning("RAGAnything retrieval returned empty, falling back to native retriever")
            return _run_native_strategy(
                query=query, filters=filters, k=k, top_k=top_k, threshold=threshold, trace=trace
            )
        return []
    except Exception:
        if trace:
            trace.finish_span("raganything_search", "error")
        if not settings.RAGANYTHING_FALLBACK_TO_NATIVE:
            raise
        logger.warning(
            "RAGAnything retrieval failed, falling back to native retriever",
            exc_info=True,
        )
        _trace_retriever_decision(
            trace,
            provider="multimodal",
            scoped=scoped,
            fallback_reason="raganything_error",
        )
        return _run_native_strategy(
            query=query, filters=filters, k=k, top_k=top_k, threshold=threshold, trace=trace
        )


def _run_hybrid_multimodal_strategy(
    *,
    query: str,
    filters: dict[str, Any],
    k: int,
    top_k: int,
    threshold: float,
    scoped: bool,
    trace: TraceContext | None,
) -> list[dict]:
    if scoped and settings.RAGANYTHING_ENABLED:
        logger.info("Scoped query: bypassing RAGAnything for strict meeting/file filters")
        _trace_retriever_decision(
            trace,
            provider="hybrid_multimodal",
            scoped=scoped,
            fallback_reason="scoped_bypass",
        )
        return _run_native_strategy(
            query=query, filters=filters, k=k, top_k=top_k, threshold=threshold, trace=trace
        )
    if not settings.RAGANYTHING_ENABLED:
        fallback = _run_hybrid_strategy if settings.HYBRID_SEARCH_ENABLED else _run_native_strategy
        return fallback(
            query=query,
            filters=filters,
            k=k,
            top_k=top_k,
            threshold=threshold,
            trace=trace,
        )
    return _hybrid_multimodal_retrieve(query, filters, k, top_k, threshold, trace=trace)


_VECTOR_SEARCH_MAX_RETRIES = 2
_VECTOR_SEARCH_RETRY_DELAY = 0.5

# Dedicated executor so timeouts don't consume slots from the default thread pool.
_VECTOR_SEARCH_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="vector-search"
)

# H-7: Backpressure — cap concurrent vector search submissions to prevent
# unbounded queue growth when all 4 threads are occupied by slow queries.
_VECTOR_SEARCH_CONCURRENCY = 8  # 4 running + 4 queued max
_VECTOR_SEARCH_SEMAPHORE: threading.Semaphore | None = None
_VECTOR_SEARCH_SEMAPHORE_LOCK = threading.Lock()


def _get_vector_search_semaphore() -> threading.Semaphore:
    """Lazily initialise the semaphore (avoids import-time threading concerns)."""
    global _VECTOR_SEARCH_SEMAPHORE
    if _VECTOR_SEARCH_SEMAPHORE is None:
        with _VECTOR_SEARCH_SEMAPHORE_LOCK:
            if _VECTOR_SEARCH_SEMAPHORE is None:
                _VECTOR_SEARCH_SEMAPHORE = threading.Semaphore(_VECTOR_SEARCH_CONCURRENCY)
    return _VECTOR_SEARCH_SEMAPHORE


def _run_with_timeout(func, *args, timeout: float | None, **kwargs):
    """Run a blocking callable with a wall-clock timeout.

    Raises :class:`concurrent.futures.TimeoutError` if the call exceeds the
    timeout. The underlying thread continues running to completion — there is
    no safe way to cancel a blocking HTTP call mid-flight — but control is
    returned to the caller so the pipeline can fall back to BM25.

    H-7: Submission is gated by a semaphore so the executor queue cannot grow
    beyond ``_VECTOR_SEARCH_CONCURRENCY`` pending tasks.
    """
    if timeout is None or timeout <= 0:
        return func(*args, **kwargs)
    sem = _get_vector_search_semaphore()
    acquired = sem.acquire(blocking=True, timeout=timeout)
    if not acquired:
        raise concurrent.futures.TimeoutError(
            "Vector search concurrency limit reached — backpressure applied"
        )
    future = submit_with_context(_VECTOR_SEARCH_EXECUTOR, func, *args, **kwargs)
    release_now = True
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        # A running callable cannot be cancelled. Keep its permit until it
        # actually finishes so repeated timeouts cannot create an unbounded
        # executor queue behind stuck workers.
        if not future.cancel():
            release_now = False
            future.add_done_callback(lambda _future: sem.release())
        raise
    finally:
        if release_now:
            sem.release()


def _vector_retrieve(
    query: str,
    filters: dict[str, Any],
    k: int,
    threshold: float | None = 1.5,
    *,
    trace: TraceContext | None = None,
) -> list[dict]:
    """Standard vector similarity search with score filtering.

    Score interpretation depends on the vectorstore backend/metric. For Chroma
    distance metrics used in this project (l2/cosine), lower scores are better,
    and threshold acts as a max-distance cutoff.

    Pass threshold=None to skip score filtering (used by hybrid RRF).

    Retries transient embedding API errors before falling back to BM25.
    """
    meeting_ids = _extract_in_filter(filters, "meeting_id")
    file_ids = _extract_in_filter(filters, "file_id")
    scoped_query = bool(meeting_ids or file_ids)
    # Scoped retrieval is already constrained by explicit meeting/file IDs.
    # Avoid distance filtering here so exact scope matches are not suppressed.
    effective_threshold = None if scoped_query else threshold

    last_exc: Exception | None = None
    for attempt in range(_VECTOR_SEARCH_MAX_RETRIES + 1):
        try:
            vectorstore = get_vectorstore()

            search_kwargs: dict[str, Any] = {"k": k}
            if filters:
                search_kwargs["filter"] = filters

            if trace and attempt == 0:
                trace.start_span("vector_search", "retrieve", parent_label="retrieve")
            results = _run_with_timeout(
                vectorstore.similarity_search_with_score,
                query,
                timeout=settings.VECTOR_SEARCH_TIMEOUT_S,
                **search_kwargs,
            )
            if trace:
                trace.finish_span("vector_search")
            last_exc = None
            break
        except concurrent.futures.TimeoutError as exc:
            last_exc = exc
            logger.warning(
                "Vector search timed out after %.1fs on attempt %d/%d, falling back to BM25",
                settings.VECTOR_SEARCH_TIMEOUT_S,
                attempt + 1,
                _VECTOR_SEARCH_MAX_RETRIES + 1,
            )
            if trace:
                trace.finish_span("vector_search", "timeout")
            break  # Don't retry timeouts — likely a systemic embedding-service issue.
        except Exception as exc:
            last_exc = exc
            if attempt < _VECTOR_SEARCH_MAX_RETRIES:
                logger.warning(
                    "Vector search attempt %d/%d failed (%s), retrying...",
                    attempt + 1,
                    _VECTOR_SEARCH_MAX_RETRIES + 1,
                    type(exc).__name__,
                )
                time.sleep(min(_VECTOR_SEARCH_RETRY_DELAY * (2**attempt), 1.0))
            else:
                if trace:
                    trace.finish_span("vector_search", "error")
                logger.error(
                    "Vector search failed after %d attempts, falling back to BM25",
                    _VECTOR_SEARCH_MAX_RETRIES + 1,
                    exc_info=True,
                )

    if last_exc is not None:
        bm25_out = _bm25_retrieve(
            _current_lexical_query.get(None) or query,
            meeting_ids,
            file_ids,
            k,
            speaker_names=_current_speaker_names.get(None),
            user_id=_extract_eq_filter(filters, "user_id"),
        )
        if scoped_query and not bm25_out:
            _warn_scoped_zero_results(meeting_ids, file_ids, k)
        return bm25_out

    lower_is_better = _vector_score_lower_is_better()

    if settings.PARENT_CHILD_ENABLED:
        out = _resolve_parent_chunks(vectorstore, results, effective_threshold, lower_is_better)
        score_kind = "distance" if lower_is_better else "relevance"
        out = [{**doc, "score_kind": score_kind} for doc in out]
        if scoped_query and not out:
            _warn_scoped_zero_results(meeting_ids, file_ids, k)
        return out
    # Standard flat mode
    out = []
    from ._contextual import restore_display_content
    from ._vector import source_metadata

    for doc, score in results:
        if effective_threshold is not None:
            if lower_is_better:
                if score > effective_threshold:  # L2: lower is better
                    continue
            elif score < effective_threshold:
                continue
        metadata = source_metadata(doc.metadata, getattr(doc, "id", None))
        out.append(
            {
                "content": restore_display_content(doc.page_content, metadata),
                "metadata": metadata,
                "score": float(score),
                "score_kind": "distance" if lower_is_better else "relevance",
            }
        )
    if scoped_query and not out:
        _warn_scoped_zero_results(meeting_ids, file_ids, k)
    if not scoped_query and not out:
        # Unscoped zero-result diagnostic
        try:
            if hasattr(vectorstore, "_collection"):
                vs_count = vectorstore._collection.count()
            else:
                vs_count = None
        except Exception:
            vs_count = None
        _warn_unscoped_zero_results(k=k, vectorstore_doc_count=vs_count)
    return out


def _hybrid_retrieve(
    query: str,
    filters: dict[str, Any],
    fetch_k: int,
    top_k: int,
    threshold: float,
    *,
    trace: TraceContext | None = None,
) -> list[dict]:
    """Hybrid retrieval: vector + BM25 with RRF merge.

    Threshold is NOT applied to vector results before fusion — this ensures
    BM25-only matches get a fair shake in RRF scoring.

    H-1: Vector and BM25 retrieval run in parallel threads to reduce
    wall-clock latency to max(vector, BM25) instead of their sum.

    H-CONC-3: When BM25 is rebuilding, fall back to pure vector retrieval
    to avoid score distortion from mismatched vector/BM25 data.
    """
    from ._bm25_maintenance import is_bm25_rebuilding

    meeting_ids = _extract_in_filter(filters, "meeting_id")
    file_ids = _extract_in_filter(filters, "file_id")
    speaker_names = _current_speaker_names.get(None)

    if is_bm25_rebuilding():
        logger.info("BM25 rebuild in progress — falling back to pure vector retrieval")
        return _vector_retrieve(query, filters, fetch_k, threshold, trace=trace)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        vector_future = submit_with_context(
            pool,
            _vector_retrieve,
            query,
            filters,
            fetch_k,
            None,
            trace=trace,
        )
        bm25_future = submit_with_context(
            pool,
            _bm25_retrieve,
            _current_lexical_query.get(None) or query,
            meeting_ids,
            file_ids,
            fetch_k,
            trace=trace,
            speaker_names=speaker_names,
            user_id=_extract_eq_filter(filters, "user_id"),
        )
        vector_results = vector_future.result()
        bm25_results = bm25_future.result()

    if trace:
        trace.start_span("rrf_merge", "retrieve", parent_label="retrieve")
    merged = _rrf_merge(vector_results, bm25_results, top_k, fetch_k=fetch_k)
    if trace:
        trace.finish_span("rrf_merge")
    return merged


def _hybrid_multimodal_retrieve(
    query: str,
    filters: dict[str, Any],
    fetch_k: int,
    top_k: int,
    threshold: float,
    *,
    trace: TraceContext | None = None,
) -> list[dict]:
    """Hybrid multimodal retrieval: native vector + RAGAnything with RRF merge."""
    # Fetch without threshold so RRF can rank fairly
    vector_results = _vector_retrieve(query, filters, fetch_k, threshold=None, trace=trace)
    if trace:
        trace.start_span("raganything_search", "retrieve", parent_label="retrieve")
    try:
        raganything_results = retrieve_with_raganything(query, top_k=fetch_k, filters=filters)
        if trace:
            trace.finish_span("raganything_search")
    except Exception:
        if trace:
            trace.finish_span("raganything_search", "error")
        from ...core.metrics import RAGANYTHING_FALLBACK_TOTAL

        RAGANYTHING_FALLBACK_TOTAL.inc()
        logger.error(
            "RAGAnything retrieval failed in hybrid mode; "
            "falling back to native hybrid (vector + BM25)",
            exc_info=True,
        )
        # HIGH-3: Don't drop BM25 — fall back to the full native hybrid path
        # so keyword-heavy queries still get good results.
        mids = _extract_in_filter(filters, "meeting_id")
        fids = _extract_in_filter(filters, "file_id")
        bm25_results = _bm25_retrieve(
            _current_lexical_query.get(None) or query,
            mids,
            fids,
            fetch_k,
            trace=trace,
            user_id=_extract_eq_filter(filters, "user_id"),
        )
        return _rrf_merge(vector_results, bm25_results, top_k)
    if trace:
        trace.start_span("rrf_merge", "retrieve", parent_label="retrieve")
    alpha = getattr(settings, "HYBRID_MULTIMODAL_ALPHA", 0.5)
    merged = _rrf_merge_multi(
        [(vector_results, alpha), (raganything_results, 1.0 - alpha)],
        top_k,
    )
    if trace:
        trace.finish_span("rrf_merge")
    return merged


_MULTIMODAL_TYPES = ("table", "image_caption", "image_ocr", "image_combined")


@consistent_index_read
def retrieve_sibling_chunks(
    docs: list[dict],
    *,
    max_per_anchor: int = 1,
    max_total: int = 4,
) -> list[dict]:
    """Fetch sibling multimodal chunks from the same file/page as top hits."""
    if not docs or max_total <= 0 or max_per_anchor <= 0:
        return []
    seen = {
        f"{d.get('metadata', {}).get('meeting_id')}:{d.get('metadata', {}).get('file_id')}:"
        f"{d.get('metadata', {}).get('chunk_index')}"
        for d in docs
    }
    siblings: list[dict] = []
    try:
        with get_connection() as conn:
            for doc in docs:
                if len(siblings) >= max_total:
                    break
                meta = doc.get("metadata") or {}
                meeting_id = meta.get("meeting_id")
                if not isinstance(meeting_id, int):
                    continue
                file_id = meta.get("file_id")
                page_number = meta.get("page_number")
                chunk_index = meta.get("chunk_index")
                rows = get_page_sibling_chunks(
                    conn,
                    meeting_id=meeting_id,
                    file_id=file_id if isinstance(file_id, int) else None,
                    page_number=page_number if isinstance(page_number, int) else None,
                    exclude_chunk_index=chunk_index if isinstance(chunk_index, int) else None,
                    content_types=list(_MULTIMODAL_TYPES),
                    limit=max_per_anchor,
                )
                for row in rows:
                    row_meta = row.get("metadata") or {}
                    key = (
                        f"{row_meta.get('meeting_id')}:{row_meta.get('file_id')}:"
                        f"{row_meta.get('chunk_index')}"
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    siblings.append(row)
                    if len(siblings) >= max_total:
                        break
    except Exception:
        logger.warning("Sibling co-retrieval failed", exc_info=True)
    if len(siblings) < max_total:
        siblings.extend(
            _vector_sibling_fallback(
                docs,
                already_seen=seen,
                max_total=max_total - len(siblings),
            )
        )
    return siblings


def _vector_sibling_fallback(
    docs: list[dict],
    *,
    already_seen: set[str],
    max_total: int,
) -> list[dict]:
    """Fallback sibling lookup directly from vector store metadata."""
    if max_total <= 0:
        return []
    out: list[dict] = []
    try:
        vectorstore = get_vectorstore()
    except Exception:
        return []
    for doc in docs:
        if len(out) >= max_total:
            break
        meta = doc.get("metadata") or {}
        meeting_id = meta.get("meeting_id")
        page_number = meta.get("page_number")
        if not isinstance(meeting_id, int) or not isinstance(page_number, int):
            continue
        where_clauses: list[dict[str, Any]] = [
            {"meeting_id": meeting_id},
            {"page_number": page_number},
            {"content_type": {"$in": list(_MULTIMODAL_TYPES)}},
        ]
        file_id = meta.get("file_id")
        if isinstance(file_id, int):
            where_clauses.append({"file_id": file_id})
        where = {"$and": where_clauses}
        try:
            payload = vectorstore.get(
                where=cast(Any, where),
                include=["documents", "metadatas"],
                limit=max_total - len(out) + 1,
            )
        except Exception:
            continue
        docs_payload = payload.get("documents") or []
        metas_payload = payload.get("metadatas") or []
        ids_payload = payload.get("ids") or []
        from ._contextual import restore_display_content
        from ._vector import source_metadata

        for index, (content, md) in enumerate(zip(docs_payload, metas_payload, strict=False)):
            md = source_metadata(md, ids_payload[index] if index < len(ids_payload) else None)
            key = f"{md.get('meeting_id')}:{md.get('file_id')}:{md.get('chunk_index')}"
            if key in already_seen:
                continue
            already_seen.add(key)
            out.append(
                {
                    "content": restore_display_content(content, md),
                    "metadata": md,
                    "score": 0.0,
                }
            )
            if len(out) >= max_total:
                break
    return out
