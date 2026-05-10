"""Background task: process uploaded meeting file -> transcribe/parse -> index"""

import asyncio
import contextlib
import json
import logging
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from ...core.config import settings
from ...core.constants import UPLOAD_DIR
from ...core.database import (
    get_meeting,
    get_meeting_file,
    get_write_connection,
    mark_chroma_indexed,
    mark_raganything_failed,
    mark_raganything_indexed,
    save_segments_json,
    update_meeting_file_artefact,
    update_meeting_file_raganything,
    update_meeting_file_status,
    update_meeting_status,
)
from ...core.trace import TraceContext
from ..files._kinds import timeline_kinds
from ..rag import (
    delete_meeting_chunks,
    index_meeting,
    index_meeting_pages,
    index_meeting_segments,
)
from ..rag._raganything import index_file_with_raganything, index_with_raganything
from ..transcriber import transcribe_with_timestamps as transcribe_with_timestamps
from ._pipeline_common import (
    _build_ingest_observability_metadata,
    _file_content_hash,
    _has_non_text_indexable_content,
    _metric_float,
    _metric_int,
    _metric_str,
    _update_meeting_status_from_files,
    _ws_notify_complete,
    _ws_notify_progress,
)
from ._pipeline_meeting import process_meeting  # noqa: F401
from ._processors import (
    AVFileProcessor,
    DocumentFileProcessor,
    ImageFileProcessor,
    ProcessorContext,
    TextFileProcessor,
)

logger = logging.getLogger(__name__)

_AV_KINDS = timeline_kinds()


def _mark_auto_failed(meeting_id: int) -> None:
    with get_write_connection() as conn:
        row = conn.execute("SELECT status FROM meetings WHERE id=?", (meeting_id,)).fetchone()
        if row and row["status"] in ("summarizing", "ready"):
            update_meeting_status(conn, meeting_id, "failed")
        conn.execute(
            "UPDATE meetings SET summary_status='failed', updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (meeting_id,),
        )


def _should_route_artefact_to_text_chunking(artefact) -> bool:
    """Return True when a non-text artefact should reuse the text chunking path."""
    return settings.NON_TEXT_CHUNKING_STRATEGY.strip().lower() == "text" and (
        artefact.segments is not None or artefact.parsed_doc is not None
    )


def _format_timestamp_label(seconds: float) -> str:
    total = int(max(seconds, 0.0))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _build_text_route_payload(artefact) -> str:
    """Build the text fed into ``index_meeting()`` when routing through text chunking.

    Reduces signal loss vs. raw ``artefact.text``:
    - audio/video: prefix each segment with ``[mm:ss]`` (and speaker, if present)
      so temporal hints survive flat chunking.
    - parsed documents: include table markdown and image caption/OCR via
      ``ParsedDocument.to_indexable_text()``.
    Falls back to ``artefact.text`` for images and pure-text artefacts.
    """
    segments = artefact.segments or []
    if len(segments) > 1:
        lines: list[str] = []
        for seg in segments:
            text_part = (seg.get("text") or "").strip()
            if not text_part:
                continue
            ts = _format_timestamp_label(float(seg.get("start", 0.0) or 0.0))
            speaker = seg.get("speaker")
            if speaker and speaker != "image":
                lines.append(f"[{ts}] {speaker}: {text_part}")
            else:
                lines.append(f"[{ts}] {text_part}")
        if lines:
            return "\n".join(lines)
    if artefact.parsed_doc is not None and not segments:
        enriched = artefact.parsed_doc.to_indexable_text()
        if enriched:
            return enriched
    return artefact.text


async def process_meeting_file(
    file_id: int,
    trace: TraceContext | None = None,
    *,
    force_meeting_summary: bool = False,
) -> TraceContext:
    """Process a single meeting file: transcribe/parse -> index -> update status.

    This updates the meeting's overall status based on all files' completion.

    Args:
        file_id: the meeting file ID to process.
        trace: optional trace context for benchmarking; a fresh one is created if None.
        force_meeting_summary: bypass MEETING_AUTO_SUMMARIZE_FILES gate for meeting
            summary rebuild.  Used by reprocess to always rebuild the meeting summary
            even when auto-summarize is disabled.

    Returns:
        The TraceContext used during processing.
    """
    from ...core.database import get_connection

    if trace is None:
        trace = TraceContext()

    meeting_id = None
    try:
        # Fetch file record
        trace.start_span("fetch_metadata", "metadata")

        def _fetch_file():
            with get_connection() as conn:
                return get_meeting_file(conn, file_id)

        file_record = await asyncio.to_thread(_fetch_file)
        if not file_record:
            trace.finish_span("fetch_metadata", "error")
            logger.error("Meeting file %d not found", file_id)

            def _mark_missing_file_error():
                with get_write_connection() as conn:
                    update_meeting_file_status(
                        conn, file_id, "error", error_message="File record not found"
                    )

            await asyncio.to_thread(_mark_missing_file_error)
            logger.info("ingest_trace %s", json.dumps(trace.to_dict()))
            return trace

        meeting_id = file_record["meeting_id"]
        file_path = Path(file_record["file_path"])

        # Rebase Docker absolute paths to local UPLOAD_DIR when running locally.
        # Validate that the rebased file matches the stored basename to prevent
        # cross-meeting filename collisions.
        if not file_path.exists():
            candidate = UPLOAD_DIR / file_path.name
            stored_name = Path(file_record["file_path"]).name
            if not candidate.exists() or candidate.name != stored_name:
                raise FileNotFoundError(
                    f"File missing on disk and rebase rejected (cross-meeting risk): {file_path}"
                )
            file_path = candidate
        file_type = file_record["file_type"]
        file_name = file_record["file_name"]

        # Fetch meeting date for metadata filtering
        def _fetch_meeting_date():
            with get_connection() as conn:
                m = get_meeting(conn, meeting_id)
            return (m.get("meeting_date"), m.get("user_id")) if m else (None, None)

        meeting_date, meeting_user_id = await asyncio.to_thread(_fetch_meeting_date)
        trace.finish_span("fetch_metadata")

        # Check file exists
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        # Mark as processing
        def _mark_processing():
            with get_write_connection() as conn:
                update_meeting_file_status(conn, file_id, "processing")
                update_meeting_status(conn, meeting_id, "processing")

        await asyncio.to_thread(_mark_processing)

        await _ws_notify_progress(
            meeting_id,
            "processing",
            0.2,
            f"Processing {file_name}",
            user_id=meeting_user_id,
        )

        # ── Convert PPT/PPTX to PDF, replacing the original file ───────────
        if file_type == "ppt" and file_path.suffix.lower() in (".ppt", ".pptx"):
            file_path, file_type, file_name = await _convert_pptx_to_pdf(
                file_id=file_id,
                meeting_id=meeting_id,
                file_path=file_path,
                file_name=file_name,
            )

        processor = _resolve_processor(file_type)
        extract_span_label = "transcribe" if file_type in _AV_KINDS else "parse"
        trace.start_span(extract_span_label, "extract")
        try:
            artefact = await processor.process(
                ProcessorContext(
                    file_id=file_id,
                    meeting_id=meeting_id,
                    file_type=file_type,
                    file_name=file_name,
                    file_path=file_path,
                    meeting_date=meeting_date,
                    trace=trace,
                )
            )
            trace.finish_span(extract_span_label)
        except Exception:
            trace.finish_span(extract_span_label, "error")
            raise
        text = artefact.text

        await _ws_notify_progress(
            meeting_id,
            "processing",
            0.6,
            f"Text extracted from {file_name}",
            user_id=meeting_user_id,
        )

        # Guard: empty or near-empty transcript
        _MIN_EXTRACTED_LENGTH = 50
        text_len = len(text.strip()) if text else 0
        has_non_text_content = _has_non_text_indexable_content(artefact)
        if (not text or text_len < _MIN_EXTRACTED_LENGTH) and not has_non_text_content:
            logger.warning(
                "File %d: extracted text too short (%d chars), marking as failed",
                file_id,
                text_len,
            )
            error_msg = "No text content could be extracted"

            def _mark_file_empty():
                with get_write_connection() as conn:
                    update_meeting_file_status(conn, file_id, "error", error_message=error_msg)
                    _update_meeting_status_from_files(conn, meeting_id)

            await asyncio.to_thread(_mark_file_empty)
            logger.info("ingest_trace %s", json.dumps(trace.to_dict()))
            return trace
        if text_len < _MIN_EXTRACTED_LENGTH and has_non_text_content:
            logger.info(
                "File %d: text too short (%d chars) but multimodal/table content exists; continue",
                file_id,
                text_len,
            )

        # Incremental index: skip only for already-ready files with unchanged bytes.
        # New uploads are created in "processing" status with a precomputed file hash.
        # We must not skip the initial indexing pass in that case.
        new_hash = _file_content_hash(file_path)
        old_hash = file_record.get("content_hash")
        is_already_ready = file_record.get("status") == "ready"
        if old_hash and old_hash == new_hash and is_already_ready:
            logger.info("File %d content unchanged (hash=%s), skipping re-index", file_id, new_hash)

            def _mark_file_ready_skip():
                with get_write_connection() as conn:
                    update_meeting_file_status(conn, file_id, "ready", error_message=None)
                    _update_meeting_status_from_files(conn, meeting_id)

            await asyncio.to_thread(_mark_file_ready_skip)
            logger.info("ingest_trace %s", json.dumps(trace.to_dict()))
            return trace

        # Index into vector database — use structured indexer when available
        trace.start_span("index_meeting", "index")
        base_meta = {
            "title": file_name,
            "file_type": file_type,
            "file_id": file_id,
            "file_name": file_name,
            "meeting_date": int(meeting_date.replace("-", "")) if meeting_date else 0,
            "user_id": meeting_user_id or "default",
            "chunk_strategy_route": (
                "text" if _should_route_artefact_to_text_chunking(artefact) else "native"
            ),
            **_build_ingest_observability_metadata(artefact.metrics),
        }
        # Propagate original_format from parsed document (e.g. PPTX→PDF conversion)
        if artefact.parsed_doc and artefact.parsed_doc.metadata.get("original_format"):
            base_meta["original_format"] = artefact.parsed_doc.metadata["original_format"]
        if file_id is not None and (
            artefact.segments is not None or _should_route_artefact_to_text_chunking(artefact)
        ):
            await asyncio.to_thread(delete_meeting_chunks, meeting_id, file_id=file_id)

        if _should_route_artefact_to_text_chunking(artefact):
            route_text = _build_text_route_payload(artefact)
            await asyncio.to_thread(
                index_meeting,
                meeting_id=meeting_id,
                text=route_text,
                metadata=base_meta,
                trace=trace,
            )
        elif artefact.segments is not None:
            await asyncio.to_thread(
                index_meeting_segments,
                meeting_id=meeting_id,
                segments=artefact.segments,
                metadata=base_meta,
                trace=trace,
            )
            if artefact.aux_segments:
                await asyncio.to_thread(
                    index_meeting_segments,
                    meeting_id=meeting_id,
                    segments=artefact.aux_segments,
                    metadata={**base_meta, "source_kind": "image"},
                    trace=trace,
                )
        elif artefact.parsed_doc is not None:
            await asyncio.to_thread(
                index_meeting_pages,
                meeting_id=meeting_id,
                parsed=artefact.parsed_doc,
                metadata=base_meta,
                trace=trace,
            )
        else:
            await asyncio.to_thread(
                index_meeting,
                meeting_id=meeting_id,
                text=text,
                metadata=base_meta,
                trace=trace,
            )

        if settings.RAGANYTHING_ENABLED:
            doc_id = f"meeting_{meeting_id}_file_{file_id if file_id is not None else 'unknown'}"
            try:
                if file_type in {"pdf", "ppt", "doc", "xls", "csv", "image", "video"}:
                    await asyncio.to_thread(
                        index_file_with_raganything,
                        meeting_id=meeting_id,
                        file_id=file_id,
                        file_path=str(file_path),
                        metadata={"title": file_name, "file_type": file_type},
                    )
                else:
                    await asyncio.to_thread(
                        index_with_raganything,
                        meeting_id=meeting_id,
                        file_id=file_id,
                        parsed=artefact.parsed_doc,
                        text=text if artefact.parsed_doc is None else None,
                        file_path=str(file_path),
                        metadata={"title": file_name, "file_type": file_type},
                    )

                def _mark_raganything_indexed() -> None:
                    with get_write_connection() as conn:
                        update_meeting_file_raganything(
                            conn,
                            file_id,
                            doc_id=doc_id,
                            indexed_at=datetime.now(UTC).isoformat(),
                        )
                        mark_raganything_indexed(
                            conn,
                            file_id=file_id,
                            meeting_id=meeting_id,
                            doc_id=doc_id,
                            indexed_at=datetime.now(UTC).isoformat(),
                        )

                await asyncio.to_thread(_mark_raganything_indexed)
            except Exception as exc:
                error_msg = f"{type(exc).__name__}: {exc}"

                def _mark_raganything_failed(*, error_msg: str = error_msg) -> None:
                    with get_write_connection() as conn:
                        mark_raganything_failed(
                            conn,
                            file_id=file_id,
                            meeting_id=meeting_id,
                            error=error_msg,
                        )

                await asyncio.to_thread(_mark_raganything_failed)
                logger.warning(
                    "RAGAnything indexing failed for file %d: %s",
                    file_id,
                    exc,
                    exc_info=True,
                )
        trace.finish_span("index_meeting")

        # Mark file as ready (or summarizing when auto-summarize will run next)
        # and store content hash.
        trace.start_span("db_persist", "persist")

        # When auto-summarize is enabled the file stays in 'summarizing' status
        # until schedule_post_ready_summary advances it to 'ready'.  This keeps
        # the meeting in 'processing' until every file has a summary, ensuring
        # the meeting badge only turns green once all content is fully ready.
        post_index_status = "summarizing" if settings.MEETING_AUTO_SUMMARIZE_FILES else "ready"

        def _mark_file_ready():
            with get_write_connection() as conn:
                update_meeting_file_status(
                    conn,
                    file_id,
                    post_index_status,
                    transcript=text,
                    content_hash=new_hash,
                    error_message=None,
                )
                mark_chroma_indexed(
                    conn,
                    file_id=file_id,
                    meeting_id=meeting_id,
                    indexed_at=datetime.now(UTC).isoformat(),
                )
                update_meeting_file_artefact(
                    conn,
                    file_id,
                    structured_json=artefact.structured_json,
                    structured_kind=artefact.structured_kind,
                    metrics_json=json.dumps(artefact.metrics, ensure_ascii=False),
                    duration_seconds=_metric_float(artefact.metrics.get("duration_seconds")),
                    page_count=_metric_int(artefact.metrics.get("page_count")),
                    word_count=_metric_int(artefact.metrics.get("word_count")),
                    language=_metric_str(artefact.metrics.get("language")),
                )
                # Backward-compat path for existing readers.
                if artefact.structured_kind == "segments" and artefact.segments is not None:
                    save_segments_json(conn, file_id, json.dumps(artefact.segments))
                _update_meeting_status_from_files(conn, meeting_id)

        await asyncio.to_thread(_mark_file_ready)
        trace.finish_span("db_persist")

        # Schedule post-ready summary (generates per-file summary, then
        # advances the file from 'summarizing' to 'ready').
        if meeting_id is not None and settings.MEETING_AUTO_SUMMARIZE_FILES:
            from ._pipeline_common import schedule_post_ready_summary

            await schedule_post_ready_summary(file_id, meeting_id)

        logger.info("Meeting file %d processed successfully", file_id)

        # Auto-trigger meeting summary when auto-summarize is disabled but
        # force was requested (e.g. reprocess).
        if (
            meeting_id is not None
            and force_meeting_summary
            and not settings.MEETING_AUTO_SUMMARIZE_FILES
        ):
            _maybe_trigger_meeting_summary(meeting_id)

        await _ws_notify_progress(
            meeting_id,
            "processing",
            1.0,
            f"Completed {file_name}",
            user_id=meeting_user_id,
        )

    except Exception as e:
        logger.error("File %d processing failed: %s", file_id, e, exc_info=True)
        error_msg = f"{type(e).__name__}: {e}"[:500]

        def _mark_file_failed():
            with get_write_connection() as conn:
                update_meeting_file_status(conn, file_id, "error", error_message=error_msg)
                if meeting_id is not None:
                    _update_meeting_status_from_files(conn, meeting_id)

        await asyncio.to_thread(_mark_file_failed)

        if meeting_id is not None:
            fname = str(file_record.get("file_name", "")) if file_record else ""
            await _ws_notify_complete(meeting_id, "failed", fname, user_id=meeting_user_id)

    logger.info("ingest_trace %s", json.dumps(trace.to_dict()))
    return trace


async def _convert_pptx_to_pdf(
    *,
    file_id: int,
    meeting_id: int,
    file_path: Path,
    file_name: str,
) -> tuple[Path, str, str]:
    """Convert PPT/PPTX to PDF, replace the original file, update DB.

    Returns (new_pdf_path, "pdf", new_file_name).
    Falls back to the original file on conversion failure.
    """
    import shutil

    from ..parser.converters import (
        LibreOfficeMissingError,
        _convert_ppt_to_pdf,
        _convert_pptx_to_pdf,
    )

    suffix = file_path.suffix.lower()
    try:
        if suffix == ".ppt":
            converted = await asyncio.to_thread(_convert_ppt_to_pdf, file_path)
        else:
            converted = await asyncio.to_thread(_convert_pptx_to_pdf, file_path)
    except (LibreOfficeMissingError, OSError, subprocess.CalledProcessError):
        logger.warning(
            "PPTX→PDF conversion failed for file %d, continuing with original",
            file_id,
            exc_info=True,
        )
        return file_path, "ppt", file_name

    # Save converted PDF alongside the original
    pdf_name = file_path.stem + ".pdf"
    pdf_path = file_path.parent / pdf_name
    if pdf_path.exists() and pdf_path != file_path:
        from uuid import uuid4

        pdf_path = file_path.parent / f"{file_path.stem}_{uuid4().hex[:8]}.pdf"
        pdf_name = pdf_path.name

    shutil.move(str(converted), str(pdf_path))
    with contextlib.suppress(OSError):
        converted.parent.rmdir()

    # Remove original PPTX
    if file_path.exists():
        file_path.unlink()

    # Update DB records
    def _update_db() -> None:
        with get_write_connection() as conn:
            conn.execute(
                "UPDATE meeting_files "
                "SET file_path=?, file_type='pdf', file_name=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE id=?",
                (str(pdf_path), pdf_name, file_id),
            )
            # Also fix the meetings row for single-file meetings
            conn.execute(
                "UPDATE meetings "
                "SET file_type='pdf', file_name=?, file_path=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND file_type='ppt'",
                (pdf_name, str(pdf_path), meeting_id),
            )

    await asyncio.to_thread(_update_db)

    logger.info("Converted %s → %s for file %d", suffix, pdf_name, file_id)
    return pdf_path, "pdf", pdf_name


def _resolve_processor(file_type: str):
    if file_type in _AV_KINDS:
        return AVFileProcessor(transcriber=_transcribe_with_timestamps_compat)
    if file_type == "image":
        return ImageFileProcessor()
    if file_type in {"pdf", "ppt", "doc", "xls", "csv"}:
        return DocumentFileProcessor()
    return TextFileProcessor()


async def _transcribe_with_timestamps_compat(
    file_path: Path, provider: str, trace: object | None
) -> list[dict]:
    typed_trace = trace if isinstance(trace, TraceContext) else None
    return await transcribe_with_timestamps(file_path, provider=provider, trace=typed_trace)


from ._pipeline_summary import (  # noqa: E402
    _maybe_trigger_meeting_summary,
)
