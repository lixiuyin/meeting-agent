"""Auto-generate meeting-level summary when all files are ready."""

import asyncio
import json
import logging
from typing import Any

from ...core.database import get_write_connection, update_meeting_status
from ...core.exceptions import is_retryable
from ..chain._per_file_summary import generate_per_file_summary
from ..chain._speaker_context import build_speaker_context as _build_speaker_context
from ._pipeline_common import _persist_file_summary, _ws_notify_complete

logger = logging.getLogger(__name__)

# OpenRouter and similar gateways occasionally return empty / HTML error /
# truncated bodies that surface as ``JSONDecodeError`` from inside the
# OpenAI client. ``is_retryable`` already classifies those as transient.
# Two retries with short backoff is enough to ride through the failure
# window without making a stuck provider hang the meeting summary task.
_LLM_RETRY_MAX_ATTEMPTS = 3
_LLM_RETRY_BASE_DELAY_S = 1.0


async def _ainvoke_with_retry(llm: Any, prompt: str) -> Any:
    """Call ``llm.ainvoke`` with bounded retry on transient provider errors.

    Re-raises the last exception when retries are exhausted or when the
    error is non-retryable (auth failure, context-window overflow, etc.)
    so the outer ``_auto_summarize_meeting`` failure path runs as before.
    """
    last_exc: Exception | None = None
    for attempt in range(_LLM_RETRY_MAX_ATTEMPTS):
        try:
            from ..llm import invoke_llm_text

            return await invoke_llm_text(llm, prompt)
        except Exception as exc:
            last_exc = exc
            if not is_retryable(exc) or attempt == _LLM_RETRY_MAX_ATTEMPTS - 1:
                raise
            delay = _LLM_RETRY_BASE_DELAY_S * (2**attempt)
            logger.warning(
                "Meeting summary LLM call failed (attempt %d/%d): %s — retrying in %.1fs",
                attempt + 1,
                _LLM_RETRY_MAX_ATTEMPTS,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
    # The loop either returns or raises on its final iteration. Keep an
    # explicit fallback so optimized Python builds do not remove correctness
    # checks the way an ``assert`` would.
    raise RuntimeError("LLM retry loop exhausted without a result") from last_exc


def _mark_auto_failed(meeting_id: int) -> None:
    with get_write_connection() as conn:
        row = conn.execute("SELECT status FROM meetings WHERE id=?", (meeting_id,)).fetchone()
        if row and row["status"] in ("summarizing", "ready"):
            update_meeting_status(conn, meeting_id, "failed")
        conn.execute(
            "UPDATE meetings SET summary_status='failed', updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (meeting_id,),
        )


async def _maybe_trigger_meeting_summary(meeting_id: int) -> None:
    """Compatibility facade for durable meeting-summary scheduling."""
    from ..summaries import enqueue_meeting_summary

    await enqueue_meeting_summary(meeting_id)


def _parse_segments_json(raw: Any) -> Any | None:
    """Parse segments_json field from a meeting file row."""
    if not raw:
        return None
    try:
        return json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return None


def _extract_file_segments(f: dict) -> Any | None:
    """Extract parsed segments for audio/video files."""
    if f["file_type"] not in ("video", "audio"):
        return None
    return _parse_segments_json(f.get("segments_json"))


async def _ensure_per_file_summary(
    f: dict,
    meeting_id: int,
) -> dict | None:
    """Ensure a per-file summary exists, generating one if needed.

    Returns a summary dict for the composed prompt, or None on failure.
    """
    summary = (f.get("summary") or "").strip()
    if not summary:
        transcript = (f.get("transcript") or "").strip()
        if not transcript:
            return None
        try:
            file_segments = _extract_file_segments(f)
            summary, key_points = await generate_per_file_summary(
                file_type=f["file_type"],
                file_name=f["file_name"],
                text=transcript,
                segments=file_segments,
            )
            await asyncio.to_thread(
                _persist_file_summary,
                f["id"],
                summary,
                key_points,
                meeting_id=meeting_id,
            )
        except Exception:
            logger.warning(
                "Auto-summary: failed per-file summary for file %d",
                f["id"],
                exc_info=True,
            )
            return None
    if not summary:
        return None
    return {
        "file_id": f["id"],
        "file_name": f["file_name"],
        "file_type": f["file_type"],
        "summary": summary,
    }


def _compose_summary_context(
    per_file_summaries: list[dict],
    speaker_ctx: str,
) -> str:
    """Compose the LLM prompt context from per-file summaries and speaker info."""
    parts = [
        f"### File Summary [file:{pf['file_id']}] {pf['file_type']} — "
        f"{pf['file_name']}\n{pf['summary']}"
        for pf in per_file_summaries
    ]
    composed = "\n\n".join(parts)
    if speaker_ctx:
        composed = composed + "\n\n" + speaker_ctx
    return composed


_SUMMARY_PROMPT_TEMPLATE = (
    "You are creating a **Meeting Summary** from the File Summaries below.  "
    "Write the output in Markdown starting with ``## Meeting Summary`` "
    "followed by the sections below.  Every factual claim must cite its "
    "source using the [file:ID] notation.\n\n"
    "## Required sections\n\n"
    "### 1. Main Topics Discussed\n"
    "Bullet list of the major topics / themes.  One topic per bullet.  "
    "Cite the primary files where each topic was raised.\n\n"
    "### 2. Key Decisions Made\n"
    "Bullet list of binding decisions.  Include brief rationale when "
    "available.  Always cite the file where the decision was recorded.\n\n"
    "### 3. Action Items with Owners\n"
    "Table with columns: Action | Owner | Deadline (if any) | Source  "
    'If no owner is named write "Unassigned".  Each row must cite a '
    "source file.\n\n"
    "### 4. Important Points and Conclusions\n"
    "Key takeaways, conclusions reached, risks flagged, or follow-ups "
    "agreed.  One point per bullet with source citations.\n\n"
    "### 5. Roles & Speaker Contributions\n"
    "For each speaker / role identified, write 1-2 sentences summarising "
    "their contributions, perspectives, and assigned action items.  If "
    "speaker names are not available, describe roles by function.\n\n"
    "---\n"
    "**Citation format**: Cite files with [file:ID] (e.g. [file:42]).  "
    "Every bullet / row / claim must carry at least one citation.\n\n"
    "Meeting Title: {title}\n\n"
    "Below are the **File Summaries** for each file in this meeting:\n\n"
    "{transcript}\n\n"
    "## Meeting Summary"
)

_MAX_AUTO_TOKENS = 12_000


def _split_into_chunks(text: str, max_tokens: int, count_fn: Any) -> list[str]:
    """Split text into token-bounded chunks for map-reduce."""
    chunk_size = max(1, max_tokens // 2)
    paragraphs: list[str] = []
    for paragraph in text.split("\n\n"):
        remainder = paragraph
        while remainder and count_fn(remainder) > chunk_size:
            end = min(len(remainder), max(chunk_size * 3, 1))
            while end > 1 and count_fn(remainder[:end]) > chunk_size:
                end = max(1, int(end * 0.85))
            paragraphs.append(remainder[:end])
            remainder = remainder[end:]
        if remainder:
            paragraphs.append(remainder)
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for para in paragraphs:
        para_tokens = count_fn(para)
        if current_tokens + para_tokens > chunk_size and current:
            chunks.append("\n\n".join(current))
            current = [para]
            current_tokens = para_tokens
        else:
            current.append(para)
            current_tokens += para_tokens
    if current:
        chunks.append("\n\n".join(current))
    return chunks


async def _generate_summary(
    llm: Any,
    composed: str,
    title: str,
    count_fn: Any,
) -> str:
    """Generate a meeting summary, using map-reduce for long inputs."""
    if count_fn(composed) <= _MAX_AUTO_TOKENS:
        prompt = _SUMMARY_PROMPT_TEMPLATE.format(title=title, transcript=composed)
        return await _ainvoke_with_retry(llm, prompt)

    chunks = _split_into_chunks(composed, _MAX_AUTO_TOKENS, count_fn)
    chunk_prompt = (
        "Summarize the following meeting section concisely, "
        "focusing on key decisions, action items, speaker "
        "contributions, and conclusions. Cite sources with [file:ID].\n\n"
        "{chunk}\n\nSection Summary:"
    )
    semaphore = asyncio.Semaphore(8)

    async def _summarize_chunk(chunk: str) -> str:
        async with semaphore:
            return await _ainvoke_with_retry(llm, chunk_prompt.format(chunk=chunk))

    chunk_results = await asyncio.gather(*(_summarize_chunk(chunk) for chunk in chunks))
    chunk_summaries = "\n\n---\n\n".join(chunk_results)

    merge_prompt = (
        "Merge into a single structured meeting summary starting with "
        "``## Meeting Summary``.  Follow this structure:\n"
        "### 1. Main Topics Discussed | ### 2. Key Decisions Made | "
        "### 3. Action Items with Owners | ### 4. Important Points and "
        "Conclusions | ### 5. Roles & Speaker Contributions\n"
        "Every claim must cite its source with [file:ID].\n\n"
        f"{chunk_summaries}\n\nFinal Summary:"
    )
    return await _ainvoke_with_retry(llm, merge_prompt)


async def _auto_summarize_meeting(meeting_id: int) -> None:
    """Auto-generate a meeting-level summary when all files are ready.

    Early-exits if the summary already exists (status == 'ready') or if
    another task is already generating one (inflight lock).
    """
    from ...core import database as db
    from ..chain._meeting_summary_lifecycle import (
        acquire_summary_inflight,
        release_summary_inflight,
    )

    def _fetch_status():
        with db.get_connection() as conn:
            return conn.execute(
                "SELECT summary_status FROM meetings WHERE id=?", (meeting_id,)
            ).fetchone()

    row = await asyncio.to_thread(_fetch_status)
    if not row:
        logger.warning("_auto_summarize_meeting: meeting %d not found", meeting_id)
        return
    if row["summary_status"] == "ready":
        return  # already done

    if not acquire_summary_inflight(meeting_id):
        return  # another task is already working on it

    try:
        # Mark pending
        def _mark_pending() -> None:
            with get_write_connection() as conn:
                conn.execute(
                    "UPDATE meetings SET summary_status='pending', updated_at=CURRENT_TIMESTAMP "
                    "WHERE id=?",
                    (meeting_id,),
                )

        await asyncio.to_thread(_mark_pending)

        # Clear stale vectors inside the lock to ensure atomicity with the
        # upcoming upsert.  This prevents orphan vectors if the process
        # crashes between invalidation and rebuild.
        try:
            from ..rag._meeting_summary_vectorstore import delete_meeting_summary

            await asyncio.to_thread(delete_meeting_summary, meeting_id)
        except Exception:
            logger.debug("Failed to clear stale summary vector for %d", meeting_id, exc_info=True)
        try:
            from ..rag._summary_vectorstore import delete_meeting_summaries

            await asyncio.to_thread(delete_meeting_summaries, meeting_id)
        except Exception:
            logger.debug("Failed to clear legacy summary vector for %d", meeting_id, exc_info=True)

        # Load meeting + files
        from ...core import database as db

        def _load_meeting_and_files():
            with db.get_connection() as conn:
                meeting = db.get_meeting(conn, meeting_id)
                files_value = db.list_ready_meeting_files(conn, meeting_id) if meeting else []
                return meeting, files_value

        m, files = await asyncio.to_thread(_load_meeting_and_files)
        if not m or m["status"] not in ("ready", "summarizing"):
            await asyncio.to_thread(_mark_auto_failed, meeting_id)
            return

        if not files:
            await asyncio.to_thread(_mark_auto_failed, meeting_id)
            return

        # M-2: Skip summary when total transcript is too short (saves LLM tokens)
        total_text = "".join((f.get("transcript") or "").strip() for f in files)
        if len(total_text) < 50:
            logger.info(
                "Skipping auto-summary for meeting %d: total transcript too short (%d chars)",
                meeting_id,
                len(total_text),
            )
            await asyncio.to_thread(_mark_auto_failed, meeting_id)
            return

        # Ensure per-file summaries exist
        per_file_summaries: list[dict] = []
        for f in files:
            result = await _ensure_per_file_summary(f, meeting_id)
            if result:
                per_file_summaries.append(result)

        if not per_file_summaries:
            await asyncio.to_thread(_mark_auto_failed, meeting_id)
            return

        # Build speaker context (same as manual endpoint)
        speaker_ctx = _build_speaker_context(files)

        # Generate summary via LLM
        from ...services.llm import get_llm
        from ...services.tokenizer import count_tokens

        composed = _compose_summary_context(per_file_summaries, speaker_ctx)
        llm = get_llm()
        summary = await _generate_summary(llm, composed, m["title"], count_tokens)

        # Persist: DB + vector via unified helper
        contributing_ids = [pf["file_id"] for pf in per_file_summaries]
        contributing_names = [pf["file_name"] for pf in per_file_summaries]

        from ..chain._meeting_summary_lifecycle import persist_meeting_summary

        await asyncio.to_thread(
            persist_meeting_summary,
            meeting_id=meeting_id,
            title=m["title"],
            summary=summary,
            contributing_file_ids=contributing_ids,
            contributing_file_names=contributing_names,
        )
        # Notify frontend: meeting badge flips summarizing -> ready.
        await _ws_notify_complete(
            meeting_id,
            "ready",
            m["title"],
            user_id=m.get("user_id"),
        )
    except Exception:
        logger.error("_auto_summarize_meeting failed for meeting %d", meeting_id, exc_info=True)
        await asyncio.to_thread(_mark_auto_failed, meeting_id)
        raise
    finally:
        release_summary_inflight(meeting_id)
