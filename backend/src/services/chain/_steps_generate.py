"""Pipeline steps: build context, generate answer, save messages, schedule extraction.

Helper functions (token stripping, retry wrappers, summary loading, circuit
breaker) live in ``_generate_helpers.py``.
"""

import asyncio
import sqlite3
import time
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.output_parsers import StrOutputParser

from ...core.config import settings
from ...core.database import get_connection
from ._common import logger
from ._context import PipelineContext
from ._generate_helpers import (
    _CHARS_PER_TOKEN,
    _EXTRACTION_CIRCUIT_BREAKER_THRESHOLD,
    _FACT_EXTRACT_BASE_DELAY,
    _FACT_EXTRACT_MAX_RETRIES,
    _HISTORY_BUDGET_TOKENS_DEFAULT,
    _get_failures,
    _increment_failures,
    _invoke_chain_with_retry,
    _invoke_chain_with_retry_multimodal,
    _load_file_summaries,
    _load_meeting_summaries_for_context,
    _reset_failures,
    _strip_internal_tokens,
)


def _estimate_tokens(text: str) -> int:
    return int(len(text) / _CHARS_PER_TOKEN)


def _truncate_text_to_tokens(text: str, max_tokens: int, model: str) -> str:
    """Truncate text to fit within max_tokens.

    Uses a two-phase approach (M-11): a single count_tokens call on the full
    text to compute the actual chars-per-token ratio, then a linear
    interpolation for the initial estimate.  Binary search only runs over the
    narrow gap between that estimate and the target.
    """
    from ..tokenizer import count_tokens

    if not text or max_tokens <= 0:
        return ""
    text_len = len(text)
    full_tokens = count_tokens(text, model)
    if full_tokens <= max_tokens:
        return text

    suffix = "\n...[truncated]"
    suffix_tokens = count_tokens(suffix, model)

    # M-11: Use the actual chars-per-token ratio from the full text for a
    # much tighter initial estimate, reducing binary search iterations from
    # ~17 to ~3 for typical inputs.
    actual_ratio = text_len / full_tokens if full_tokens > 0 else _CHARS_PER_TOKEN
    target_content_tokens = max_tokens - suffix_tokens
    if target_content_tokens <= 0:
        return suffix

    char_estimate = int(target_content_tokens * actual_ratio)
    if char_estimate >= text_len:
        return text  # should not happen, but guard

    best = text[:char_estimate].rstrip() + suffix
    est_tokens = count_tokens(best, model)

    # Narrow the binary search range based on whether the estimate over/undershot.
    if est_tokens > max_tokens:
        low, high = 0, char_estimate
    else:
        low, high = char_estimate, text_len

    while low <= high:
        mid = (low + high) // 2
        candidate = text[:mid].rstrip() + suffix
        if count_tokens(candidate, model) <= max_tokens:
            best = candidate
            low = mid + 1
        else:
            high = mid - 1
    return best


def _load_meeting_titles(meeting_ids: set[int]) -> dict[int, str]:
    """Load meeting titles from DB for display in grouped output."""
    if not meeting_ids:
        return {}
    try:
        with get_connection() as conn:
            placeholders = ",".join("?" for _ in meeting_ids)
            rows = conn.execute(
                f"SELECT id, title FROM meetings WHERE id IN ({placeholders})",
                list(meeting_ids),
            ).fetchall()
            return {r["id"]: r["title"] for r in rows if r["title"]}
    except (sqlite3.DatabaseError, OSError):
        logger.warning("Failed to load meeting titles", exc_info=True)
        return {}


def _inject_speaker_instructions(ctx: PipelineContext, cached_titles: dict[int, str]) -> None:
    """Append per-meeting speaker formatting instructions to combined_context."""
    qa = ctx.query_analysis
    if not qa or not qa.speaker_names or ctx.file_ids:
        return
    mid_set = {
        d.get("metadata", {}).get("meeting_id")
        for d in ctx.docs
        if d.get("metadata", {}).get("meeting_id") is not None
    }
    if len(mid_set) <= 1:
        return
    if not cached_titles:
        cached_titles.update(_load_meeting_titles(mid_set))
    speakers = ", ".join(qa.speaker_names)
    titles_list = ", ".join(f'"{cached_titles.get(m, f"Meeting#{m}")}"' for m in sorted(mid_set))
    ctx.combined_context += (
        f"\n\n[Speaker Query Instructions]\n"
        f"The user is asking about speaker: {speakers}. "
        f"The sources above come from {len(mid_set)} different meetings: "
        f"{titles_list}. "
        f"These meetings are completely independent and unrelated. "
        f"The speaker '{speakers}' in different meetings may be different people "
        f"who happen to share the same name.\n\n"
        f"CRITICAL FORMATTING REQUIREMENT:\n"
        f"You MUST organize your answer into {len(mid_set)} separate sections, "
        f"one for each meeting. Use this exact format:\n\n"
        f'**In "[meeting title]" meeting:**\n'
        f"[Summary of what {speakers} said, citing only sources from this meeting]\n\n"
        f'**In "[other meeting title]" meeting:**\n'
        f"[Summary of what {speakers} said, citing only sources from this meeting]\n\n"
        f"Do NOT merge content from different meetings into one paragraph. "
        f"Do NOT cross-reference between meetings."
    )
    logger.info(
        "build_context: injected per-meeting instructions for %s across %d meetings: %s",
        speakers,
        len(mid_set),
        titles_list,
    )


def build_context(ctx: PipelineContext) -> None:
    """Merge all context sources into a single system context string.

    Applies a token budget to prevent context-window overflow:
    reserves space for prompt overhead, history, and the answer,
    then truncates meeting chunks greedily by rank if needed.
    """
    from ..tokenizer import count_tokens
    from ._formatting import (
        _build_system_context,
        _canonical_citation_docs,
        _format_docs,
        _format_docs_by_meeting,
    )

    ctx.trace.start_span("build_context", "assemble")
    try:
        qa = ctx.query_analysis
        model = settings.LLM_MODEL
        ctx.memory_context = _truncate_text_to_tokens(
            ctx.memory_context, settings.MEMORY_CONTEXT_MAX_TOKENS, model
        )
        ctx.entity_context = _truncate_text_to_tokens(
            ctx.entity_context, settings.ENTITY_CONTEXT_MAX_TOKENS, model
        )
        ctx.session_context = _truncate_text_to_tokens(
            ctx.session_context, settings.SESSION_CONTEXT_MAX_TOKENS, model
        )

        _cached_titles: dict[int, str] = {}

        # Deduplicate/filter docs so _format_docs [N] stays aligned with
        # _extract_sources sources[N-1].
        _original_doc_count = len(ctx.docs)
        ctx.docs = _canonical_citation_docs(ctx.docs)
        _removed = _original_doc_count - len(ctx.docs)
        if _removed:
            logger.info(
                "Citation dedup removed %d redundant docs (kept %d)",
                _removed,
                len(ctx.docs),
            )

        # H-6: Track whether any chunk docs survived retrieval (before
        # summaries are merged) so we can inject a no-results instruction
        # at the end of build_context, preventing hallucinated citations.
        _has_chunk_docs = len(ctx.docs) > 0

        def _do_format(docs_to_fmt: list[dict]) -> str:
            nonlocal _cached_titles
            qa = ctx.query_analysis
            if qa and qa.speaker_names and not ctx.file_ids:
                mid_set = {
                    d.get("metadata", {}).get("meeting_id")
                    for d in docs_to_fmt
                    if d.get("metadata", {}).get("meeting_id") is not None
                }
                if len(mid_set) > 1:
                    if not _cached_titles:
                        _cached_titles = _load_meeting_titles(mid_set)
                    return _format_docs_by_meeting(docs_to_fmt, qa.speaker_names, _cached_titles)
            return _format_docs(docs_to_fmt)

        # Load summaries BEFORE formatting so they get numbered [N] citations.
        # Pass the starting citation index so the markdown summary blocks can
        # surface ``[N]`` aligned with the index each synthetic doc receives in
        # ``all_docs`` (chunks: [1]..[M], file summaries: [M+1]..[M+F],
        # meeting summaries: [M+F+1]..[M+F+S]). Without this, the LLM only
        # sees ``[N]`` in ``[Meeting Content]`` and tends to misattribute
        # facts paraphrased from the markdown summary block to source [1].
        chunk_count = len(ctx.docs)
        file_summaries_context, file_summary_docs = _load_file_summaries(
            ctx, citation_start=chunk_count + 1
        )
        meeting_summaries_context, meeting_summary_docs = _load_meeting_summaries_for_context(
            ctx, citation_start=chunk_count + len(file_summary_docs) + 1
        )
        summary_docs = file_summary_docs + meeting_summary_docs

        # Combine canonical chunk docs + summary docs into one numbered list.
        # Chunks get [1]..[M], summaries get [M+1]..[M+S].
        all_docs = ctx.docs + summary_docs

        ctx.meeting_context = _do_format(all_docs)

        ctx.combined_context = _build_system_context(
            ctx.memory_context,
            ctx.session_context,
            ctx.entity_context,
            ctx.meeting_context,
            ctx.web_context,
            file_summaries_context,
            meeting_summaries_context,
        )

        context_window = getattr(settings, "LLM_CONTEXT_WINDOW", 128_000)
        prompt_reserve = settings.LLM_PROMPT_RESERVE_TOKENS
        answer_reserve = settings.LLM_MAX_TOKENS
        history_parts = [str(m.content) for m in ctx.history_messages]
        history_budget = getattr(
            settings, "LLM_HISTORY_BUDGET_CHARS", _HISTORY_BUDGET_TOKENS_DEFAULT
        )
        history_blob_parts: list[str] = []
        history_token_count = 0
        for part in reversed(history_parts):
            part_tokens = count_tokens(part, model)
            if history_token_count + part_tokens > history_budget:
                break
            history_blob_parts.insert(0, part)
            history_token_count += part_tokens
        # Reuse accumulated count; the join separator adds negligible tokens.
        history_tokens = history_token_count + max(0, len(history_blob_parts) - 1)
        window_budget = context_window - prompt_reserve - answer_reserve
        total_budget = settings.PROMPT_TOTAL_BUDGET_TOKENS
        if total_budget > 0:
            window_budget = min(window_budget, total_budget)
        budget = max(0, window_budget - history_tokens - 200)

        combined_tokens = count_tokens(ctx.combined_context, model)
        if combined_tokens <= budget:
            ctx.docs = all_docs
            # H-6: Inject no-results instruction on the early-return path too.
            if not _has_chunk_docs:
                ctx.combined_context += (
                    "\n\n[IMPORTANT] No relevant meeting transcript or document "
                    "chunks were retrieved for this query. Base your answer only "
                    "on the summaries provided (if any), or state that no "
                    "relevant information was found in the available records. "
                    "Do NOT fabricate specific details or citations."
                )
            ctx.trace.finish_span("build_context")
            return

        # Cache non-meeting context tokens so the loop only re-counts meeting portion.
        non_meeting_ctx = _build_system_context(
            ctx.memory_context,
            ctx.session_context,
            ctx.entity_context,
            "",
            ctx.web_context,
            file_summaries_context,
            meeting_summaries_context,
        )
        non_meeting_tokens = count_tokens(non_meeting_ctx, model)

        tokens_before_truncation = combined_tokens

        # Cap summary docs to 40% of budget so they cannot consume the
        # entire context window.  Pop from the tail (lowest-ranked) until
        # the formatted summaries fit within the cap.
        _SUMMARY_BUDGET_RATIO = 0.4
        summary_budget = int(budget * _SUMMARY_BUDGET_RATIO)
        summary_tokens = count_tokens(_do_format(summary_docs), model)
        truncated_summaries = list(summary_docs)
        if summary_tokens > summary_budget:
            while (
                len(truncated_summaries) > 1
                and count_tokens(_do_format(truncated_summaries), model) > summary_budget
            ):
                truncated_summaries.pop()
        summary_docs = truncated_summaries

        # Truncation: only pop chunk docs, summaries always survive.
        truncated_chunks = list(ctx.docs)
        while truncated_chunks and combined_tokens > budget:
            truncated_chunks.pop()
            ctx.meeting_context = _do_format(truncated_chunks + summary_docs)
            meeting_tokens = count_tokens(ctx.meeting_context, model)
            combined_tokens = non_meeting_tokens + meeting_tokens

        if combined_tokens != non_meeting_tokens:
            ctx.combined_context = _build_system_context(
                ctx.memory_context,
                ctx.session_context,
                ctx.entity_context,
                ctx.meeting_context,
                ctx.web_context,
                file_summaries_context,
                meeting_summaries_context,
            )

        if len(truncated_chunks) != len(ctx.docs):
            ctx.dropped_chunks = len(ctx.docs) - len(truncated_chunks)
            logger.info(
                "Context truncated: %d -> %d chunk docs (%d tokens -> %d, budget=%d)",
                len(ctx.docs),
                len(truncated_chunks),
                tokens_before_truncation,
                combined_tokens,
                budget,
            )

        ctx.docs = truncated_chunks + summary_docs

        # H-6: When no chunk docs survived retrieval (zero matches, all
        # filtered, BM25 fallback empty), inject explicit instruction so
        # the LLM does not fabricate citations from summaries alone.
        if not _has_chunk_docs:
            ctx.combined_context += (
                "\n\n[IMPORTANT] No relevant meeting transcript or document "
                "chunks were retrieved for this query. Base your answer only "
                "on the summaries provided (if any), or state that no "
                "relevant information was found in the available records. "
                "Do NOT fabricate specific details or citations."
            )
            logger.info("Empty retrieval guard: no chunk docs found for query")

        _inject_speaker_instructions(ctx, _cached_titles)

        if qa and qa.temporal_hint:
            hint = qa.temporal_hint
            if hint.absolute_seconds is not None:
                abs_lo, abs_hi = hint.absolute_seconds
                if abs_lo >= 0:
                    mins = abs_hi / 60
                    desc = f"the first {mins:.0f} minute(s)"
                else:
                    mins = -abs_lo / 60
                    desc = f"the last {mins:.0f} minute(s)"
            else:
                lo_pct = int(hint.ratio_min * 100)
                hi_pct = int(hint.ratio_max * 100)
                desc = f"the {lo_pct}%-{hi_pct}% portion"
            ctx.combined_context += (
                f"\n\n[Temporal Focus]\n"
                f"The user is asking specifically about {desc} of the meeting. "
                f"The sources provided have been filtered to this time range. "
                f"Focus your answer on content from this time period."
            )

        ctx.trace.finish_span("build_context")
    except Exception as _trace_exc:
        ctx.trace.finish_span("build_context", "error", error=_trace_exc)
        raise


async def generate_answer(
    ctx: PipelineContext, skill_definition: dict[str, Any] | None = None
) -> None:
    """Invoke the LLM via LCEL chain to produce the answer.

    When the LLM supports vision and retrieved docs contain images, the context
    is delivered as a multimodal HumanMessage with both text and image_url blocks.
    """
    from langchain_core.runnables import RunnableLambda

    from ..llm import get_llm, get_rag_prompt, get_skill_prompt
    from ..traffic_control import traffic_controller
    from ._anthropic_cache import apply_anthropic_cache_control

    ctx.trace.start_span("generate_answer", "generate")
    ctx.trace.start_span("llm_ttft", "generate", parent_label="generate_answer")
    llm = ctx.llm if ctx.llm is not None else get_llm()
    prompt = get_skill_prompt(skill_definition) if skill_definition else get_rag_prompt()
    chain = prompt | RunnableLambda(apply_anthropic_cache_control) | llm | StrOutputParser()

    try:
        from ...core.metrics import LLM_REQUEST_DURATION, LLM_REQUEST_TOTAL
        from ..llm._parsing import strip_thinking_blocks
        from ._formatting import extract_image_urls_from_docs, load_image_as_base64_url

        inputs = {
            "context": ctx.combined_context,
            "question": ctx.question,
            "history": ctx.history_messages,
            "memory_context": "",
        }
        provider = settings.LLM_BINDING
        status = "success"
        start_time = time.monotonic()

        # Multimodal: inject images into the LLM call when docs contain images
        # AND the user's question plausibly needs visual reasoning. Attaching
        # images to text-only questions wastes significant tokens.
        _vision_images: list[dict[str, Any]] = []
        from ._formatting import is_visual_query

        attach_images = True
        if settings.MULTIMODAL_ATTACH_GATE_ENABLED:
            attach_images = is_visual_query(ctx.question) or is_visual_query(ctx.rewritten_query)

        if ctx.docs and attach_images:
            image_infos = extract_image_urls_from_docs(ctx.docs)
            for info in image_infos:
                storage_path = info.get("storage_path")
                if not isinstance(storage_path, str):
                    continue
                data_url = load_image_as_base64_url(storage_path)
                if data_url:
                    _vision_images.append({"url": data_url, "source_index": info["source_index"]})
            if _vision_images:
                logger.info(
                    "Multimodal context: %d images attached to LLM call",
                    len(_vision_images),
                )
        elif ctx.docs and not attach_images:
            logger.debug("Multimodal attach gate: skipping image attach for non-visual query")

        async def _generate_with_fallback() -> str:
            """Invoke LLM, falling back to text-only if multimodal is rejected."""
            if _vision_images:
                try:
                    return await asyncio.to_thread(
                        _invoke_chain_with_retry_multimodal,
                        chain,
                        inputs,
                        _vision_images,
                    )
                except Exception:
                    # LLM provider errors are unpredictable — keep broad to
                    # ensure text-only fallback always triggers.
                    logger.warning(
                        "Multimodal generation failed, falling back to text-only",
                        exc_info=True,
                    )
            return await asyncio.to_thread(_invoke_chain_with_retry, chain, inputs)

        # Wrap LLM call with traffic controller for concurrency/rate-limit/circuit-breaker
        if traffic_controller is not None:
            async with traffic_controller:
                try:
                    raw_answer = await _generate_with_fallback()
                    ctx.trace.finish_span("llm_ttft")
                    ctx.answer = _strip_internal_tokens(strip_thinking_blocks(raw_answer))
                    traffic_controller.record_success()
                except Exception as _trace_exc:
                    ctx.trace.finish_span("llm_ttft", "error", error=_trace_exc)
                    traffic_controller.record_failure()
                    status = "error"
                    raise
                except BaseException as _base_exc:
                    traffic_controller.record_failure()
                    raise
        else:
            raw_answer = await _generate_with_fallback()
            ctx.trace.finish_span("llm_ttft")
            ctx.answer = _strip_internal_tokens(strip_thinking_blocks(raw_answer))

        ctx.trace.finish_span("generate_answer")
        # Annotate generate span with token counts (use estimate for input to avoid re-tokenizing)
        _in_estimate = int(len(ctx.combined_context) / _CHARS_PER_TOKEN)
        _out_estimate = int(len(ctx.answer) / _CHARS_PER_TOKEN) if ctx.answer else 0
        for span in ctx.trace.spans:
            if span.label == "generate_answer" and span.end_time is not None:
                span.tokens_in = _in_estimate
                span.tokens_out = _out_estimate
                break
    except Exception as _trace_exc:
        ctx.trace.finish_span("generate_answer", "error", error=_trace_exc)
        status = "error"
        raise
    finally:
        try:
            LLM_REQUEST_DURATION.labels(provider=provider).observe(time.monotonic() - start_time)
            LLM_REQUEST_TOTAL.labels(provider=provider, status=status).inc()
        except Exception:
            logger.debug("Metrics recording failed", exc_info=True)


def save_messages(ctx: PipelineContext) -> None:
    """Persist the Q&A turn to chat history.

    Sources are serialized as JSON alongside the AI message so that
    historical session views can display citation chips.

    Tolerates the session being deleted mid-stream: ``DELETE /sessions/{id}``
    can race against an in-flight streaming response, leaving us trying to
    INSERT into ``chat_messages`` for a session whose parent row no longer
    exists. Rather than raise ``sqlite3.IntegrityError`` (which propagates
    as an unhandled exception and dumps a stack trace), we detect the
    missing session and skip persistence — the answer was already streamed
    to the client over SSE, and the user explicitly removed the session.
    """
    import json

    from ...core import database as core_db
    from ..memory import get_session_history, invalidate_session
    from ._formatting import _extract_sources

    ctx.trace.start_span("save_messages", "persist")
    try:
        assert ctx.session_id is not None, "session_id must be set before saving messages"

        # Pre-flight: confirm the parent session still exists before we attempt
        # any chat_messages INSERT. Cheap read-only probe (no write lock).
        with get_connection() as conn:
            session_row = core_db.get_session(conn, ctx.session_id)
        if session_row is None:
            logger.warning(
                "Skipping persistence: session %s was deleted while streaming "
                "(answer was streamed to client; nothing to save).",
                ctx.session_id,
            )
            invalidate_session(ctx.session_id)
            ctx.trace.finish_span("save_messages")
            return

        history = get_session_history(ctx.session_id)
        history.add_message(HumanMessage(content=ctx.question))

        # Build serializable sources list for the AI message.
        #
        # IMPORTANT: this MUST stay in sync with the live `_extract_sources`
        # output emitted to the frontend over SSE. When this loop produced a
        # different field set, the same answer rendered in History (read from
        # saved JSON) showed mismatched citation chips compared to the live
        # stream — `source_kind`, `content_type`, slide/image paths and
        # `heading_path` were missing from the saved version, so a citation
        # the LLM intended as a slide preview rendered as plain text.
        #
        # We run the same `_extract_sources` used by the SSE branch to
        # guarantee the saved [N] → source mapping is byte-identical.
        sources_payload: list[dict[str, object]] | None = None
        if ctx.docs:
            extracted = _extract_sources(ctx.docs, max_sources=len(ctx.docs))
            if extracted:
                sources_payload = list(extracted)

        extra_kwargs: dict[str, object] = {}
        if sources_payload:
            extra_kwargs["sources_json"] = json.dumps(sources_payload, ensure_ascii=False)

        history.add_message(AIMessage(content=ctx.answer, additional_kwargs=extra_kwargs))
        ctx.trace.finish_span("save_messages")
    except sqlite3.IntegrityError as exc:
        # Defense in depth: even with the pre-flight check above, a delete
        # could land between probe and INSERT. Treat the same way.
        if "FOREIGN KEY" in str(exc).upper():
            logger.warning(
                "Session %s deleted during message persistence (FK violation); dropping write.",
                ctx.session_id,
            )
            from ..memory import invalidate_session as _invalidate

            if ctx.session_id is not None:
                _invalidate(ctx.session_id)
            ctx.trace.finish_span("save_messages")
            return
        ctx.trace.finish_span("save_messages", "error", error=exc)
        raise
    except Exception as _trace_exc:
        ctx.trace.finish_span("save_messages", "error", error=_trace_exc)
        raise


# Track sessions with an active extraction task so we can skip duplicate
# scheduling for the same session (C-C3 per-session rate limiting).
_active_extraction_sessions: set[str] = set()


def schedule_fact_extraction(ctx: PipelineContext) -> None:
    """Fire-and-forget background fact extraction + entity extraction with retry."""
    from ..knowledge_graph import kg_service
    from ..memory import memory_service
    from ._extraction import run_combined_extraction, should_skip_extraction

    # Per-session dedup: skip if this session already has an extraction in
    # flight to avoid piling up redundant LLM calls (C-C3).
    sess = ctx.session_id or ""
    if sess:
        if sess in _active_extraction_sessions:
            logger.debug(
                "Skipping fact extraction for session=%s: another extraction is already running",
                sess[:8],
            )
            return
        # Claim the slot immediately (before any yield point) to prevent
        # a check-then-act race between schedule_fact_extraction calls.
        _active_extraction_sessions.add(sess)

    ctx.trace.start_span("schedule_fact_extraction", "persist")
    try:

        async def _safe_extract():
            try:
                # Per-session circuit breaker: skip extraction after consecutive failures
                failures = _get_failures(ctx.session_id)
                if failures >= _EXTRACTION_CIRCUIT_BREAKER_THRESHOLD:
                    logger.warning(
                        "Extraction circuit breaker open for session=%s "
                        "(%d consecutive failures), skipping",
                        (ctx.session_id or "none")[:8],
                        failures,
                    )
                    return

                # Cheap skip for trivial / empty turns — avoids an LLM call entirely.
                if should_skip_extraction(ctx.question, ctx.answer):
                    return

                use_combined = settings.COMBINED_EXTRACTION_ENABLED
                last_exc: Exception | None = None
                for attempt in range(_FACT_EXTRACT_MAX_RETRIES + 1):
                    try:
                        if use_combined:
                            await run_combined_extraction(
                                ctx.user_id,
                                ctx.question,
                                ctx.answer,
                                session_id=ctx.session_id,
                                meeting_ids=ctx.meeting_ids,
                                file_ids=ctx.file_ids,
                            )
                        else:
                            await memory_service.auto_extract_facts(
                                ctx.user_id,
                                ctx.question,
                                ctx.answer,
                                session_id=ctx.session_id,
                                meeting_ids=ctx.meeting_ids,
                                file_ids=ctx.file_ids,
                            )
                            await kg_service.extract_entities(
                                ctx.user_id,
                                ctx.question,
                                ctx.answer,
                                session_id=ctx.session_id,
                                meeting_ids=ctx.meeting_ids,
                                file_ids=ctx.file_ids,
                            )
                        _reset_failures(ctx.session_id)
                        return
                    except Exception as exc:
                        last_exc = exc
                        if attempt < _FACT_EXTRACT_MAX_RETRIES:
                            delay = _FACT_EXTRACT_BASE_DELAY * (2**attempt)
                            logger.debug(
                                "Fact extraction attempt %d failed, retrying in %.1fs",
                                attempt + 1,
                                delay,
                            )
                            await asyncio.sleep(delay)
                logger.warning(
                    "Background fact extraction failed after %d attempts: %s",
                    _FACT_EXTRACT_MAX_RETRIES + 1,
                    last_exc,
                )
                ctx.failed_extraction_count += 1
                _increment_failures(ctx.session_id)
            finally:
                if sess:
                    _active_extraction_sessions.discard(sess)

        from ..chain import _background_tasks, _register_background_task

        task = asyncio.create_task(_safe_extract())

        def _on_done(t: asyncio.Task) -> None:
            _background_tasks.discard(t)
            if sess:
                _active_extraction_sessions.discard(sess)
            if t.cancelled():
                logger.debug("Fact extraction task was cancelled")
            elif t.exception():
                logger.warning("Fact extraction task failed", exc_info=t.exception())

        _register_background_task(task)
        task.add_done_callback(_on_done)

        # Periodic profile refresh: trigger every N turns
        if settings.MEMORY_PROFILE_ENABLED and ctx.session_id:

            async def _maybe_refresh_profile() -> None:
                try:
                    with get_connection() as conn:
                        row = conn.execute(
                            "SELECT COUNT(*) as cnt FROM chat_messages WHERE session_id=?",
                            (ctx.session_id,),
                        ).fetchone()
                    turn_count = row["cnt"] if row else 0
                    if (
                        turn_count > 0
                        and turn_count % settings.MEMORY_PROFILE_REFRESH_INTERVAL == 0
                    ):
                        await memory_service.refresh_user_profile(ctx.user_id)
                except Exception:
                    logger.warning("Profile refresh scheduling failed", exc_info=True)

            profile_task = asyncio.create_task(_maybe_refresh_profile())
            _register_background_task(profile_task)
            profile_task.add_done_callback(_background_tasks.discard)

        ctx.trace.finish_span("schedule_fact_extraction")
    except Exception as _trace_exc:
        ctx.trace.finish_span("schedule_fact_extraction", "error", error=_trace_exc)
        raise
