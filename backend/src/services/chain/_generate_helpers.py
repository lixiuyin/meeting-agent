"""Helper functions for the generate step: token stripping, summary loading,
retry wrappers, and extraction circuit breaker.

Extracted from ``_steps_generate.py`` to keep that module focused on the
four main pipeline step functions.
"""

import logging
import re
import sqlite3
import threading
from typing import Any

from cachetools import TTLCache
from langchain_core.output_parsers import StrOutputParser
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from ...core.config import settings
from ...core.settings_epoch import register_epoch_cache
from ._common import logger
from ._context import PipelineContext

__all__ = [
    "get_extraction_status",
    "invalidate_file_summaries",
]

_FACT_EXTRACT_MAX_RETRIES = 2
_FACT_EXTRACT_BASE_DELAY = 1.0  # seconds
_HISTORY_BUDGET_TOKENS_DEFAULT = 4096
_EXTRACTION_CIRCUIT_BREAKER_THRESHOLD = 10
_CHARS_PER_TOKEN = 3.5

_INTERNAL_TOKEN_RE_1 = re.compile(
    r"\[(?:"
    r"user[_ ]memory"
    r"|meeting[_ ]summar(?:y|ies)"
    r"|file[_ ]summar(?:y|ies)"
    r"|web[_ ]search"
    r"|image\s*#?\d*"
    r")\]",
    re.IGNORECASE,
)
_INTERNAL_TOKEN_RE_2 = re.compile(r"\[file:\d+\]")


def _strip_internal_tokens(answer: str) -> str:
    """Remove internal placeholder tokens that should never reach the user."""
    answer = _INTERNAL_TOKEN_RE_1.sub("", answer)
    answer = _INTERNAL_TOKEN_RE_2.sub("", answer)
    return re.sub(r"[ \t]{2,}", " ", answer).strip()


_SUMMARY_SCORE_TRUNC_CHARS = 2000
_SUMMARY_SCORE_FALLBACK = 0.5


def _score_summary(query_embedding: list[float], summary_text: str) -> float:
    """Compute cosine similarity between a query embedding and summary text.

    Single-text variant kept for backward compatibility; prefer
    ``_score_summaries_batch`` when scoring multiple summaries to avoid an
    N+1 HTTP round-trip per summary on the chat hot path.

    Returns a value clamped to [0.0, 1.0], rounded to 4 decimals.
    Falls back to 0.5 on any failure so summary sources are never excluded.
    """
    scores = _score_summaries_batch(query_embedding, [summary_text])
    return scores[0] if scores else _SUMMARY_SCORE_FALLBACK


def _score_summaries_batch(query_embedding: list[float], summary_texts: list[str]) -> list[float]:
    """Batched cosine similarity scoring for multiple summaries.

    Replaces N synchronous ``embed_query`` HTTP calls with a single
    ``embed_documents`` call. This matters on the chat hot path where
    ``_load_file_summaries_for_context`` and
    ``_load_meeting_summaries_for_context`` each scored every summary
    individually — N=10 summaries meant 10 sequential blocking embedding
    HTTP calls before the LLM stream could start, regularly exceeding the
    frontend's 30s heartbeat timeout.

    Returns one score per input summary in matching order. On any failure,
    returns ``_SUMMARY_SCORE_FALLBACK`` for every entry.
    """
    if not summary_texts:
        return []
    try:
        import numpy as np

        from ...services.embedder import get_embeddings

        truncated = [t[:_SUMMARY_SCORE_TRUNC_CHARS] for t in summary_texts]
        embeddings = get_embeddings()
        doc_vecs = embeddings.embed_documents(truncated)
        if len(doc_vecs) != len(summary_texts):
            logger.warning(
                "Summary batch embedding count mismatch (%d != %d)",
                len(doc_vecs),
                len(summary_texts),
            )
            return [_SUMMARY_SCORE_FALLBACK] * len(summary_texts)

        q_arr = np.array(query_embedding, dtype=np.float64)
        norm_q = float(np.linalg.norm(q_arr))
        if norm_q == 0.0:
            return [_SUMMARY_SCORE_FALLBACK] * len(summary_texts)

        scores: list[float] = []
        for vec in doc_vecs:
            d_arr = np.array(vec, dtype=np.float64)
            norm_d = float(np.linalg.norm(d_arr))
            if norm_d == 0.0:
                scores.append(_SUMMARY_SCORE_FALLBACK)
                continue
            similarity = float(np.dot(q_arr, d_arr) / (norm_q * norm_d))
            scores.append(round(max(0.0, min(1.0, similarity)), 4))
        return scores
    except (ValueError, RuntimeError, ImportError):
        logger.warning("Failed to compute batched summary scores", exc_info=True)
        return [_SUMMARY_SCORE_FALLBACK] * len(summary_texts)


# ---------------------------------------------------------------------------
# File summary cache
# ---------------------------------------------------------------------------

# M-5: Cache key includes updated_at so speaker rename / re-index invalidates stale summaries.
_file_summary_cache: TTLCache[tuple[int, str], str] = TTLCache(maxsize=512, ttl=300)
_file_summary_cache_lock = threading.Lock()


def invalidate_file_summaries(file_ids: int | list[int]) -> None:
    """Remove cached file summaries so they are regenerated on next access."""
    if isinstance(file_ids, int):
        file_ids = [file_ids]
    with _file_summary_cache_lock:
        keys_to_remove = [k for k in _file_summary_cache if k[0] in set(file_ids)]
        for k in keys_to_remove:
            del _file_summary_cache[k]


# ---------------------------------------------------------------------------
# Extraction circuit breaker
# ---------------------------------------------------------------------------

# Per-session extraction failure counter with 30-minute TTL.
# After TTL expires, failure count resets — providing automatic half-open
# recovery for sessions that were blocked due to transient LLM failures.
_extraction_breaker_cache: TTLCache[str, int] = TTLCache(maxsize=1024, ttl=1800)
_extraction_breaker_lock = threading.Lock()

# H-11: Per-session last-failure timestamp for forced half-open recovery.
# Even if a session keeps failing (refreshing the TTLCache entry), once
# 30 minutes have elapsed since the *first* failure that opened the breaker,
# we allow one probing attempt.
_EXTRACTION_RECOVERY_TIMEOUT_S = 1800  # 30 minutes
_extraction_failure_times: TTLCache[str, float] = TTLCache(
    maxsize=1024, ttl=_EXTRACTION_RECOVERY_TIMEOUT_S
)


def _clear_file_summary_cache() -> None:
    with _file_summary_cache_lock:
        _file_summary_cache.clear()


def _clear_extraction_breaker_cache() -> None:
    with _extraction_breaker_lock:
        _extraction_breaker_cache.clear()


# Register with settings epoch so caches are cleared when config changes (H11).
register_epoch_cache(_clear_file_summary_cache)
register_epoch_cache(_clear_extraction_breaker_cache)


def _get_failures(session_id: str | None) -> int:
    if not session_id:
        return 0
    with _extraction_breaker_lock:
        failures = _extraction_breaker_cache.get(session_id, 0)
        if failures < _EXTRACTION_CIRCUIT_BREAKER_THRESHOLD:
            return failures
        # H-11: Allow a probing attempt if enough time has passed since the
        # breaker opened, even if the TTLCache entry hasn't expired yet.
        first_failure_time = _extraction_failure_times.get(session_id)
        if first_failure_time is not None:
            import time

            elapsed = time.monotonic() - first_failure_time
            if elapsed >= _EXTRACTION_RECOVERY_TIMEOUT_S:
                logger.info(
                    "Extraction breaker half-open for session=%s (%.0f min since open)",
                    session_id[:8],
                    elapsed / 60,
                )
                return 0
        return failures


def _increment_failures(session_id: str | None) -> None:
    if not session_id:
        return
    import time

    with _extraction_breaker_lock:
        current = _extraction_breaker_cache.get(session_id, 0)
        _extraction_breaker_cache[session_id] = current + 1
        # Record when the breaker first opened so _get_failures can
        # implement time-based half-open recovery (H-11).
        if (
            current + 1 >= _EXTRACTION_CIRCUIT_BREAKER_THRESHOLD
            and session_id not in _extraction_failure_times
        ):
            _extraction_failure_times[session_id] = time.monotonic()


def _reset_failures(session_id: str | None) -> None:
    if not session_id:
        return
    with _extraction_breaker_lock:
        _extraction_breaker_cache[session_id] = 0
        _extraction_failure_times.pop(session_id, None)


async def get_extraction_status() -> dict[str, int | bool]:
    """Return current extraction circuit breaker status (aggregate across sessions)."""
    with _extraction_breaker_lock:
        total_sessions = len(_extraction_breaker_cache)
        max_failures = max(_extraction_breaker_cache.values(), default=0)
    return {
        "tracked_sessions": total_sessions,
        "max_consecutive_failures": max_failures,
        "circuit_open": max_failures >= _EXTRACTION_CIRCUIT_BREAKER_THRESHOLD,
    }


# ---------------------------------------------------------------------------
# LLM chain invocation with retry
# ---------------------------------------------------------------------------


def _is_retryable(exc: BaseException) -> bool:
    from ...core.exceptions import is_retryable

    if not isinstance(exc, Exception):
        return False
    return is_retryable(exc)


def _invoke_chain_with_retry(chain, inputs: dict[str, Any]) -> Any:
    """Invoke LCEL chain with tenacity retry on transient failures."""

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(settings.LLM_RETRY_MAX_ATTEMPTS),
        wait=wait_exponential(multiplier=1.5, min=1, max=60),
        before_sleep=before_sleep_log(logger, logging.DEBUG),
        reraise=True,
    )
    def _invoke() -> Any:
        try:
            return chain.invoke(inputs)
        except Exception as exc:
            from ...core.exceptions import map_error

            mapped = map_error(exc, provider=settings.LLM_BINDING)
            if mapped.__class__ in type(exc).__mro__:
                raise
            raise mapped from exc

    return _invoke()


def _invoke_chain_with_retry_multimodal(
    chain, inputs: dict[str, Any], images: list[dict[str, Any]]
) -> Any:
    """Invoke LLM with multimodal content (text + images).

    Instead of manipulating the LCEL chain, we:
    1. Format the prompt to get text messages
    2. Append image content blocks to the last HumanMessage
    3. Invoke the LLM directly, then parse through StrOutputParser
    """
    from langchain_core.messages import HumanMessage

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(settings.LLM_RETRY_MAX_ATTEMPTS),
        wait=wait_exponential(multiplier=1.5, min=1, max=60),
        before_sleep=before_sleep_log(logger, logging.DEBUG),
        reraise=True,
    )
    def _invoke_multimodal() -> Any:
        # Step 1: Format the prompt to get text messages
        prompt_value = chain.first.invoke(inputs)
        messages = prompt_value.to_messages()

        # Step 2: Inject images into the last HumanMessage
        image_blocks = [{"type": "image_url", "image_url": {"url": img["url"]}} for img in images]

        # Find the last HumanMessage and make it multimodal
        for idx in range(len(messages) - 1, -1, -1):
            if isinstance(messages[idx], HumanMessage):
                original_text = messages[idx].content
                if isinstance(original_text, str):
                    content: list[dict[str, Any] | str] = [
                        {"type": "text", "text": original_text},
                        *image_blocks,
                    ]
                else:
                    content = [*original_text, *image_blocks]
                messages[idx] = HumanMessage(
                    content=content,
                    additional_kwargs=messages[idx].additional_kwargs,
                )
                break

        # Step 3: Invoke LLM directly with modified messages
        llm = chain.steps[1] if len(chain.steps) > 1 else chain.middle[0]
        llm_response = llm.invoke(messages)

        # Step 4: Parse through StrOutputParser
        parser = StrOutputParser()
        return parser.invoke(llm_response)

    try:
        return _invoke_multimodal()
    except Exception as exc:
        from ...core.exceptions import map_error

        mapped = map_error(exc, provider=settings.LLM_BINDING)
        if mapped.__class__ in type(exc).__mro__:
            raise
        raise mapped from exc


# ---------------------------------------------------------------------------
# Summary loading for context building
# ---------------------------------------------------------------------------


def _load_file_summaries(
    ctx: PipelineContext, *, citation_start: int = 1
) -> tuple[str, list[dict]]:
    """Load per-file summaries for the current query scope.

    Returns ``(formatted_context_text, synthetic_docs)``.  The caller is
    responsible for appending *synthetic_docs* to ``ctx.docs`` at the
    appropriate time (after truncation) so that summary citations survive
    when chunk docs are popped.

    ``citation_start`` is the 1-based index this block's first synthetic
    doc will occupy in the final ``all_docs`` list.  It is embedded in
    the per-file label as ``[N]`` so the LLM can cite each summary by
    the same number used in the ``[Meeting Content]`` block.

    Candidate set is driven by scope, NOT by retrieved chunks:

    * **File scope** → ``ctx.file_ids`` (exact files)
    * **Meeting scope** → all ready files in ``ctx.meeting_ids``
    * **Unscoped** → all ready files with summaries (broad inject when count
      ≤ cap, else top-N routing)

    When ``ctx.file_ids`` or ``ctx.meeting_ids`` is set (scoped query),
    summaries are injected in full without truncation.  When unscoped,
    summaries are truncated to the configured character budget.
    """
    try:
        from ...core.database import get_connection, get_meeting_files_summaries

        scoped = bool(ctx.file_ids) or bool(ctx.meeting_ids)

        file_ids: list[int] = []
        seen: set[int] = set()
        if ctx.file_ids:
            # Exact file scope
            for fid in ctx.file_ids:
                if fid not in seen:
                    seen.add(fid)
                    file_ids.append(fid)
        elif ctx.meeting_ids:
            # Meeting-scoped: load ALL ready files in those meetings
            with get_connection() as conn:
                m_placeholders = ",".join("?" for _ in ctx.meeting_ids)
                rows = conn.execute(
                    f"SELECT id FROM meeting_files "
                    f"WHERE meeting_id IN ({m_placeholders}) AND status = 'ready'",
                    list(ctx.meeting_ids),
                ).fetchall()
                for row in rows:
                    fid = row["id"]
                    if fid not in seen:
                        seen.add(fid)
                        file_ids.append(fid)
        else:
            # Unscoped: all ready files with summaries
            with get_connection() as conn:
                rows = conn.execute(
                    "SELECT id FROM meeting_files WHERE status='ready' AND summary IS NOT NULL"
                ).fetchall()
            all_ids = [row["id"] for row in rows]
            if len(all_ids) <= settings.FILE_SUMMARY_BROAD_INJECT_CAP:
                file_ids = all_ids
            else:
                # Large corpus → route by summary similarity
                from ...services.rag._summary_router import route_files_by_summary

                routed = route_files_by_summary(
                    ctx.rewritten_query or ctx.question,
                    top_k=settings.FILE_SUMMARY_BROAD_INJECT_CAP,
                )
                file_ids = routed or all_ids

        if not file_ids:
            return "", []

        # M-5: Fetch updated_at for cache key versioning before checking cache.
        with get_connection() as conn:
            id_placeholders = ",".join("?" for _ in file_ids)
            updated_rows = conn.execute(
                f"SELECT id, updated_at FROM meeting_files WHERE id IN ({id_placeholders})",
                file_ids,
            ).fetchall()
            updated_at_map = {r["id"]: r["updated_at"] or "" for r in updated_rows}

        # Check cache with (file_id, updated_at) key — stale entries auto-miss.
        with _file_summary_cache_lock:
            summaries: dict[int, str] = {}
            uncached_ids: list[int] = []
            for fid in file_ids:
                ts = updated_at_map.get(fid, "")
                cached = _file_summary_cache.get((fid, ts))
                if cached is not None:
                    summaries[fid] = cached
                else:
                    uncached_ids.append(fid)

        if uncached_ids:
            with get_connection() as conn:
                db_summaries = get_meeting_files_summaries(conn, uncached_ids)
            with _file_summary_cache_lock:
                for fid, text in db_summaries.items():
                    ts = updated_at_map.get(fid, "")
                    _file_summary_cache[(fid, ts)] = text
            summaries.update(db_summaries)

        name_map: dict[int, str] = {}
        meeting_title_map: dict[int, str] = {}
        file_meeting_id_map: dict[int, int] = {}
        missing_ids: list[int] = []
        if summaries:
            for fid in summaries:
                if fid not in name_map or fid not in file_meeting_id_map:
                    missing_ids.append(fid)
            if missing_ids:
                with get_connection() as conn:
                    placeholders = ",".join("?" for _ in missing_ids)
                    rows = conn.execute(
                        f"SELECT mf.id, mf.file_name, mf.meeting_id, m.title "
                        f"FROM meeting_files mf "
                        f"JOIN meetings m ON m.id = mf.meeting_id "
                        f"WHERE mf.id IN ({placeholders})",
                        missing_ids,
                    ).fetchall()
                    for row in rows:
                        if row["id"] not in name_map:
                            name_map[row["id"]] = row["file_name"]
                        if row["title"]:
                            meeting_title_map[row["id"]] = row["title"]
                        file_meeting_id_map[row["id"]] = row["meeting_id"]

        if not summaries:
            return "", []

        # No truncation for scoped queries; use config budget for unscoped.
        truncation: int | None = None if scoped else settings.FILE_SUMMARY_CONTEXT_CHARS
        lines: list[str] = []
        synthetic_docs: list[dict] = []
        pending_scores: list[tuple[dict, str]] = []
        next_citation_index = citation_start
        for fid in file_ids:
            if fid in summaries:
                fname = name_map.get(fid, f"File#{fid}")
                meeting_title = meeting_title_map.get(fid)
                summary_text = summaries[fid]
                summary_text = _INTERNAL_TOKEN_RE_2.sub("", summary_text)
                if truncation is not None and len(summary_text) > truncation:
                    summary_text = summary_text[:truncation]
                # ``[N]`` prefix mirrors the index this synthetic doc receives
                # in ``all_docs``, so the LLM cites file summaries with the
                # same number it sees in the ``[Meeting Content]`` block.
                # The file id is rendered as ``#{fid}`` (not bracketed) to
                # avoid being mistaken for a citation marker.
                label_parts = [f"[{next_citation_index}] File Summary #{fid} {fname}"]
                if meeting_title:
                    label_parts.append(f"(Meeting: {meeting_title})")
                label = " ".join(label_parts)
                lines.append(f"{label}: {summary_text}")
                next_citation_index += 1

                meeting_id_for_file = file_meeting_id_map.get(fid)
                if meeting_id_for_file is not None:
                    synthetic_doc = {
                        "content": summary_text,
                        "metadata": {
                            "meeting_id": meeting_id_for_file,
                            "file_id": fid,
                            "file_name": fname,
                            "meeting_title": meeting_title,
                            "source_kind": "file_summary",
                            "chunk_index": None,
                            "page_number": 1,
                        },
                        "score": _SUMMARY_SCORE_FALLBACK,
                    }
                    synthetic_docs.append(synthetic_doc)
                    pending_scores.append((synthetic_doc, summary_text))

        if pending_scores and ctx.query_embedding:
            scores = _score_summaries_batch(
                ctx.query_embedding,
                [text for _, text in pending_scores],
            )
            for (doc_ref, _), score in zip(pending_scores, scores, strict=False):
                doc_ref["score"] = score

        return ("\n".join(lines) if lines else "", synthetic_docs)
    except (sqlite3.DatabaseError, OSError, ValueError):
        logger.warning("Failed to load file summaries", exc_info=True)
        return "", []


def _load_meeting_summaries_for_context(
    ctx: PipelineContext, *, citation_start: int = 1
) -> tuple[str, list[dict]]:
    """Load meeting-level summaries for meetings referenced in retrieved docs.

    Returns ``(formatted_context_text, synthetic_docs)``.  The caller is
    responsible for appending *synthetic_docs* to ``ctx.docs`` at the
    appropriate time (after truncation).

    ``citation_start`` is the 1-based index this block's first synthetic
    doc will occupy in the final ``all_docs`` list.  It is embedded in the
    section heading as ``[N]`` so the LLM cites each summary with the same
    number used in the ``[Meeting Content]`` block.

    Candidate set is driven by scope, NOT by retrieved chunks:

    * **File scope** → parent meetings of ``ctx.file_ids``
    * **Meeting scope** → ``ctx.meeting_ids``
    * **Unscoped** → all meetings with ``summary_status='ready'`` (broad inject
      when count ≤ cap, else top-N routing)

    For scoped queries targeting <= 2 meetings the full summary is injected
    without truncation.  For larger sets the configured character cap applies.
    """
    try:
        from ...core.database import get_connection

        meeting_ids: list[int]

        if ctx.file_ids:
            # File scope → resolve parent meetings
            with get_connection() as conn:
                placeholders = ",".join("?" for _ in ctx.file_ids)
                rows = conn.execute(
                    f"SELECT DISTINCT meeting_id FROM meeting_files WHERE id IN ({placeholders})",
                    list(ctx.file_ids),
                ).fetchall()
            seen: set[int] = set()
            meeting_ids = []
            for row in rows:
                mid = row["meeting_id"]
                if mid not in seen:
                    seen.add(mid)
                    meeting_ids.append(mid)
        elif ctx.meeting_ids:
            # Meeting scope → those exact meetings
            meeting_ids = list(ctx.meeting_ids)
        else:
            # Unscoped → all meetings with summaries
            with get_connection() as conn:
                try:
                    rows = conn.execute(
                        "SELECT id FROM meetings WHERE summary_status='ready'"
                    ).fetchall()
                except sqlite3.OperationalError:
                    # Fallback for minimal schemas without summary_status column
                    rows = conn.execute(
                        "SELECT m.id FROM meetings m "
                        "JOIN meeting_summaries ms ON ms.meeting_id = m.id "
                        "WHERE ms.summary IS NOT NULL"
                    ).fetchall()
            all_ids = [row["id"] for row in rows]
            if len(all_ids) <= settings.MEETING_SUMMARY_BROAD_INJECT_CAP:
                meeting_ids = all_ids
            else:
                # Large corpus → route by summary similarity
                from ...services.rag._meeting_summary_vectorstore import (
                    route_meetings_by_summary,
                )

                routed = route_meetings_by_summary(
                    ctx.rewritten_query or ctx.question,
                    top_k=settings.MEETING_SUMMARY_BROAD_INJECT_CAP,
                )
                meeting_ids = [mid for mid, _score in routed] if routed else all_ids

        if not meeting_ids:
            return "", []

        with get_connection() as conn:
            placeholders = ",".join("?" for _ in meeting_ids)
            rows = conn.execute(
                f"SELECT m.id, m.title, m.created_at, ms.summary "
                f"FROM meetings m "
                f"LEFT JOIN meeting_summaries ms ON ms.meeting_id = m.id "
                f"WHERE m.id IN ({placeholders}) AND ms.summary IS NOT NULL",
                meeting_ids,
            ).fetchall()

        if not rows:
            return "", []

        # Full summary for scoped queries with <= 2 meetings; truncated otherwise.
        small_scope = len(meeting_ids) <= 2
        truncation: int | None = None if small_scope else settings.MEETING_SUMMARY_CONTEXT_CHARS
        lines: list[str] = ["## Meeting Summaries", ""]
        synthetic_docs: list[dict] = []
        pending_scores: list[tuple[dict, str]] = []
        next_citation_index = citation_start
        for row in rows:
            summary_text = (row["summary"] or "").strip()
            if not summary_text:
                continue
            summary_text = _INTERNAL_TOKEN_RE_2.sub("", summary_text)
            if truncation is not None and len(summary_text) > truncation:
                summary_text = summary_text[:truncation] + "..."

            date_str = ""
            created = row["created_at"]
            if isinstance(created, str):
                date_str = created[:10]
            # ``[N]`` prefix matches the index this summary will receive in
            # the ``all_docs`` list. The LLM uses headings here to phrase its
            # answer; surfacing the citation index here keeps prose facts and
            # numeric citations aligned even when chunk docs precede summaries.
            heading = f"### [{next_citation_index}] {row['title']} (Meeting #{row['id']}"
            if date_str:
                heading += f" · {date_str}"
            heading += ")"
            lines.append(heading)
            lines.append(summary_text)
            lines.append("")
            next_citation_index += 1

            synthetic_doc = {
                "content": summary_text,
                "metadata": {
                    "meeting_id": row["id"],
                    "title": row["title"],
                    "meeting_title": row["title"],
                    "source_kind": "meeting_summary",
                    "summary_kind": "meeting",
                    "chunk_index": None,
                },
                "score": _SUMMARY_SCORE_FALLBACK,
            }
            synthetic_docs.append(synthetic_doc)
            pending_scores.append((synthetic_doc, summary_text))

        if pending_scores and ctx.query_embedding:
            scores = _score_summaries_batch(
                ctx.query_embedding,
                [text for _, text in pending_scores],
            )
            for (doc_ref, _), score in zip(pending_scores, scores, strict=False):
                doc_ref["score"] = score

        return ("\n".join(lines).strip(), synthetic_docs)
    except (sqlite3.DatabaseError, OSError, ValueError):
        logger.warning("Failed to load meeting summaries for context", exc_info=True)
        return "", []
