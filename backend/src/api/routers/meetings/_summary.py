import asyncio
import contextlib
import json
import re
from collections.abc import AsyncGenerator

from fastapi import Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ....api.middleware import limiter
from ....core import database as db
from ....core.audit import audit_log
from ....core.security import verify_api_key
from ....models.schemas import MeetingStatus, SummaryResponse
from ....models.schemas._common import FileType
from ....models.schemas.meetings import PerFileSummary
from ....services.chain._per_file_summary import generate_per_file_summary
from ....services.chain._speaker_context import build_speaker_context as _build_speaker_context
from ....services.stream_bus import serialize_event
from ._common import _ownership_filter, logger, router

# Maximum input tokens before switching to map-reduce summarization
_SUMMARY_TOKEN_LIMIT = 12_000

# Normalize LLM citation variants (file:1, file: 1) into [file:1]
_UNBRACKETED_FILE_RE = re.compile(r"(?<!\[)\bfile:\s*(\d+)\b(?!\])")


def _normalize_file_citations(text: str) -> str:
    """Ensure all file citations use the ``[file:N]`` bracket format."""
    return _UNBRACKETED_FILE_RE.sub(r"[file:\1]", text)


def _set_meeting_status(meeting_id: int, status: str) -> None:
    """Thread-safe wrapper around ``db.update_meeting_status``.

    ``db.update_meeting_status`` takes the write connection as its first
    argument; calling it from background threads would otherwise need each
    site to open its own ``get_write_connection()`` block. Centralizing it
    here matches the convention of ``db.update_meeting_summary_status``.
    """
    from ....core.database import get_write_connection

    with get_write_connection() as conn:
        db.update_meeting_status(conn, meeting_id, status)


_SUMMARY_PROMPT = """You are creating a **Meeting Summary** from the per-file summaries below.
Write the output in Markdown starting with a level-2 heading ``## Meeting Summary``
followed by the sections below.  **Every factual claim must cite its source**
using the ``[file:ID]`` notation found in the per-file context.

Use only ``[file:ID]`` citations — never cite section names like
[Meeting Summary], [File Summary], or other labels.

When dates, times, or durations are mentioned in the content, include them
explicitly in section headings or parenthetical notes.

## Required sections

### 1. Main Topics Discussed
Bullet list of the major topics / themes.  One topic per bullet.
Cite the primary files where each topic was raised.

### 2. Key Decisions Made
Bullet list of binding decisions.  Include brief rationale when available.
Always cite the file where the decision was recorded.

### 3. Action Items with Owners
Table with columns: Action | Owner | Deadline (if any) | Source
If no owner is named write "Unassigned".  Each row must cite a source file.

### 4. Important Points and Conclusions
Key takeaways, conclusions reached, risks flagged, or follow-ups agreed.
One point per bullet with source citations.

### 5. Roles & Speaker Contributions
For each speaker / role identified in the per-file summaries, write 1-2
sentences summarising their contributions, perspectives, and assigned
action items.  If speaker names are not available, describe roles by
function (e.g. "the project manager", "the engineering lead").

### 6. Key Timestamps Per Speaker (audio/video only)
If the per-file summaries contain a "Key Timestamps Per Speaker" section,
aggregate the highlights here.  For each speaker list 3-6 timestamped
bullets in ``HH:MM:SS - note`` format.  Omit this section entirely when
no audio/video timestamps are present.

---

## Citation format
- Cite files with ``[file:ID]`` (e.g. ``[file:42]``).
- You may cite multiple files: ``[file:3][file:7]``.
- Every bullet / row / claim must carry at least one citation.

---

Meeting Title: {title}

Below are the **File Summaries** for each file in this meeting:

{transcript}

## Meeting Summary"""

_CHUNK_SUMMARY_PROMPT = """Summarize the following meeting transcript section concisely,
focusing on key decisions, action items, speaker contributions, and conclusions.
Cite source files with [file:ID].

{chunk}

Section Summary:"""

_MERGE_PROMPT = """Merge the following section summaries into a single structured meeting
summary.  Follow this exact Markdown structure:

## Main Topics Discussed
## Key Decisions Made
## Action Items with Owners
## Important Points and Conclusions
## Roles & Speaker Contributions

Every factual claim must cite its source file with [file:ID] notation.

{summaries}

Final Summary:"""


def _load_meeting_and_transcript(meeting_id: int, *, user_id: str | None = None):
    with db.get_connection() as conn:
        m = db.get_meeting(conn, meeting_id, user_id=user_id)
        if not m:
            raise HTTPException(404, "Meeting not found")

        if m["status"] not in (MeetingStatus.READY, MeetingStatus.SUMMARIZING):
            raise HTTPException(400, "Meeting is not ready")

        files = db.list_ready_meeting_files(conn, meeting_id)
        return m, files


def _parse_key_points(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if isinstance(item, str)]


async def _ensure_per_file_summaries(
    files: list[dict],
) -> list[PerFileSummary]:
    per_files: list[PerFileSummary] = []
    for file in files:
        summary = (file.get("summary") or "").strip()
        key_points = _parse_key_points(file.get("key_points_json"))
        if not summary:
            transcript = (file.get("transcript") or "").strip()
            if transcript:
                try:
                    # Load structured segments for audio/video files so the
                    # per-file summary prompt can include a speaker timeline.
                    segments: list[dict] | None = None
                    raw_segments = file.get("segments_json")
                    if raw_segments and file["file_type"] in ("video", "audio"):
                        if isinstance(raw_segments, str):
                            try:
                                segments = json.loads(raw_segments)
                            except json.JSONDecodeError:
                                segments = None
                        elif isinstance(raw_segments, list):
                            segments = raw_segments

                    summary, key_points = await generate_per_file_summary(
                        file_type=file["file_type"],
                        file_name=file["file_name"],
                        text=transcript,
                        segments=segments,
                    )
                    from ....services.processor._pipeline_common import (
                        _persist_file_summary,
                    )

                    await asyncio.to_thread(
                        _persist_file_summary,
                        file["id"],
                        summary,
                        key_points,
                        meeting_id=file.get("meeting_id"),
                    )
                except Exception:
                    logger.warning(
                        "Failed generating missing per-file summary for file %d",
                        file["id"],
                        exc_info=True,
                    )
                    # Don't fall back to raw transcript — a truncated 800-char
                    # snippet pollutes the meeting summary and is unlikely to be
                    # useful.  The file will be re-attempted on the next pass.
                    summary = ""
                    key_points = []
                    await asyncio.to_thread(db.update_file_summary_status, file["id"], "failed")
        if summary:
            per_files.append(
                PerFileSummary(
                    file_id=file["id"],
                    file_name=file["file_name"],
                    file_type=FileType(file["file_type"]) if file["file_type"] else FileType.TXT,
                    summary=summary,
                    key_points=key_points,
                )
            )
    return per_files


def _compose_file_summary_context(files: list[PerFileSummary]) -> str:
    """Build the per-file context block for the meeting-summary LLM prompt.

    Each file is tagged ``[file:{file_id}]`` so the LLM can cite sources
    directly by file ID.  The frontend parses ``[file:N]`` patterns in the
    rendered Markdown and turns them into clickable links that navigate to
    the corresponding file in the Materials page.
    """
    parts: list[str] = []
    for file in files:
        block = (
            f"### File Summary [file:{file.file_id}] {file.file_type} — {file.file_name}\n"
            f"{file.summary}\n"
            f"Key points: {'; '.join(file.key_points) if file.key_points else 'N/A'}"
        )
        parts.append(block)
    return "\n\n".join(parts)


@router.get("/{meeting_id}/summary", response_model=SummaryResponse)
async def get_summary(
    meeting_id: int,
    principal: dict = Depends(verify_api_key),
):
    """Return the pre-generated meeting summary and its lifecycle status.

    Always returns 200 with ``status`` set to 'pending' | 'generating' |
    'ready' | 'failed'.  Returns 404 only when the meeting itself does not
    exist.  The frontend uses ``status`` to decide whether to show a
    spinner, an error banner, or the summary text.
    """
    user_id = _ownership_filter(principal)

    # Ownership check via get_meeting (404 if not owned)
    def _check_ownership():
        with db.get_connection() as conn:
            m = db.get_meeting(conn, meeting_id, user_id=user_id)
            if not m:
                raise HTTPException(404, "Meeting not found")

    await asyncio.to_thread(_check_ownership)
    result = await asyncio.to_thread(db.get_meeting_summary_with_status, meeting_id)
    if result is None:
        raise HTTPException(404, "Meeting not found")

    def _load_per_file_summaries():
        with db.get_connection() as conn:
            files = db.list_ready_meeting_files(conn, meeting_id)
            file_ids = [f["id"] for f in files]
            summaries = db.get_meeting_files_summaries(conn, file_ids) if file_ids else {}
        return [
            PerFileSummary(
                file_id=f["id"],
                file_name=f["file_name"],
                file_type=f["file_type"],
                summary=summaries.get(f["id"], ""),
                key_points=_parse_key_points(f.get("key_points_json")),
            )
            for f in files
            if f["id"] in summaries
        ]

    per_file = await asyncio.to_thread(_load_per_file_summaries)
    return SummaryResponse(
        meeting_id=meeting_id,
        status=result["status"],
        summary=result.get("summary"),
        tokens_used=0,
        per_file_summaries=per_file,
    )


@router.post("/{meeting_id}/summary", response_model=SummaryResponse)
@limiter.limit("5/minute")
async def generate_summary(
    request: Request,
    meeting_id: int,
    principal: dict = Depends(verify_api_key),
):
    """Generate a summary of the meeting content using LLM"""
    user_id = _ownership_filter(principal)
    from ....services.chain._meeting_summary_lifecycle import (
        acquire_summary_inflight,
        release_summary_inflight,
    )

    if not acquire_summary_inflight(meeting_id):
        raise HTTPException(409, "Summary generation already in progress for this meeting")

    m, files = await asyncio.to_thread(_load_meeting_and_transcript, meeting_id, user_id=user_id)
    if not files:
        release_summary_inflight(meeting_id)
        raise HTTPException(400, "Meeting has no transcript")

    # Push meeting to 'summarizing' so the badge reflects generation in progress.
    # persist_meeting_summary (success path) will flip it to 'ready'.
    await asyncio.to_thread(_set_meeting_status, meeting_id, "summarizing")
    await asyncio.to_thread(db.update_meeting_summary_status, meeting_id, "pending")

    try:
        from ....services.llm import get_llm
        from ....services.tokenizer import count_tokens

        llm = get_llm()
        tokens_used: int = 0
        per_file_summaries = await _ensure_per_file_summaries(files)
        if not per_file_summaries:
            raise HTTPException(400, "Meeting has no transcript")
        composed = _compose_file_summary_context(per_file_summaries)
        speaker_ctx = _build_speaker_context(files)
        if speaker_ctx:
            composed = composed + "\n\n" + speaker_ctx

        if count_tokens(composed) <= _SUMMARY_TOKEN_LIMIT:
            # Single-pass summarization
            prompt = _SUMMARY_PROMPT.format(title=m["title"], transcript=composed)
            response = await llm.ainvoke(prompt)
            summary = _normalize_file_citations(str(response.content))
            tokens_used = count_tokens(prompt) + count_tokens(summary)
        else:
            # Map-reduce: summarize chunks, then merge
            chunk_size = _SUMMARY_TOKEN_LIMIT // 2  # leave room for prompt overhead
            chunks = _split_transcript(composed, chunk_size)

            # Summarize each chunk in parallel
            chunk_prompts = [_CHUNK_SUMMARY_PROMPT.format(chunk=c) for c in chunks]
            chunk_results = await asyncio.gather(*(llm.ainvoke(p) for p in chunk_prompts))
            chunk_summaries = "\n\n---\n\n".join(str(r.content) for r in chunk_results)

            # Merge chunk summaries
            merge_prompt = _MERGE_PROMPT.format(summaries=chunk_summaries)
            merge_response = await llm.ainvoke(merge_prompt)
            summary = _normalize_file_citations(str(merge_response.content))
            tokens_used = sum(count_tokens(p) for p in chunk_prompts) + count_tokens(merge_prompt)
            tokens_used += count_tokens(summary)

        audit_log("generate", "summary", meeting_id, detail=f"tokens={tokens_used}")

        # Persist to DB + vector via unified helper (idempotent, content-hash aware).
        # persist_meeting_summary also transitions meeting: summarizing -> ready.
        contributing_ids = [f.file_id for f in per_file_summaries]
        contributing_names = [f.file_name for f in per_file_summaries]
        from ....services.chain._meeting_summary_lifecycle import persist_meeting_summary

        await asyncio.to_thread(
            persist_meeting_summary,
            meeting_id=meeting_id,
            title=m["title"],
            summary=summary,
            contributing_file_ids=contributing_ids,
            contributing_file_names=contributing_names,
        )

        return SummaryResponse(
            meeting_id=meeting_id,
            status="ready",
            summary=summary,
            tokens_used=tokens_used,
            per_file_summaries=per_file_summaries,
        )
    except HTTPException:
        await asyncio.to_thread(_set_meeting_status, meeting_id, "failed")
        await asyncio.to_thread(db.update_meeting_summary_status, meeting_id, "failed")
        raise
    except Exception as e:
        logger.error("Failed to generate summary for meeting %d: %s", meeting_id, e, exc_info=True)
        await asyncio.to_thread(_set_meeting_status, meeting_id, "failed")
        await asyncio.to_thread(db.update_meeting_summary_status, meeting_id, "failed")
        raise HTTPException(500, "Failed to generate summary") from e
    finally:
        release_summary_inflight(meeting_id)


@router.post("/{meeting_id}/summary/stream")
async def generate_summary_stream(
    meeting_id: int,
    principal: dict = Depends(verify_api_key),
):
    """Generate meeting summary via true SSE token streaming."""
    user_id = _ownership_filter(principal)
    from ....services.chain._meeting_summary_lifecycle import (
        acquire_summary_inflight,
        release_summary_inflight,
    )

    if not acquire_summary_inflight(meeting_id):
        raise HTTPException(409, "Summary generation already in progress for this meeting")

    m, files = await asyncio.to_thread(_load_meeting_and_transcript, meeting_id, user_id=user_id)
    if not files:
        release_summary_inflight(meeting_id)
        raise HTTPException(400, "Meeting has no transcript")

    # Push meeting to 'summarizing' so the badge reflects generation in progress.
    # persist_meeting_summary (success path) will flip it to 'ready'.
    await asyncio.to_thread(_set_meeting_status, meeting_id, "summarizing")
    await asyncio.to_thread(db.update_meeting_summary_status, meeting_id, "pending")

    per_file_summaries = await _ensure_per_file_summaries(files)
    if not per_file_summaries:
        await asyncio.to_thread(_set_meeting_status, meeting_id, "failed")
        await asyncio.to_thread(db.update_meeting_summary_status, meeting_id, "failed")
        raise HTTPException(400, "Meeting has no transcript")
    composed = _compose_file_summary_context(per_file_summaries)
    speaker_ctx2 = _build_speaker_context(files)
    if speaker_ctx2:
        composed = composed + "\n\n" + speaker_ctx2

    async def event_generator():
        try:
            from ....services.llm import get_llm
            from ....services.tokenizer import count_tokens

            llm = get_llm()
            tokens_used: int = 0
            accumulated = ""

            yield serialize_event({"type": "step", "step": "prepare", "status": "start"})

            if count_tokens(composed) <= _SUMMARY_TOKEN_LIMIT:
                prompt = _SUMMARY_PROMPT.format(title=m["title"], transcript=composed)
                yield serialize_event({"type": "step", "step": "generate", "status": "start"})
                stream = llm.astream(prompt)
                try:
                    async for event in stream:
                        token = str(getattr(event, "content", ""))
                        if not token:
                            continue
                        accumulated += token
                        yield serialize_event({"type": "token", "content": token})
                except Exception:
                    # Fallback for providers without streaming support.
                    response = await llm.ainvoke(prompt)
                    accumulated = str(response.content)
                    yield serialize_event({"type": "token", "content": accumulated})
                finally:
                    try:
                        if isinstance(stream, AsyncGenerator):
                            await asyncio.wait_for(stream.aclose(), timeout=5.0)
                    except TimeoutError:
                        logger.warning(
                            "summary stream aclose timed out, forcing close",
                            exc_info=True,
                        )
                        with contextlib.suppress(StopAsyncIteration, Exception):
                            await stream.athrow(GeneratorExit)  # type: ignore[union-attr]
                    except Exception:
                        logger.debug("summary stream aclose raised", exc_info=True)
                yield serialize_event({"type": "step", "step": "generate", "status": "done"})
                tokens_used = count_tokens(prompt) + count_tokens(accumulated)
            else:
                chunk_size = _SUMMARY_TOKEN_LIMIT // 2
                chunks = _split_transcript(composed, chunk_size)
                chunk_summaries: list[str] = []

                yield serialize_event({"type": "step", "step": "map_reduce", "status": "start"})
                # Process chunks in parallel with concurrency limit to avoid rate-limit storms.
                _chunk_semaphore = asyncio.Semaphore(8)

                async def _process_chunk(idx: int, chunk: str) -> tuple[int, str]:
                    async with _chunk_semaphore:
                        chunk_prompt = _CHUNK_SUMMARY_PROMPT.format(chunk=chunk)
                        try:
                            chunk_resp = await llm.ainvoke(chunk_prompt)
                            return idx, str(chunk_resp.content)
                        except Exception:
                            logger.warning(
                                "Chunk %d/%d summarization failed", idx, len(chunks), exc_info=True
                            )
                            return idx, f"[Chunk {idx} unavailable]"

                results = await asyncio.gather(
                    *[_process_chunk(idx, chunk) for idx, chunk in enumerate(chunks, start=1)]
                )
                results.sort(key=lambda r: r[0])
                chunk_summaries = [r[1] for r in results]
                for idx in range(1, len(chunks) + 1):
                    yield serialize_event(
                        {
                            "type": "step",
                            "step": "map_progress",
                            "status": "done",
                            "completed": idx,
                            "total": len(chunks),
                        }
                    )

                merge_prompt = _MERGE_PROMPT.format(summaries="\n\n---\n\n".join(chunk_summaries))
                yield serialize_event({"type": "step", "step": "merge", "status": "start"})
                merge_stream = llm.astream(merge_prompt)
                try:
                    async for event in merge_stream:
                        token = str(getattr(event, "content", ""))
                        if not token:
                            continue
                        accumulated += token
                        yield serialize_event({"type": "token", "content": token})
                except Exception:
                    merge_resp = await llm.ainvoke(merge_prompt)
                    accumulated = str(merge_resp.content)
                    yield serialize_event({"type": "token", "content": accumulated})
                finally:
                    try:
                        if isinstance(merge_stream, AsyncGenerator):
                            await asyncio.wait_for(merge_stream.aclose(), timeout=5.0)
                    except TimeoutError:
                        logger.warning("summary merge stream aclose timed out", exc_info=True)
                        with contextlib.suppress(StopAsyncIteration, Exception):
                            await merge_stream.athrow(GeneratorExit)  # type: ignore[union-attr]
                    except Exception:
                        logger.debug("summary merge stream aclose raised", exc_info=True)
                yield serialize_event({"type": "step", "step": "merge", "status": "done"})
                yield serialize_event({"type": "step", "step": "map_reduce", "status": "done"})
                tokens_used = count_tokens(merge_prompt) + count_tokens(accumulated)

            # Persist to DB + vector via unified helper (also transitions
            # meeting: summarizing -> ready atomically).
            _contributing_ids = [f.file_id for f in per_file_summaries]
            _contributing_names = [f.file_name for f in per_file_summaries]
            from ....services.chain._meeting_summary_lifecycle import (
                persist_meeting_summary,
            )

            await asyncio.to_thread(
                persist_meeting_summary,
                meeting_id=meeting_id,
                title=m["title"],
                summary=_normalize_file_citations(accumulated),
                contributing_file_ids=_contributing_ids,
                contributing_file_names=_contributing_names,
            )

            audit_log("generate", "summary_stream", meeting_id, detail=f"tokens={tokens_used}")
            yield serialize_event(
                {
                    "type": "done",
                    "meeting_id": meeting_id,
                    "tokens_used": tokens_used,
                }
            )
        except Exception as e:
            logger.error(
                "Failed to stream summary for meeting %d: %s", meeting_id, e, exc_info=True
            )
            await asyncio.to_thread(_set_meeting_status, meeting_id, "failed")
            await asyncio.to_thread(db.update_meeting_summary_status, meeting_id, "failed")
            yield serialize_event({"type": "error", "message": "Failed to generate summary"})
        finally:
            release_summary_inflight(meeting_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
        },
    )


def _split_transcript(text: str, max_tokens: int) -> list[str]:
    """Split transcript into chunks that fit within max_tokens.

    Splits on paragraph boundaries when possible.
    """
    from ....services.tokenizer import count_tokens

    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for para in paragraphs:
        para_tokens = count_tokens(para)
        if current_tokens + para_tokens > max_tokens and current:
            chunks.append("\n\n".join(current))
            current = [para]
            current_tokens = para_tokens
        else:
            current.append(para)
            current_tokens += para_tokens

    if current:
        chunks.append("\n\n".join(current))

    return chunks
