import asyncio
import json
from pathlib import Path as FilePath
from typing import cast

from fastapi import Depends, HTTPException

from ....core import database as db
from ....core.audit import audit_log
from ....core.config import settings
from ....core.constants import UPLOAD_DIR
from ....core.security import verify_api_key
from ....models.schemas import MeetingStatus, MessageResponse
from ....models.schemas.meetings import (
    CaptionsTimeline,
    FileTimelineResponse,
    PagesTimeline,
    SegmentsTimeline,
    TextTimeline,
    TimelineCaptionItem,
    TimelinePageItem,
    TimelineSegmentItem,
)
from ....services.files._kinds import resolve_kind_name
from ....services.parser import parse_structured
from ._common import _ownership_filter, logger, router
from ._structured import normalize_page_image_assets, parse_structured_json
from ._timestamps import _apply_speaker_mappings

# NOTE: GET /{meeting_id}/files/{file_id} is handled by the file_download
# router (api/routers/file_download.py) which supports both header and
# token-based auth for secure file downloads without URL-key exposure.


@router.delete("/{meeting_id}/files/{file_id}", response_model=MessageResponse)
async def delete_meeting_file(
    meeting_id: int,
    file_id: int,
    principal: dict = Depends(verify_api_key),
):
    """Delete a specific file from a meeting"""
    user_id = _ownership_filter(principal)

    def _fetch():
        with db.get_connection() as conn:
            m = db.get_meeting(conn, meeting_id, user_id=user_id)
            if not m:
                raise HTTPException(404, "Meeting not found")
            f = db.get_meeting_file(conn, file_id, user_id=user_id)
            if not f or f["meeting_id"] != meeting_id:
                raise HTTPException(404, "File not found")
            return f

    f = await asyncio.to_thread(_fetch)

    # Clean up vectors first so DB row remains available for retry on failure.
    try:
        from ....services.rag import delete_meeting_chunks

        delete_meeting_chunks(meeting_id, file_id=file_id)
    except Exception as exc:
        logger.error("Failed to clean up vectors for file %d", file_id, exc_info=True)
        raise HTTPException(500, "Failed to delete file vectors") from exc

    # Clean up file on disk
    file_path = FilePath(f["file_path"])
    try:
        file_path.unlink(missing_ok=True)
    except Exception as exc:
        logger.error("Failed to delete file %d from disk", file_id, exc_info=True)
        raise HTTPException(500, "Failed to delete file from disk") from exc

    def _delete_db() -> None:
        with db.get_write_connection() as conn:
            current = db.get_meeting_file(conn, file_id, user_id=user_id)
            if not current or current["meeting_id"] != meeting_id:
                raise HTTPException(404, "File not found")
            db.delete_meeting_file(conn, file_id, user_id=user_id)
            # Cascade: remove pending vector deletion entries matching this
            # file's deterministic chunk ID pattern so the cleanup loop
            # doesn't retry a non-existent file.
            conn.execute(
                "DELETE FROM pending_vector_deletions WHERE embedding_id LIKE ?",
                (f"meeting_{meeting_id}_chunk_%",),
            )

    await asyncio.to_thread(_delete_db)

    # Invalidate meeting-level summary — the removed file may have been cited
    try:
        from ....services.chain._meeting_summary_lifecycle import (
            enqueue_meeting_summary_rebuild,
            invalidate_meeting_summary,
        )

        await asyncio.to_thread(invalidate_meeting_summary, meeting_id)
        await enqueue_meeting_summary_rebuild(meeting_id)
    except Exception:
        logger.warning(
            "Failed to invalidate/rebuild meeting summary after file %d deletion",
            file_id,
            exc_info=True,
        )

    audit_log("delete", "meeting_file", file_id)
    return {"message": "File deleted"}


@router.get("/{meeting_id}/files/{file_id}/timeline", response_model=FileTimelineResponse)
async def get_file_timeline(
    meeting_id: int,
    file_id: int,
    principal: dict = Depends(verify_api_key),
):
    """Get kind-specific timeline for a single file.

    Returns a discriminated-union shape determined by the file kind:
    - ``segments`` for video/audio (timestamped segments)
    - ``pages`` for PDF/PPTX (page outline)
    - ``image`` for images (OCR text)
    - ``text`` for plain text files
    """
    user_id = _ownership_filter(principal)

    def _fetch():
        with db.get_connection() as conn:
            m = db.get_meeting(conn, meeting_id, user_id=user_id)
            if not m:
                return None, None
            f = db.get_meeting_file(conn, file_id, user_id=user_id)
            return m, f

    m, f = await asyncio.to_thread(_fetch)
    if m is None:
        raise HTTPException(404, "Meeting not found")
    if f is None or f["meeting_id"] != meeting_id:
        raise HTTPException(404, "File not found")
    if f["status"] != MeetingStatus.READY:
        raise HTTPException(400, "File is not ready")

    file_name: str = f["file_name"]
    file_type: str = f["file_type"]
    kind_name = resolve_kind_name(file_name)

    # --- Audio / Video → segments timeline ---
    if file_type in ("video", "audio") and kind_name in ("video", "audio"):
        return await _build_segments_timeline(file_id, file_name)

    # --- PDF / PPTX → pages timeline ---
    if kind_name in ("pdf", "pptx"):
        return await _build_pages_timeline(file_id, file_name, f)

    # --- Image → captions/ocr ---
    if kind_name == "image":
        structured = parse_structured_json(f)
        if f.get("structured_kind") == "captions" and isinstance(structured, list):
            captions = [
                {
                    "caption": item.get("caption"),
                    "ocr_text": item.get("ocr_text"),
                }
                for item in structured
                if isinstance(item, dict)
            ]
        else:
            transcript = f.get("transcript") or ""
            captions = [{"caption": None, "ocr_text": transcript or None}]
        return CaptionsTimeline(
            kind="captions",
            file_id=file_id,
            file_name=file_name,
            captions=cast(list[TimelineCaptionItem], captions),
        )

    # --- Fallback: plain text ---
    transcript = f.get("transcript") or ""
    word_count = len(transcript.split()) if transcript else 0
    return TextTimeline(
        kind="text",
        file_id=file_id,
        file_name=file_name,
        text=transcript,
        word_count=word_count,
    )


async def _build_segments_timeline(file_id: int, file_name: str) -> SegmentsTimeline:
    """Build a segments timeline from cached or live transcript."""

    def _fetch_segments():
        with db.get_connection() as conn:
            return db.get_segments_json(conn, file_id)

    segments_json = await asyncio.to_thread(_fetch_segments)

    if segments_json:
        segments = json.loads(segments_json)
        segments = await asyncio.to_thread(_apply_speaker_mappings, file_id, segments)
        total_duration = segments[-1]["end"] if segments else None
        speakers = {s.get("speaker") for s in segments if s.get("speaker")}
        speaker_count = len(speakers) if speakers else None

        return SegmentsTimeline(
            kind="segments",
            file_id=file_id,
            file_name=file_name,
            segments=cast(
                list[TimelineSegmentItem],
                [
                    {
                        "start": s["start"],
                        "end": s["end"],
                        "text": s.get("text", ""),
                        "speaker": s.get("speaker"),
                    }
                    for s in segments
                ],
            ),
            total_duration=total_duration,
            speaker_count=speaker_count,
        )

    # No cached segments — return empty timeline
    return SegmentsTimeline(
        kind="segments",
        file_id=file_id,
        file_name=file_name,
        segments=[],
        total_duration=None,
        speaker_count=None,
    )


async def _build_pages_timeline(file_id: int, file_name: str, file_record: dict) -> PagesTimeline:
    """Build a pages timeline from transcript or live parse."""
    structured = parse_structured_json(file_record)
    pages: list[dict]
    if file_record.get("structured_kind") == "pages" and isinstance(structured, list):
        pages = []
        for item in structured:
            if not isinstance(item, dict):
                continue
            page_num = item.get("page_num") or item.get("page_number")
            try:
                if isinstance(page_num, int | float | str):
                    page_num = int(page_num)
                else:
                    page_num = len(pages) + 1
            except (TypeError, ValueError):
                page_num = len(pages) + 1
            pages.append(
                {
                    "page_num": page_num,
                    "text": str(item.get("text", "")),
                    "heading": item.get("heading"),
                    "image_assets": normalize_page_image_assets(item.get("image_assets")),
                }
            )
        if not pages:
            pages = [{"page_num": 1, "text": "", "heading": None}]
    else:
        transcript = file_record.get("transcript") or ""
        pages = _split_transcript_into_pages(transcript)
        if len(pages) == 1 and pages[0]["page_num"] == 1 and file_record.get("file_path"):
            parsed = await _parse_pages_from_source(file_record["file_path"])
            if parsed:
                pages = parsed

    return PagesTimeline(
        kind="pages",
        file_id=file_id,
        file_name=file_name,
        pages=cast(list[TimelinePageItem], pages),
        page_count=len(pages),
    )


def _split_transcript_into_pages(transcript: str) -> list[dict]:
    """Split transcript text into page-like items.

    Attempts to detect ``--- Page N ---`` markers from the parser.
    Falls back to a single-page representation.
    """
    if not transcript:
        return [{"page_num": 1, "text": "", "heading": None}]

    # Detect page markers from the parser (marker / MinerU output format)
    import re

    page_marker = re.compile(r"---\s*Page\s+(\d+)\s*---", re.IGNORECASE)
    parts = page_marker.split(transcript)

    if len(parts) > 1:
        # parts: [pre, num1, text1, num2, text2, ...]
        pages = []
        i = 1
        while i < len(parts):
            page_num = int(parts[i])
            text = parts[i + 1].strip() if i + 1 < len(parts) else ""
            heading = _extract_heading(text)
            pages.append(
                {"page_num": page_num, "text": text, "heading": heading, "image_assets": []}
            )
            i += 2
        return pages

    # No page markers — treat as single page
    heading = _extract_heading(transcript)
    return [{"page_num": 1, "text": transcript, "heading": heading, "image_assets": []}]


def _extract_heading(text: str) -> str | None:
    """Extract the first non-empty line as a heading candidate."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and len(stripped) < 120:
            return stripped
    return None


async def _parse_pages_from_source(file_path: str) -> list[dict] | None:
    p = FilePath(file_path)
    if not p.exists():
        p = UPLOAD_DIR / p.name
    if not p.exists():
        return None
    try:
        parsed = await asyncio.wait_for(
            asyncio.to_thread(parse_structured, p),
            timeout=settings.PARSE_TIMEOUT_SECONDS,
        )
    except Exception:
        return None

    pages: list[dict] = []
    for page in parsed.pages:
        heading = _extract_heading(page.text)
        pages.append(
            {
                "page_num": page.page_num,
                "text": page.text,
                "heading": heading,
                "image_assets": normalize_page_image_assets(
                    [
                        {
                            "storage_path": getattr(a, "storage_path", None),
                            "thumbnail_path": getattr(a, "thumbnail_path", None),
                            "caption": getattr(a, "caption", None),
                            "ocr_text": getattr(a, "ocr_text", None),
                        }
                        for a in (page.image_assets or ())
                    ]
                ),
            }
        )
    return pages
