import asyncio

from ...core import database as db
from ...core.config import settings
from ._common import logger
from ._context import PipelineContext
from ._steps_session import MAX_HISTORY_TOKENS, sanitize_history_messages

_MEMORY_MIN_IMPORTANCE = 3
_MEMORY_MAX_ITEMS = 8


async def load_memories(ctx: PipelineContext) -> None:
    """Load relevant user memories using semantic + importance-weighted retrieval."""
    from ..memory import memory_service

    ctx.trace.start_span("load_memories", "memory")
    try:
        # Load user profile if available
        profile_text = await asyncio.to_thread(memory_service.get, ctx.user_id, "__profile__")
        if profile_text:
            ctx.memory_context = f"[User Profile Summary]\n{profile_text}\n\n"

        # Load relevant individual memories
        query = ctx.rewritten_query or ctx.question
        entries = await memory_service.search_semantic(
            ctx.user_id,
            query=query,
            limit=min(settings.MEMORY_MAX_CONTEXT_ITEMS, _MEMORY_MAX_ITEMS),
            min_importance=_MEMORY_MIN_IMPORTANCE,
            meeting_ids=ctx.meeting_ids,
            file_ids=ctx.file_ids,
        )
        if entries:
            memories = [{"key": e.key, "value": e.value} for e in entries]
            ctx.memory_context += _format_memory_context(memories)
            # Fire-and-forget: boost importance + touch access in background
            from ..chain import _background_tasks

            keys = [e.key for e in entries]

            def _bg_boost_and_touch() -> None:
                # M-MEM-3: Pass full entry data so boost_recalled skips SQL re-reads.
                memory_service.boost_recalled_entries(ctx.user_id, entries)
                try:
                    from ...core.database import get_write_connection

                    with get_write_connection() as conn:
                        for k in keys:
                            db.touch_memory_access(conn, user_id=ctx.user_id, key=k)
                except Exception:
                    logger.warning("Memory touch batch failed", exc_info=True)

            task = asyncio.create_task(asyncio.to_thread(_bg_boost_and_touch))
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)
        ctx.trace.finish_span("load_memories")
    except Exception as _trace_exc:
        ctx.trace.finish_span("load_memories", "error", error=_trace_exc)
        logger.warning("Failed to load user memories", exc_info=True)


async def load_entity_context(ctx: PipelineContext) -> None:
    """Load knowledge-graph entity context relevant to the current query."""
    ctx.trace.start_span("load_entity_context", "memory")
    try:
        from ..knowledge_graph import kg_service

        query = ctx.rewritten_query or ctx.question
        ctx.entity_context = await kg_service.get_entity_context(
            ctx.user_id,
            query,
            meeting_ids=ctx.meeting_ids,
            file_ids=ctx.file_ids,
        )
        ctx.trace.finish_span("load_entity_context")
    except Exception as _trace_exc:
        ctx.trace.finish_span("load_entity_context", "error", error=_trace_exc)
        logger.warning("Failed to load entity context", exc_info=True)


async def load_session_context(ctx: PipelineContext) -> None:
    """Load relevant prior session summaries for cross-session context."""
    if not settings.SESSION_SUMMARY_ENABLED:
        return
    if ctx.meeting_ids and len(ctx.meeting_ids) <= settings.SESSION_CONTEXT_SKIP_THRESHOLD:
        logger.debug(
            "Skipping session context for tight meeting scope (size=%d, threshold=%d)",
            len(ctx.meeting_ids),
            settings.SESSION_CONTEXT_SKIP_THRESHOLD,
        )
        return
    from ..memory import session_summary_service

    ctx.trace.start_span("load_session_context", "session_context")
    try:
        query = ctx.rewritten_query or ctx.question
        summaries = await session_summary_service.search_sessions(
            ctx.user_id,
            query=query,
            limit=settings.SESSION_SUMMARY_MAX_ITEMS,
            meeting_ids=ctx.meeting_ids,
        )
        # Exclude the current session from results
        summaries = [s for s in summaries if s["session_id"] != ctx.session_id]
        if summaries:
            ctx.session_context = _format_session_context(summaries)
            ctx.past_session_refs = [
                {
                    "session_id": s["session_id"],
                    "title": s.get("title", ""),
                    "created_at": s.get("created_at", ""),
                    "summary_preview": s.get("summary", "")[:200],
                    "score": round(s.get("score", 0), 4),
                }
                for s in summaries
            ]
        ctx.trace.finish_span("load_session_context")
    except Exception as _trace_exc:
        ctx.trace.finish_span("load_session_context", "error", error=_trace_exc)
        logger.warning("Failed to load session context", exc_info=True)


_LOCAL_CONFIDENCE_THRESHOLD = 0.6  # skip web search if top normalized score >= this


def _normalize_top_score(top_doc: dict, raw_score: float) -> float:
    """Normalize ``top_doc['score']`` to higher-is-better in roughly [0, 1].

    Score orientation depends on the retrieval path that produced ``top_doc``:
    - Reranker (``reranked=True``) emits relevance score (higher-better).
    - Funnel (``RAG_HIERARCHICAL_ENABLED``) and hybrid (``HYBRID_SEARCH_ENABLED``)
      pipelines pre-normalize to [0, 1], higher-better.
    - Raw vector retrieval emits whatever the configured ``DISTANCE_METRIC``
      gives. For L2 / cosine that is a *distance* (lower-better); convert via
      ``1 / (1 + raw)`` so the threshold compare keeps the same semantics
      regardless of which path ran.
    """
    if top_doc.get("reranked"):
        return raw_score
    if settings.RAG_HIERARCHICAL_ENABLED or settings.HYBRID_SEARCH_ENABLED:
        return raw_score
    from ..rag._vector import _vector_score_lower_is_better

    if _vector_score_lower_is_better():
        return 1.0 / (1.0 + raw_score)
    return raw_score


async def perform_web_search(ctx: PipelineContext) -> None:
    """Optionally augment context with web search results, filtered by relevance.

    Skips the web search entirely when the top local document's normalized
    confidence is above ``_LOCAL_CONFIDENCE_THRESHOLD`` — the local corpus
    already has a strong answer, so paying for an external call is wasteful.
    """
    if not ctx.use_web_search or not settings.SEARCH_BINDING:
        return
    # Bail out if local results are already high-confidence
    if ctx.docs:
        top = ctx.docs[0]
        raw_score = float(top.get("score", 0))
        normalized = _normalize_top_score(top, raw_score)
        if normalized >= _LOCAL_CONFIDENCE_THRESHOLD:
            logger.debug(
                "Skipping web search: normalized top score %.3f (raw=%.3f) >= %.3f",
                normalized,
                raw_score,
                _LOCAL_CONFIDENCE_THRESHOLD,
            )
            return
    ctx.trace.start_span("web_search", "search")
    try:
        from ..search import format_search_results, web_search

        search_results = await web_search(
            ctx.question,
            max_results=ctx.web_search_results or settings.SEARCH_MAX_RESULTS,
        )
        if search_results:
            # Filter web results by keyword overlap relevance
            filtered = _filter_web_results(ctx.question, search_results)
            if filtered:
                ctx.web_context = format_search_results(filtered)
                ctx.web_results = [
                    {"title": r.title, "url": r.url, "snippet": r.snippet} for r in filtered
                ]
        ctx.trace.finish_span("web_search")
    except Exception as e:
        ctx.trace.finish_span("web_search", "error", error=e)
        logger.warning("Web search failed: %s", e)


def _filter_web_results(question: str, results: list, min_overlap: int = 2) -> list:
    """Filter web search results by keyword overlap with the query.

    Keeps results whose title or snippet shares at least ``min_overlap``
    meaningful words (length > 3) with the question.  If no results pass
    the filter, returns all results unchanged (to avoid dropping everything
    for short queries).
    """
    q_words = {w.lower() for w in question.split() if len(w) > 3}
    if not q_words:
        return results

    passed = []
    for r in results:
        text = f"{r.title} {r.snippet}".lower()
        r_words = {w for w in text.split() if len(w) > 3}
        overlap = len(q_words & r_words)
        if overlap >= min_overlap:
            passed.append(r)

    return passed[:2] if passed else results[:2]  # Cap at 2 web results; fallback to top 2


async def load_history(ctx: PipelineContext) -> None:
    """Load chat history for the session with optional summarization.

    When the history exceeds SESSION_MAX_TOKENS and there are enough messages,
    older messages are summarized into a compact SystemMessage prefix, while
    recent messages are kept verbatim. This preserves long-range context that
    simple truncation would lose.
    """
    from ..memory import get_session_history
    from ..tokenizer import (
        count_messages_tokens,
        summarize_messages,
        truncate_with_summary,
    )

    ctx.trace.start_span("load_history", "history")
    try:
        assert ctx.session_id is not None, "session_id must be set before loading history"
        if ctx.raw_history_messages is not None:
            messages = ctx.raw_history_messages
        else:
            history = get_session_history(ctx.session_id)
            messages = history.messages

        # Apply summarization when token budget is exceeded
        if settings.SESSION_MAX_TOKENS > 0 and len(messages) > 6:
            token_count = count_messages_tokens(messages, settings.LLM_MODEL)
            if token_count > settings.SESSION_MAX_TOKENS:
                # Split into older (to summarize) and recent (keep verbatim)
                older = messages[:-4]
                summary_text = await summarize_messages(older, settings.LLM_MODEL)
                messages = truncate_with_summary(
                    messages,
                    settings.SESSION_MAX_TOKENS,
                    settings.LLM_MODEL,
                    summary_text=summary_text,
                )

        capped_tokens = min(settings.SESSION_MAX_TOKENS, MAX_HISTORY_TOKENS)
        ctx.history_messages = sanitize_history_messages(messages, capped_tokens)
        ctx.trace.finish_span("load_history")
    except Exception as _trace_exc:
        ctx.trace.finish_span("load_history", "error", error=_trace_exc)
        raise


def _format_memory_context(memories: list[dict]) -> str:
    """Format long-term memories as compact key=val lines."""
    if not memories:
        return ""
    lines = [f"{m['key']}={m['value']}" for m in memories]
    return "\n".join(lines)


def _format_session_context(summaries: list[dict]) -> str:
    """Format prior session summaries as compact single-line entries."""
    if not summaries:
        return ""
    lines = []
    for s in summaries:
        sid = s.get("session_id", "?")[:8]
        title = s.get("title", "")
        date = s.get("created_at", "")[:10]
        topics = ",".join(s.get("topics", [])[:3])
        header = f"[{sid}|{title}|{date}"
        if topics:
            header += f"|{topics}"
        lines.append(f"{header}] {s['summary']}")
    return "\n".join(lines)
