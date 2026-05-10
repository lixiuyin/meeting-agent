import asyncio
import json
from typing import Any
from urllib.parse import quote

from fastapi import Depends, HTTPException, Query

from ....core import database as db
from ....core.audit import audit_log
from ....core.security import verify_api_key
from ....models.schemas import ExportFormat, ExportResponse, MeetingStatus
from ....models.schemas.meetings import (
    ExportFileCaptions,
    ExportFilePages,
    ExportFileSegments,
    ExportFileText,
    ExportImageItem,
    ExportMeetingPayload,
    ExportPageItem,
    TranscriptSegment,
)
from ._common import _ownership_filter, router
from ._structured import normalize_page_image_assets, parse_structured_json


def _build_absolute_asset_url(base_url: str, storage_path: str) -> str:
    """Build an absolute URL for an asset path.

    Consumers must provide the API key header to access these URLs.
    """
    return f"{base_url}api/v1/meetings/assets?path={quote(storage_path)}"


def _build_file_pages(file: dict[str, Any], base_url: str) -> ExportFilePages:
    structured = parse_structured_json(file)
    pages: list[ExportPageItem] = []
    if file.get("structured_kind") == "pages" and isinstance(structured, list):
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
            assets = normalize_page_image_assets(item.get("image_assets"))
            pages.append(
                ExportPageItem(
                    page_num=page_num,
                    heading=item.get("heading"),
                    text=str(item.get("text", "")),
                    images=[
                        ExportImageItem(
                            url=_build_absolute_asset_url(base_url, a["storage_path"]),
                            storage_path=a["storage_path"],
                            caption=a.get("caption"),
                            ocr_text=a.get("ocr_text"),
                        )
                        for a in assets
                    ],
                )
            )
    else:
        transcript = file.get("transcript") or ""
        if transcript:
            pages.append(ExportPageItem(page_num=1, heading=None, text=transcript, images=[]))
    return ExportFilePages(
        file_id=file["id"],
        file_name=file["file_name"],
        file_type=file["file_type"],
        kind="pages",
        summary=file.get("summary"),
        pages=pages,
    )


def _build_file_captions(file: dict[str, Any], base_url: str) -> ExportFileCaptions:
    structured = parse_structured_json(file)
    images: list[ExportImageItem] = []
    if file.get("structured_kind") == "captions" and isinstance(structured, list):
        for item in structured:
            if not isinstance(item, dict):
                continue
            images.append(
                ExportImageItem(
                    url=_build_absolute_asset_url(base_url, file.get("file_path", "")),
                    storage_path=file.get("file_path", ""),
                    caption=item.get("caption"),
                    ocr_text=item.get("ocr_text"),
                )
            )
    else:
        transcript = file.get("transcript") or ""
        images.append(
            ExportImageItem(
                url="",
                storage_path="",
                caption=None,
                ocr_text=transcript or None,
            )
        )
    image_url = _build_absolute_asset_url(base_url, file.get("file_path", ""))
    return ExportFileCaptions(
        file_id=file["id"],
        file_name=file["file_name"],
        file_type=file["file_type"],
        kind="captions",
        image_url=image_url,
        images=images,
    )


def _build_file_segments(file: dict[str, Any]) -> ExportFileSegments:
    segments_raw = file.get("segments_json")
    segments: list[TranscriptSegment] = []
    total_duration = None
    if segments_raw:
        try:
            parsed = json.loads(segments_raw) if isinstance(segments_raw, str) else segments_raw
            parsed_segments = parsed if isinstance(parsed, list) else []
            for s in parsed_segments:
                if not isinstance(s, dict):
                    continue
                segments.append(
                    TranscriptSegment(
                        start=s.get("start", 0),
                        end=s.get("end", 0),
                        text=s.get("text", ""),
                        speaker=s.get("speaker"),
                    )
                )
            total_duration = parsed_segments[-1].get("end") if parsed_segments else None
        except (json.JSONDecodeError, KeyError, IndexError):
            pass
    return ExportFileSegments(
        file_id=file["id"],
        file_name=file["file_name"],
        file_type=file["file_type"],
        kind="segments",
        summary=file.get("summary"),
        segments=segments,
        total_duration=total_duration,
    )


def _build_file_text(file: dict[str, Any]) -> ExportFileText:
    transcript = file.get("transcript") or ""
    return ExportFileText(
        file_id=file["id"],
        file_name=file["file_name"],
        file_type=file["file_type"],
        kind="text",
        summary=file.get("summary"),
        text=transcript,
        word_count=len(transcript.split()) if transcript else 0,
    )


def _render_markdown(payload: ExportMeetingPayload) -> str:
    lines = [f"# {payload.title}", ""]
    if payload.description:
        lines.append(f"**Description:** {payload.description}")
        lines.append("")
    if payload.meeting_date:
        lines.append(f"**Meeting Date:** {payload.meeting_date}")
        lines.append("")
    lines.append(f"**Created:** {payload.created_at}")
    lines.extend(["", "---", ""])

    for f in payload.files:
        if isinstance(f, ExportFilePages):
            lines.append(f"## {f.file_name}")
            lines.append("")
            if f.summary:
                lines.append(f"**Summary:** {f.summary}")
                lines.append("")
            for page in f.pages:
                heading_suffix = f": {page.heading}" if page.heading else ""
                lines.append(f"### Page {page.page_num}{heading_suffix}")
                lines.append("")
                lines.append(page.text.strip() or "_Empty page._")
                lines.append("")
                if page.images:
                    lines.append("#### Images")
                    lines.append("")
                    for img in page.images:
                        if img.caption:
                            lines.append(f"**VLM Description:** {img.caption}")
                            lines.append("")
                        if img.ocr_text:
                            lines.append(f"**OCR:** {img.ocr_text}")
                            lines.append("")
                lines.append("")
        elif isinstance(f, ExportFileCaptions):
            lines.append(f"## {f.file_name}")
            lines.append("")
            if f.image_url:
                lines.append(f"![{f.file_name}]({f.image_url})")
                lines.append("")
            lines.append("## Captions / OCR")
            lines.append("")
            for idx, img in enumerate(f.images):
                lines.append(f"### Item {idx + 1}")
                lines.append("")
                if img.caption:
                    lines.append(f"**VLM Description:** {img.caption}")
                    lines.append("")
                if img.ocr_text:
                    lines.append("**OCR Text**:")
                    lines.append("")
                    lines.append(img.ocr_text)
                    lines.append("")
            lines.append("")
        elif isinstance(f, ExportFileSegments):
            lines.append(f"## {f.file_name}")
            lines.append("")
            if f.summary:
                lines.append(f"**Summary:** {f.summary}")
                lines.append("")
            lines.append("## Transcript Timestamps")
            lines.append("")
            for seg in f.segments:
                start_m = f"{int(seg.start // 60):02d}:{int(seg.start % 60):02d}"
                end_m = f"{int(seg.end // 60):02d}:{int(seg.end % 60):02d}"
                speaker = f"**{seg.speaker}** · " if seg.speaker else ""
                lines.append(f"- [{start_m} - {end_m}] {speaker}{seg.text}")
            lines.append("")
        elif isinstance(f, ExportFileText):
            lines.append(f"## {f.file_name}")
            lines.append("")
            lines.append(f"Word count: {f.word_count}")
            lines.append("")
            lines.append(f.text or "_No text available._")
            lines.append("")

    # Append legacy transcript at the end
    if payload.transcript:
        lines.extend(["---", "", "## Transcript (Legacy)", "", payload.transcript])

    return "\n".join(lines)


def _render_txt(payload: ExportMeetingPayload) -> str:
    md = _render_markdown(payload)
    import re

    return re.sub(r"!\[[^\]]*\]\([^)]*\)", "", md)


@router.get("/{meeting_id}/export", response_model=ExportResponse)
async def export_meeting(
    meeting_id: int,
    format: ExportFormat = Query(ExportFormat.MARKDOWN),
    principal: dict = Depends(verify_api_key),
):
    """Export meeting data in various formats (JSON, Markdown, TXT)."""
    ownership = _ownership_filter(principal)

    def _fetch():
        with db.get_connection() as conn:
            m = db.get_meeting(conn, meeting_id, user_id=ownership)
            if not m:
                raise HTTPException(404, "Meeting not found")
            if m["status"] != MeetingStatus.READY:
                raise HTTPException(400, "Meeting is not ready")
            transcript = (
                db.get_meeting_transcripts(conn, meeting_id, user_id=ownership)
                or m.get("transcript")
                or ""
            )
            files = db.list_meeting_files(conn, meeting_id, user_id=ownership)
            return m, transcript, files

    m, transcript, files = await asyncio.to_thread(_fetch)
    title = m["title"]
    safe_title = "".join(c for c in title if c.isalnum() or c in " _-").strip()

    # Build base URL from request context (use relative path; frontend proxy resolves)
    base_url = "/"

    # Build structured payload
    from ....services.files._kinds import resolve_kind_name

    export_files: list[
        ExportFilePages | ExportFileCaptions | ExportFileSegments | ExportFileText
    ] = []
    for f in files:
        kind_name = resolve_kind_name(f["file_name"])
        file_type = f["file_type"]
        if kind_name in ("pdf", "pptx"):
            export_files.append(_build_file_pages(f, base_url))
        elif kind_name == "image":
            export_files.append(_build_file_captions(f, base_url))
        elif file_type in ("video", "audio") and kind_name in ("video", "audio"):
            export_files.append(_build_file_segments(f))
        else:
            export_files.append(_build_file_text(f))

    payload = ExportMeetingPayload(
        meeting_id=m["id"],
        title=m["title"],
        description=m.get("description"),
        meeting_date=str(m.get("meeting_date")) if m.get("meeting_date") else None,
        created_at=str(m["created_at"]),
        transcript=transcript,
        files=export_files,
    )

    if format == ExportFormat.JSON:
        content = json.dumps(payload.model_dump(mode="json"), indent=2, ensure_ascii=False)
        filename = f"{safe_title}.json"
        content_type = "application/json"
        audit_log("export", "meeting", meeting_id, detail=f"format={format.value}")
        return ExportResponse(
            meeting_id=meeting_id,
            format=format,
            content=content,
            data=payload,
            filename=filename,
            content_type=content_type,
        )

    if format == ExportFormat.MARKDOWN:
        content = _render_markdown(payload)
        filename = f"{safe_title}.md"
        content_type = "text/markdown"
    else:  # TXT
        content = _render_txt(payload)
        filename = f"{safe_title}.txt"
        content_type = "text/plain"

    audit_log("export", "meeting", meeting_id, detail=f"format={format.value}")
    return ExportResponse(
        meeting_id=meeting_id,
        format=format,
        content=content,
        data=None,
        filename=filename,
        content_type=content_type,
    )
