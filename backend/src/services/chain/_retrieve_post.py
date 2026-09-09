"""Post-retrieval steps: rerank, pre-rerank dedup, and near-duplicate suppression.

Extracted from ``_steps_retrieve.py`` to keep that module focused on the
main ``retrieve_documents`` entry point.
"""

import contextlib
import re
import time

from ...core.config import settings
from ...core.metrics import PRE_RERANK_DEDUP_DROPPED, RERANKER_DURATION_SECONDS
from ..rag._query_plan import infer_query_intent
from ._common import logger
from ._context import PipelineContext
from ._retrieve_filters import _apply_content_type_bias
from ._retrieve_utils import (
    _CONTENT_SIMILARITY_THRESHOLD,
    _MMR_LAMBDA,
    _filter_low_information_chunks,
    _ngrams,
)

_FACT_VALUE_RE = re.compile(
    r"(?<!\w)(?:[$€£¥]\s*)?[-+]?\d[\d,]*(?:\.\d+)?(?:\s*%|\s*(?:usd|eur|gbp|cny))?",
    re.IGNORECASE,
)
_NEGATION_RE = re.compile(
    r"\b(?:not|no|never|neither|without|cannot|can't|won't|isn't|aren't|didn't|doesn't)\b|"
    r"(?:不|未|无|没有|禁止|取消|并非|不能)",
    re.IGNORECASE,
)


def _protected_fact_markers(content: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Extract values whose differences must survive fuzzy text deduplication."""
    normalized = content.casefold()
    return tuple(_FACT_VALUE_RE.findall(normalized)), tuple(_NEGATION_RE.findall(normalized))


def _near_duplicate(left: str, right: str, *, threshold: float, n: int) -> bool:
    """Compare text while protecting numeric and negation-bearing facts."""
    if _protected_fact_markers(left) != _protected_fact_markers(right):
        return False
    # Similarity is not factual equivalence. Only punctuation/whitespace
    # variants may be collapsed; names, predicates and CJK facts must survive.
    if re.findall(r"\w+", left.casefold()) != re.findall(r"\w+", right.casefold()):
        return False
    left_ngrams = _ngrams(left, n)
    right_ngrams = _ngrams(right, n)
    if not left_ngrams or not right_ngrams:
        return False
    return len(left_ngrams & right_ngrams) / min(len(left_ngrams), len(right_ngrams)) >= threshold


def _merge_alternate_source(target: dict, duplicate: dict) -> None:
    """Retain all source identities when equivalent evidence is collapsed."""
    keys = (
        "meeting_id",
        "file_id",
        "file_name",
        "chunk_index",
        "page_number",
        "slide_number",
        "timestamp_start",
        "timestamp_end",
        "document_revision",
    )
    target_meta = target.setdefault("metadata", {})
    duplicate_meta = duplicate.get("metadata") or {}
    references = list(target_meta.get("alternate_sources") or [])
    for metadata in (target_meta, duplicate_meta):
        reference = {key: metadata.get(key) for key in keys if metadata.get(key) is not None}
        if reference and reference not in references:
            references.append(reference)
    if len(references) > 1:
        target_meta["alternate_sources"] = references


def rerank_documents(ctx: PipelineContext) -> None:
    """Rerank retrieved documents if reranker is configured.

    Decoupling note: ``RERANKER_TOP_N`` controls how many docs the reranker
    keeps for downstream filters (near-dup suppression, content-type bias).
    The final pool surfaced to the LLM is then truncated to ``ctx.top_k`` so
    the answer prompt size stays predictable even when the reranker pool is
    larger.
    """
    final_top_k = ctx.top_k or settings.TOP_K
    candidate_count = len(ctx.docs)
    binding = settings.RERANKER_BINDING.lower()
    if not ctx.docs:
        ctx.trace.start_span(
            "rerank",
            "rerank",
            skipped=True,
            reason="no_candidates",
            candidate_count=0,
            top_k=final_top_k,
        )
        return
    if not binding:
        ctx.trace.start_span(
            "rerank",
            "rerank",
            skipped=True,
            reason="disabled",
            candidate_count=candidate_count,
            top_k=final_top_k,
        )
        return

    # Skip only when the candidate pool is truly small — there's nothing
    # to winnow. Single-file scope is no longer a skip reason because the
    # most valuable reranking happens when many chunks from one file need
    # narrow-to-top-K selection (FUNNEL-1).
    min_rerank_pool = max(final_top_k * 2, 12)
    if len(ctx.docs) < min_rerank_pool:
        ctx.trace.start_span(
            "rerank",
            "rerank",
            skipped=True,
            reason="small_candidate_set",
            candidate_count=len(ctx.docs),
            top_n=settings.RERANKER_TOP_N,
            top_k=final_top_k,
        )
        return

    from ..rag import rerank as _do_rerank

    span = ctx.trace.start_span(
        "rerank",
        "rerank",
        backend=binding,
        candidate_count=candidate_count,
        requested_top_n=settings.RERANKER_TOP_N,
        top_k=final_top_k,
    )
    query = ctx.rewritten_query or ctx.question
    try:
        is_broad = not ctx.file_ids
        intent = ctx.query_plan.intent if ctx.query_plan else infer_query_intent(ctx.question)
        coverage_intent = intent in {"summary", "comparison", "exhaustive"}
        rerank_top_n = settings.RERANKER_TOP_N
        # FUNNEL-3: dynamic top_n scales with candidate pool size
        # and guarantees file coverage in broad recall mode.
        if is_broad:
            distinct_files = len(
                {
                    (d.get("metadata") or {}).get("file_id")
                    for d in ctx.docs
                    if isinstance((d.get("metadata") or {}).get("file_id"), int)
                }
            )
            rerank_top_n = max(rerank_top_n, len(ctx.docs) // 3, distinct_files)
        _rerank_start = time.monotonic()
        ctx.docs = _do_rerank(
            query,
            ctx.docs,
            top_n=rerank_top_n,
            is_unscoped=is_broad,
            min_per_file=1 if is_broad and coverage_intent else 0,
        )
        reranker_latency_ms = (time.monotonic() - _rerank_start) * 1000
        with contextlib.suppress(Exception):
            RERANKER_DURATION_SECONDS.observe(reranker_latency_ms / 1000)
        if settings.RAG_CONTENT_TYPE_RERANK_ENABLED:
            ctx.docs = _apply_content_type_bias(query, ctx.docs)
        if len(ctx.docs) > final_top_k:
            ctx.docs = ctx.docs[:final_top_k]
        reranked_count = sum(doc.get("reranked") is True for doc in ctx.docs)
        score_values: list[float] = []
        for doc in ctx.docs:
            with contextlib.suppress(TypeError, ValueError):
                score_values.append(float(doc.get("score", 0.0)))
        span.metadata.update(
            {
                "executed": True,
                "output_count": len(ctx.docs),
                "reranked_count": reranked_count,
                "latency_ms": round(reranker_latency_ms, 1),
            }
        )
        if score_values:
            span.metadata.update(
                {
                    "top_score": round(max(score_values), 6),
                    "min_score": round(min(score_values), 6),
                }
            )
        if reranked_count < len(ctx.docs):
            span.metadata["degrade_reason"] = "backend_fallback"
            ctx.trace.finish_span("rerank", "degraded")
        else:
            ctx.trace.finish_span("rerank")
    except Exception as _trace_exc:
        ctx.trace.finish_span("rerank", "error", error=_trace_exc)
        raise


def _adaptive_ngram_size(content: str) -> int:
    """Choose n-gram size based on content length (FUNNEL-2).

    Short text (captions): 3-grams with higher overlap tolerance.
    Medium text (paragraphs): 4-grams (default).
    Long text (full pages): 5-grams for more discriminative matching.
    """
    length = len(content)
    if length < 100:
        return 3
    if length > 1000:
        return 5
    return 4


def pre_rerank_dedup(ctx: PipelineContext) -> None:
    """Drop near-duplicate chunks before reranking to reduce API cost.

    Uses adaptive n-gram sizing based on content length (FUNNEL-2) so that
    short captions and long paragraphs are deduplicated appropriately.
    Gated by ``RAG_PRE_RERANK_DEDUP_ENABLED``.
    """
    if not settings.RAG_PRE_RERANK_DEDUP_ENABLED or len(ctx.docs) <= 1:
        return

    threshold = settings.RAG_PRE_RERANK_DEDUP_THRESHOLD
    kept: list[dict] = []
    kept_contents: list[str] = []

    for doc in ctx.docs:
        content = doc.get("content", "")
        n = _adaptive_ngram_size(content)
        is_dup = False
        for index, existing in enumerate(kept_contents):
            if _near_duplicate(content, existing, threshold=threshold, n=n):
                is_dup = True
                _merge_alternate_source(kept[index], doc)
                break
        if not is_dup:
            kept.append(doc)
            kept_contents.append(content)

    dropped = len(ctx.docs) - len(kept)
    if dropped:
        PRE_RERANK_DEDUP_DROPPED.inc(dropped)
        logger.debug("Pre-rerank dedup: %d -> %d docs", len(ctx.docs), len(kept))
    ctx.docs = kept


def suppress_near_duplicates(ctx: PipelineContext) -> None:
    """Drop near-duplicate chunks after reranking using ngram overlap.

    Keeps the highest-ranked chunk and removes others whose content
    overlap exceeds _CONTENT_SIMILARITY_THRESHOLD.  This prevents
    repetitive chunks from flooding the LLM context.
    """
    ctx.trace.start_span("suppress_near_duplicates", "retrieve")
    try:
        if len(ctx.docs) <= 1:
            _select_final_documents(ctx)
            ctx.trace.finish_span("suppress_near_duplicates")
            return

        kept: list[dict] = []
        kept_contents: list[str] = []

        for doc in ctx.docs:
            content = doc.get("content", "")
            is_dup = False
            for index, existing in enumerate(kept_contents):
                if _near_duplicate(
                    content,
                    existing,
                    threshold=_CONTENT_SIMILARITY_THRESHOLD,
                    n=4,
                ):
                    is_dup = True
                    _merge_alternate_source(kept[index], doc)
                    break

            if not is_dup:
                kept.append(doc)
                kept_contents.append(content)

        kept = _filter_low_information_chunks(kept)

        removed = len(ctx.docs) - len(kept)
        if removed:
            logger.debug("Near-duplicate suppression: %d -> %d docs", len(ctx.docs), len(kept))
        ctx.docs = kept
        _select_final_documents(ctx)
        ctx.trace.finish_span("suppress_near_duplicates")
    except Exception as _trace_exc:
        ctx.trace.finish_span("suppress_near_duplicates", "error", error=_trace_exc)
        raise


def _select_final_documents(ctx: PipelineContext) -> None:
    """Apply a deterministic hard-sized evidence selector.

    Factual queries favour concentrated relevance.  Summary/comparison intents
    add file coverage, but only above a relative relevance floor.  Every path
    obeys the same hard ``top_k`` limit.
    """
    limit = max(1, int(ctx.top_k or settings.TOP_K))
    if len(ctx.docs) <= limit:
        return
    intent = ctx.query_plan.intent if ctx.query_plan else infer_query_intent(ctx.question)
    coverage_intent = intent in {"summary", "comparison", "exhaustive"}

    scores = [float(doc.get("score", 0.0) or 0.0) for doc in ctx.docs]
    top_score = max(scores, default=0.0)
    floor = top_score * 0.35 if top_score > 0 else float("-inf")
    eligible = [doc for doc in ctx.docs if float(doc.get("score", 0.0) or 0.0) >= floor]
    if not eligible:
        eligible = list(ctx.docs)

    if ctx.file_ids or not coverage_intent:
        ctx.docs = _mmr_select(eligible, limit)
        return

    selected: list[dict] = []
    selected_ids: set[int] = set()
    covered_files: set[int] = set()
    for index, doc in enumerate(eligible):
        file_id = (doc.get("metadata") or {}).get("file_id")
        if not isinstance(file_id, int) or file_id in covered_files:
            continue
        selected.append(doc)
        selected_ids.add(index)
        covered_files.add(file_id)
        if len(selected) >= limit:
            break
    if len(selected) < limit:
        for index, doc in enumerate(eligible):
            if index in selected_ids:
                continue
            selected.append(doc)
            if len(selected) >= limit:
                break
    ctx.docs = selected


def _mmr_select(docs: list[dict], limit: int) -> list[dict]:
    """Small deterministic MMR selector using rank relevance and text overlap."""
    if len(docs) <= limit:
        return docs
    selected: list[dict] = []
    remaining = list(enumerate(docs))
    max_score = max(float(doc.get("score", 0.0) or 0.0) for doc in docs)
    min_score = min(float(doc.get("score", 0.0) or 0.0) for doc in docs)
    spread = max(max_score - min_score, 1e-9)
    while remaining and len(selected) < limit:
        best_pos = 0
        best_value = float("-inf")
        for pos, (rank, doc) in enumerate(remaining):
            score = float(doc.get("score", 0.0) or 0.0)
            relevance = (score - min_score) / spread if spread > 1e-8 else 1.0 / (rank + 1)
            grams = _ngrams(doc.get("content", ""), 4)
            redundancy = 0.0
            for kept in selected:
                kept_grams = _ngrams(kept.get("content", ""), 4)
                if grams and kept_grams:
                    overlap = len(grams & kept_grams) / min(len(grams), len(kept_grams))
                    redundancy = max(redundancy, overlap)
            value = _MMR_LAMBDA * relevance - (1.0 - _MMR_LAMBDA) * redundancy
            if value > best_value:
                best_value = value
                best_pos = pos
        _, chosen = remaining.pop(best_pos)
        selected.append(chosen)
    return selected
