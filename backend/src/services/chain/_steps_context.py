import asyncio

from ...core import database as db
from ...core.config import settings
from ...core.memory_admission import is_reference_memory
from ._common import logger
from ._context import PipelineContext
from ._steps_session import MAX_HISTORY_TOKENS, sanitize_history_messages

_MEMORY_MAX_ITEMS = 8


def _historical_cutoffs(ctx: PipelineContext) -> tuple[str, ...]:
    """Return every explicit business-time snapshot requested by the user."""
    if not ctx.query_plan:
        if ctx.valid_at is not None:
            return (ctx.valid_at.isoformat(),)
        return ()
    valid_at = getattr(ctx.query_plan, "valid_at", None)
    if valid_at is not None:
        return (valid_at.isoformat(),)
    configured = getattr(ctx.query_plan, "historical_cutoffs", ())
    if configured:
        return tuple(value.isoformat() for value in configured)
    # Compatibility for callers/tests that construct a legacy query-plan
    # shape. Re-parse the original question rather than treating an API
    # document date filter as a memory snapshot.
    from ..rag._query_plan import infer_historical_cutoffs

    inferred = infer_historical_cutoffs(ctx.question)
    if inferred:
        return tuple(value.isoformat() for value in inferred)
    return ()


def _historical_cutoff(ctx: PipelineContext) -> str | None:
    cutoffs = _historical_cutoffs(ctx)
    if cutoffs:
        return cutoffs[-1]
    known_at = getattr(ctx.query_plan, "known_at", None) if ctx.query_plan else None
    return known_at.isoformat() if known_at is not None else None


def _structured_memory_types(question: str) -> list[str]:
    folded = question.casefold()
    types: list[str] = []
    if any(term in folded for term in ("decision", "decided", "决策", "决定", "结论")):
        types.append("decision")
    if any(
        term in folded
        for term in (
            "action item",
            "todo",
            "to-do",
            "owner",
            "assignee",
            "deadline",
            "task",
            "assigned",
            "overdue",
            "incomplete",
            "open item",
            "待办",
            "行动项",
            "负责人",
            "截止",
            "任务",
            "分配",
            "逾期",
            "未完成",
        )
    ):
        types.append("action_item")
    if any(
        term in folded
        for term in (
            "current status",
            "latest status",
            "project status",
            "project owner",
            "risk",
            "dependency",
            "负责人",
            "当前状态",
            "最新状态",
            "风险",
            "依赖",
        )
    ):
        types.append("project_fact")
    return list(dict.fromkeys(types))


async def load_memories(ctx: PipelineContext) -> None:
    """Load relevant user memories using semantic + importance-weighted retrieval."""
    from ..memory import memory_service

    override = getattr(ctx, "memory_scope_override", None)
    memory_file_ids = (list(override) or None) if override is not None else ctx.file_ids
    ctx.trace.start_span("load_memories", "memory", memory_mode=ctx.memory_mode)
    if ctx.memory_mode == "off":
        ctx.trace.finish_span("load_memories", "skipped")
        return
    try:
        branch_ancestors: set[str] = set()
        if ctx.session_id:

            def _load_ancestors() -> set[str]:
                with db.get_connection() as conn:
                    return db.get_session_ancestor_ids(
                        conn, ctx.session_id or "", user_id=ctx.user_id
                    )

            branch_ancestors = await asyncio.to_thread(_load_ancestors)
        historical_cutoffs = _historical_cutoffs(ctx)
        known_at_value = getattr(ctx.query_plan, "known_at", None) if ctx.query_plan else None
        known_at = known_at_value.isoformat() if known_at_value is not None else None
        if known_at and not historical_cutoffs:
            # Asking what the system knew at T implies evaluating facts at T
            # unless the caller supplies a separate business-time snapshot.
            historical_cutoffs = (known_at,)
        historical_cutoff = historical_cutoffs[-1] if historical_cutoffs else None
        # Load user profile if available
        # Profiles currently have no historical snapshot API. Omitting them is
        # safer than leaking a present-day preference into an as-of answer.
        profile_text = (
            None
            if historical_cutoff
            else await asyncio.to_thread(
                memory_service.get,
                ctx.user_id,
                "__profile__",
                excluded_session_ids=branch_ancestors,
            )
        )
        from ...core.untrusted_material import has_embedded_directive

        if profile_text and not has_embedded_directive(profile_text):
            ctx.memory_context = f"[User Profile Summary]\n{profile_text}\n\n"

        # Exact typed retrieval comes first for task-state queries.  Vector
        # similarity is still used below for semantic context, but it cannot
        # silently drop action items or decisions from an "all" request.
        structured_rows: list[dict] = []
        structured_total = 0
        structured_types = _structured_memory_types(ctx.question)
        if historical_cutoff and not structured_types:
            # Historical recall must use one coherent snapshot. Include every
            # typed fact family instead of falling back to current vectors.
            structured_types = [
                "fact",
                "preference",
                "project_fact",
                "decision",
                "action_item",
            ]
        if structured_types:
            structured_limit = (
                200 if ctx.query_plan and ctx.query_plan.intent == "exhaustive" else 20
            )

            def _load_structured() -> tuple[list[dict], int]:
                with db.get_connection() as conn:
                    if not historical_cutoffs:
                        return db.search_structured_memories(
                            conn,
                            user_id=ctx.user_id,
                            fact_types=structured_types,
                            meeting_ids=ctx.meeting_ids,
                            file_ids=memory_file_ids,
                            include_unscoped=not settings.SCOPED_MEMORY_STRICT,
                            query_text=ctx.question,
                            project_ids=getattr(ctx.query_plan, "project_ids", None),
                            action_constraints=getattr(ctx.query_plan, "action_constraints", None),
                            limit=structured_limit,
                        )
                    rows: list[dict] = []
                    total = 0
                    for cutoff in historical_cutoffs:
                        snapshot_rows, snapshot_total = db.search_structured_memories(
                            conn,
                            user_id=ctx.user_id,
                            fact_types=structured_types,
                            meeting_ids=ctx.meeting_ids,
                            file_ids=memory_file_ids,
                            include_unscoped=not settings.SCOPED_MEMORY_STRICT,
                            query_text=ctx.question,
                            project_ids=getattr(ctx.query_plan, "project_ids", None),
                            action_constraints=getattr(ctx.query_plan, "action_constraints", None),
                            as_of=cutoff,
                            known_at=known_at,
                            limit=structured_limit,
                        )
                        rows.extend({**row, "snapshot_at": cutoff} for row in snapshot_rows)
                        total += snapshot_total
                    return rows, total

            structured_rows, structured_total = await asyncio.to_thread(_load_structured)
            if branch_ancestors:
                structured_rows = [
                    row for row in structured_rows if row.get("session_id") not in branch_ancestors
                ]
                structured_total = len(structured_rows)

        # Load relevant individual memories
        query = ctx.rewritten_query or ctx.question
        from ._query_routes import is_recorded_fact_request

        if is_recorded_fact_request(ctx.question, ctx.memory_mode) and not structured_rows:
            ctx.memory_context += (
                "[The recorded-facts query returned zero matching records. This does not "
                "prove that the source meetings contain no tasks or decisions.]\n"
            )
        entries = (
            []
            if historical_cutoff or is_recorded_fact_request(ctx.question, ctx.memory_mode)
            else await memory_service.search_semantic(
                ctx.user_id,
                query=query,
                limit=min(settings.MEMORY_MAX_CONTEXT_ITEMS, _MEMORY_MAX_ITEMS),
                min_importance=settings.MEMORY_MIN_IMPORTANCE,
                meeting_ids=ctx.meeting_ids,
                file_ids=memory_file_ids,
                exclude_reference=True,
                project_ids=getattr(ctx.query_plan, "project_ids", ()),
                action_constraints=getattr(ctx.query_plan, "action_constraints", None),
            )
        )
        if branch_ancestors:
            entries = [
                entry
                for entry in entries
                if getattr(entry, "metadata", {}).get("session_id") not in branch_ancestors
            ]
        if entries or structured_rows:
            # The profile has its own dedicated prompt section above.  Keeping
            # it out of generic recall avoids duplicated (and potentially
            # conflicting) copies in the same prompt.
            entries = [entry for entry in entries if entry.key != "__profile__"]
            entries = [
                entry
                for entry in entries
                if not is_reference_memory(
                    {
                        **getattr(entry, "metadata", {}),
                        "key": entry.key,
                        "source": getattr(entry, "source", "unknown"),
                        "category": getattr(entry, "category", None),
                    }
                )
            ]
            structured_rows = [row for row in structured_rows if not is_reference_memory(row)]
            project_ids = getattr(ctx.query_plan, "project_ids", ())
            if project_ids:
                entries = [
                    entry
                    for entry in entries
                    if getattr(entry, "metadata", {}).get("project_id") in project_ids
                ]
            constraints = getattr(ctx.query_plan, "action_constraints", None)
            if constraints is not None:
                entries = [
                    entry
                    for entry in entries
                    if getattr(entry, "metadata", {}).get("fact_type") != "action_item"
                    or constraints.matches(getattr(entry, "metadata", {}).get("action_status"))
                ]
                if constraints.overdue:
                    # Overdue is an exact SQL date predicate, not semantic similarity.
                    entries = [
                        entry
                        for entry in entries
                        if getattr(entry, "metadata", {}).get("fact_type") != "action_item"
                    ]
            semantic_memories = [
                {
                    "key": e.key,
                    "value": e.value,
                    "source": getattr(e, "source", "unknown"),
                    "confidence": getattr(e, "confidence", 1.0),
                    "fact_type": getattr(e, "metadata", {}).get("fact_type", "fact"),
                    "assertion_status": getattr(e, "metadata", {}).get(
                        "assertion_status", "confirmed"
                    ),
                    "project_id": getattr(e, "metadata", {}).get("project_id"),
                    "action_status": getattr(e, "metadata", {}).get("action_status"),
                    "assignee": getattr(e, "metadata", {}).get("assignee"),
                    "due_at": getattr(e, "metadata", {}).get("due_at"),
                    "valid_from": getattr(e, "metadata", {}).get("valid_from"),
                    "valid_to": getattr(e, "metadata", {}).get("valid_to"),
                    "evidence_excerpt": getattr(e, "metadata", {}).get("evidence_excerpt"),
                    "evidence_refs": getattr(e, "metadata", {}).get("evidence_refs"),
                    "revision": getattr(e, "metadata", {}).get("revision"),
                    "conflicts_with": getattr(e, "metadata", {}).get("conflicts_with"),
                    "meeting_ids": getattr(e, "meeting_ids", None),
                    "file_ids": getattr(e, "file_ids", None),
                }
                for e in entries
            ]
            structured_keys = {str(row["key"]) for row in structured_rows}
            memories = [*structured_rows]
            memories.extend(
                memory for memory in semantic_memories if str(memory["key"]) not in structured_keys
            )
            if structured_types:
                ctx.memory_context += (
                    f"[Structured memory coverage: returned {len(structured_rows)} of "
                    f"{structured_total}; types={','.join(structured_types)}]\n"
                )
                ctx.memory_context += (
                    "[Coverage is for recorded facts only, not exhaustive source extraction. "
                    "Use Memory > Decisions & tasks for the paginated authoritative list.]\n"
                )
            if project_ids:
                ctx.memory_context += (
                    f"[Resolved project candidates: {', '.join(project_ids)}; "
                    "keep their facts separate.]\n"
                )
        if entries or structured_rows:
            from ..memory.evidence_admission import filter_context_memories

            admitted = await asyncio.to_thread(filter_context_memories, memories, ctx.user_id)
            if len(admitted) != len(memories):
                ctx.memory_context += (
                    f"[Withheld {len(memories) - len(admitted)} memories: "
                    "evidence requires review.]\n"
                )
            memories = admitted
            ctx.memory_context += _format_memory_context(memories)
            # Commit recall side effects only after a valid answer is persisted.
            # A failed/empty generation must not reinforce long-term memory.
            admitted_keys = {m["key"] for m in memories}
            ctx.recalled_memory_entries = [
                row for row in structured_rows if row["key"] in admitted_keys
            ]
            ctx.recalled_memory_entries.extend(
                entry for entry in entries if entry.key in admitted_keys
            )
            from ._memory_sources import memory_evidence_sources

            ctx.memory_sources = await asyncio.to_thread(
                memory_evidence_sources, memories, ctx.user_id
            )
        ctx.trace.finish_span("load_memories")
    except Exception as _trace_exc:
        ctx.trace.finish_span("load_memories", "error", error=_trace_exc)
        logger.warning("Failed to load user memories", exc_info=True)


def commit_memory_recall_side_effects(ctx: PipelineContext) -> None:
    """Record memory access for a successful answer without self-reinforcement."""
    if not ctx.recalled_memory_entries:
        return
    entries = list(ctx.recalled_memory_entries)
    # Recall is an observation, not evidence that a memory is true or useful.
    # Record access/freshness only; importance promotion is reserved for
    # explicit confirmation and contradiction-resolution write paths.
    with db.get_write_connection() as conn:
        for entry in entries:
            key = entry.get("key") if isinstance(entry, dict) else getattr(entry, "key", None)
            if isinstance(key, str):
                db.touch_memory_access(conn, user_id=ctx.user_id, key=key)
    ctx.recalled_memory_entries.clear()


async def load_entity_context(ctx: PipelineContext) -> None:
    """Load knowledge-graph entity context relevant to the current query."""
    if ctx.memory_mode == "off":
        ctx.trace.start_span(
            "load_entity_context",
            "memory",
            skipped=True,
            memory_mode=ctx.memory_mode,
            skip_reason="memory_mode_off",
        )
        return
    if _historical_cutoff(ctx):
        # The KG is a materialized current view and cannot yet honor valid-time
        # snapshots. Do not contaminate a historical answer with newer edges.
        ctx.trace.start_span(
            "load_entity_context",
            "memory",
            skipped=True,
            memory_mode=ctx.memory_mode,
            skip_reason="historical_snapshot_unsupported",
        ).finish("skipped")
        return
    ctx.trace.start_span("load_entity_context", "memory", memory_mode=ctx.memory_mode)
    try:
        from ..knowledge_graph import kg_service

        query = ctx.rewritten_query or ctx.question
        branch_ancestors: set[str] = set()
        if ctx.session_id:

            def _load_ancestors() -> set[str]:
                with db.get_connection() as conn:
                    return db.get_session_ancestor_ids(
                        conn, ctx.session_id or "", user_id=ctx.user_id
                    )

            branch_ancestors = await asyncio.to_thread(_load_ancestors)
        if branch_ancestors:
            ctx.entity_context = await kg_service.get_entity_context(
                ctx.user_id,
                query,
                meeting_ids=ctx.meeting_ids,
                file_ids=ctx.file_ids,
                excluded_session_ids=branch_ancestors,
            )
        else:
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
    if ctx.memory_mode == "off":
        ctx.trace.start_span(
            "load_session_context",
            "session_context",
            skipped=True,
            memory_mode=ctx.memory_mode,
            skip_reason="memory_mode_off",
        )
        return
    if _historical_cutoff(ctx):
        # Session-summary vectors are also a current materialized view. Their
        # created_at metadata is insufficient to reconstruct an as-of snapshot.
        ctx.trace.start_span(
            "load_session_context",
            "session_context",
            skipped=True,
            memory_mode=ctx.memory_mode,
            skip_reason="historical_snapshot_unsupported",
        ).finish("skipped")
        return
    if not settings.SESSION_SUMMARY_ENABLED:
        ctx.trace.start_span(
            "load_session_context",
            "session_context",
            skipped=True,
            memory_mode=ctx.memory_mode,
            skip_reason="session_summaries_disabled",
        )
        return
    history_sensitive = bool(
        (ctx.query_plan and ctx.query_plan.intent in {"comparison", "exhaustive"})
        or any(
            marker in ctx.question.casefold()
            for marker in (
                "history",
                "previous",
                "before",
                "changed",
                "change over time",
                "历史",
                "之前",
                "以前",
                "变化",
                "变更",
                "上次",
            )
        )
    )
    if (
        not history_sensitive
        and ctx.meeting_ids
        and len(ctx.meeting_ids) <= settings.SESSION_CONTEXT_SKIP_THRESHOLD
    ):
        logger.debug(
            "Skipping session context for tight meeting scope (size=%d, threshold=%d)",
            len(ctx.meeting_ids),
            settings.SESSION_CONTEXT_SKIP_THRESHOLD,
        )
        ctx.trace.start_span(
            "load_session_context",
            "session_context",
            skipped=True,
            memory_mode=ctx.memory_mode,
            skip_reason="tight_meeting_scope",
        )
        return
    from ..memory import session_summary_service

    ctx.trace.start_span("load_session_context", "session_context", memory_mode=ctx.memory_mode)
    try:
        query = ctx.rewritten_query or ctx.question
        summaries = await session_summary_service.search_sessions(
            ctx.user_id,
            query=query,
            limit=settings.SESSION_SUMMARY_MAX_ITEMS,
            meeting_ids=ctx.meeting_ids,
            file_ids=ctx.file_ids,
        )
        # A branch is a replacement history. Recalling summaries from any
        # ancestor can re-introduce content the user explicitly edited or
        # withdrew, so those sessions share the current-session exclusion.
        excluded_session_ids = {ctx.session_id} if ctx.session_id else set()
        if ctx.session_id:

            def _load_ancestors() -> set[str]:
                with db.get_connection() as conn:
                    return db.get_session_ancestor_ids(
                        conn, ctx.session_id or "", user_id=ctx.user_id
                    )

            excluded_session_ids.update(await asyncio.to_thread(_load_ancestors))
        summaries = [s for s in summaries if s["session_id"] not in excluded_session_ids]
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


def _calibrated_local_confidence(top_doc: dict) -> float | None:
    """Return a validated probability-like confidence signal when one exists.

    Rank-fusion, vector-distance, and reranker scores are relative ranking
    signals, not calibrated answerability probabilities.  They must never be
    used to suppress an explicitly requested web lookup.  Producers that have
    been calibrated on held-out data may opt in with
    ``confidence_kind='calibrated'`` and a bounded ``confidence`` value.
    """
    if top_doc.get("confidence_kind") != "calibrated":
        return None
    try:
        confidence = float(top_doc["confidence"])
    except (KeyError, TypeError, ValueError):
        return None
    if not 0.0 <= confidence <= 1.0:
        return None
    return confidence


def _normalize_top_score(top_doc: dict, raw_score: float) -> float:
    """Normalize ``top_doc['score']`` to higher-is-better in roughly [0, 1].

    The public retriever, funnel, RRF, multimodal adapter, and reranker all emit
    ``score_kind=relevance``.  Explicit ``distance`` remains supported for
    legacy/internal callers, without guessing from unrelated global flags.
    """
    if top_doc.get("score_kind") == "distance":
        from ..rag._vector import normalize_score

        return normalize_score(raw_score, lower_is_better=True)
    return raw_score


async def perform_web_search(ctx: PipelineContext) -> None:
    """Optionally augment context with web search results, filtered by relevance.

    Skips the web search entirely when the top local document's normalized
    confidence is above ``_LOCAL_CONFIDENCE_THRESHOLD`` — the local corpus
    already has a strong answer, so paying for an external call is wasteful.
    """
    mode = ctx.web_search_mode or ("always" if ctx.use_web_search else "off")
    if mode == "off" or not settings.SEARCH_BINDING:
        return
    # Fallback mode may skip the external call only when a producer supplied a
    # genuine calibrated confidence.  Relative RRF/reranker scores do not
    # qualify, even when their top item is normalized to 1.0.
    if mode == "fallback" and ctx.docs:
        confidence = _calibrated_local_confidence(ctx.docs[0])
        if confidence is not None and confidence >= _LOCAL_CONFIDENCE_THRESHOLD:
            logger.debug(
                "Skipping fallback web search: calibrated local confidence %.3f >= %.3f",
                confidence,
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
    older messages are summarized into a compact, explicitly untrusted history
    message, while recent messages are kept verbatim. This preserves long-range
    context without promoting user-derived text to system-instruction priority.
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
        with db.get_connection() as conn:
            session_row = db.get_session(conn, ctx.session_id, user_id=ctx.user_id)
        if session_row and session_row.get("task_state_json"):
            import json

            try:
                decoded = json.loads(str(session_row["task_state_json"]))
                # v4 adds a frozen evidence snapshot. Keep accepting the three
                # earlier versions so existing sessions remain resumable.
                if isinstance(decoded, dict) and decoded.get("schema_version") in (1, 2, 3, 4):
                    ctx.session_task_state = decoded
            except (json.JSONDecodeError, TypeError):
                logger.warning("Ignoring malformed task state for session %s", ctx.session_id)
        from ..memory._history_context import load_incremental_history

        persisted = await load_incremental_history(
            ctx.session_id,
            settings.LLM_MODEL,
            settings.SESSION_MAX_TOKENS,
        )
        if persisted is not None:
            messages, metadata = persisted
            ctx.history_messages = sanitize_history_messages(
                messages,
                min(settings.SESSION_MAX_TOKENS, MAX_HISTORY_TOKENS),
            )
            ctx.trace.finish_span(
                "load_history", "degraded" if metadata.get("backlog") else "success"
            )
            return
        if ctx.raw_history_messages is not None:
            messages = ctx.raw_history_messages
        else:
            history = await asyncio.to_thread(get_session_history, ctx.session_id)
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
    """Format fact values with lifecycle and provenance for safe generation."""
    if not memories:
        return ""
    lines: list[str] = []

    def _scope_values(value: object) -> list[str]:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, (list, tuple, set)):
            return [str(item) for item in value]
        return []

    for memory in memories:
        metadata = [
            str(memory.get("assertion_status") or "confirmed"),
            str(memory.get("fact_type") or "fact"),
            f"source={memory.get('source') or 'unknown'}",
            f"confidence={float(memory.get('confidence', 1.0)):.2f}",
        ]
        if memory.get("project_id"):
            metadata.append(f"project={memory['project_id']}")
        if memory.get("action_status"):
            metadata.append(f"task_status={memory['action_status']}")
        if memory.get("assignee"):
            metadata.append(f"assignee={memory['assignee']}")
        if memory.get("due_at"):
            metadata.append(f"due={memory['due_at']}")
        if memory.get("valid_from") or memory.get("valid_to"):
            metadata.append(
                f"valid={memory.get('valid_from') or '?'}..{memory.get('valid_to') or '?'}"
            )
        if memory.get("snapshot_at"):
            metadata.append(f"snapshot={memory['snapshot_at']}")
        if memory.get("conflicts_with"):
            metadata.append("has_conflict=true")
        scope: list[str] = []
        meeting_scope = _scope_values(memory.get("meeting_ids"))
        file_scope = _scope_values(memory.get("file_ids"))
        if meeting_scope:
            scope.append("meetings=" + ",".join(meeting_scope))
        if file_scope:
            scope.append("files=" + ",".join(file_scope))
        if scope:
            metadata.append(";".join(scope))
        line = f"[{'|'.join(metadata)}] {memory['key']}={memory['value']}"
        evidence = str(memory.get("evidence_excerpt") or "").strip().replace("\n", " ")
        if evidence:
            line += f" [evidence: {evidence[:240]}]"
        refs = memory.get("evidence_refs")
        if refs:
            import json

            try:
                refs = json.loads(refs) if isinstance(refs, str) else refs
                line += (
                    " [source_refs: "
                    + json.dumps(refs, ensure_ascii=False, separators=(",", ":"))
                    + "]"
                )
            except (ValueError, TypeError):
                line += " [source_refs: unavailable]"
        lines.append(line)
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
