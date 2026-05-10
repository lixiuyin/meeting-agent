"""Post-retrieval steps: rerank, pre-rerank dedup, and near-duplicate suppression.

Extracted from ``_steps_retrieve.py`` to keep that module focused on the
main ``retrieve_documents`` entry point.
"""

import contextlib
import time

from ...core.config import settings
from ...core.metrics import PRE_RERANK_DEDUP_DROPPED, RERANKER_DURATION_SECONDS
from ._common import logger
from ._context import PipelineContext
from ._retrieve_filters import _apply_content_type_bias
from ._retrieve_utils import (
    _CONTENT_SIMILARITY_THRESHOLD,
    _filter_low_information_chunks,
    _ngrams,
)


def rerank_documents(ctx: PipelineContext) -> None:
    """Rerank retrieved documents if reranker is configured.

    Decoupling note: ``RERANKER_TOP_N`` controls how many docs the reranker
    keeps for downstream filters (near-dup suppression, content-type bias).
    The final pool surfaced to the LLM is then truncated to ``ctx.top_k`` so
    the answer prompt size stays predictable even when the reranker pool is
    larger.
    """
    if not settings.RERANKER_BINDING or not ctx.docs:
        return

    final_top_k = ctx.top_k or settings.TOP_K
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

    ctx.trace.start_span("rerank", "rerank")
    query = ctx.rewritten_query or ctx.question
    try:
        is_broad = not ctx.file_ids
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
            min_per_file=1 if is_broad else 0,
        )
        with contextlib.suppress(Exception):
            RERANKER_DURATION_SECONDS.observe(time.monotonic() - _rerank_start)
        if settings.RAG_CONTENT_TYPE_RERANK_ENABLED:
            ctx.docs = _apply_content_type_bias(query, ctx.docs)
        # Truncate to final answer size, but never drop the per-file guarantee:
        # in broad recall mode keep at least one chunk per distinct file even
        # if that means the LLM context exceeds ``final_top_k``.
        if is_broad:
            covered_files = {
                (d.get("metadata") or {}).get("file_id")
                for d in ctx.docs
                if isinstance((d.get("metadata") or {}).get("file_id"), int)
            }
            min_floor = max(final_top_k, len(covered_files))
            if len(ctx.docs) > min_floor:
                ctx.docs = ctx.docs[:min_floor]
        elif len(ctx.docs) > final_top_k:
            ctx.docs = ctx.docs[:final_top_k]
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
    kept_ngrams: list[set[str]] = []

    for doc in ctx.docs:
        content = doc.get("content", "")
        n = _adaptive_ngram_size(content)
        doc_ngrams = _ngrams(content, n)
        is_dup = False
        for existing in kept_ngrams:
            if not doc_ngrams or not existing:
                continue
            denom = min(len(doc_ngrams), len(existing))
            if denom == 0:
                continue
            overlap = len(doc_ngrams & existing) / denom
            if overlap >= threshold:
                is_dup = True
                break
        if not is_dup:
            kept.append(doc)
            kept_ngrams.append(doc_ngrams)

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
            ctx.trace.finish_span("suppress_near_duplicates")
            return

        kept: list[dict] = []
        kept_ngrams: list[set[str]] = []

        for doc in ctx.docs:
            content = doc.get("content", "")
            doc_ngrams = _ngrams(content, 4)

            is_dup = False
            for existing in kept_ngrams:
                if not doc_ngrams or not existing:
                    continue
                overlap = len(doc_ngrams & existing) / min(len(doc_ngrams), len(existing))
                if overlap >= _CONTENT_SIMILARITY_THRESHOLD:
                    is_dup = True
                    break

            if not is_dup:
                kept.append(doc)
                kept_ngrams.append(doc_ngrams)

        kept = _filter_low_information_chunks(kept)

        removed = len(ctx.docs) - len(kept)
        if removed:
            logger.debug("Near-duplicate suppression: %d -> %d docs", len(ctx.docs), len(kept))
        ctx.docs = kept
        ctx.trace.finish_span("suppress_near_duplicates")
    except Exception as _trace_exc:
        ctx.trace.finish_span("suppress_near_duplicates", "error", error=_trace_exc)
        raise
