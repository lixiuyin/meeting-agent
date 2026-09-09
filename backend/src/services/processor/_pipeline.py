"""Background task: process uploaded meeting file -> transcribe/parse -> index"""

import asyncio
import contextlib
import json
import logging
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from ...core.config import settings
from ...core.database import (
    get_meeting,
    get_meeting_file,
    get_write_connection,
    mark_native_index_building,
    mark_native_index_failed,
    mark_native_index_ready,
    mark_raganything_failed,
    mark_raganything_indexed,
    save_segments_json,
    update_meeting_file_artefact,
    update_meeting_file_raganything,
    update_meeting_file_status,
    update_meeting_status,
)
from ...core.index_manifest import index_config_fingerprint
from ...core.trace import TraceContext, write_ingest_trace
from ..files._kinds import timeline_kinds
from ..files._paths import resolve_upload_path
from ..rag import (
    index_meeting,
    index_meeting_pages,
    index_meeting_segments,
)
from ..rag._indexer_store import (
    NativeIndexManifest,
    atomic_file_index_replacement,
    inspect_native_index_generation,
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


def _replace_native_indexes(
    *,
    meeting_id: int,
    file_id: int,
    text: str,
    artefact,
    metadata: dict,
    trace: TraceContext,
) -> NativeIndexManifest:
    """Replace native indexes without making the previous generation unavailable."""
    with atomic_file_index_replacement(meeting_id, file_id) as generation:
        generation_metadata = {**metadata, "index_generation": generation}
        if _should_route_artefact_to_text_chunking(artefact):
            index_meeting(
                meeting_id=meeting_id,
                text=_build_text_route_payload(artefact),
                metadata=generation_metadata,
                trace=trace,
                strict_bm25=True,
            )
        elif artefact.segments is not None:
            index_meeting_segments(
                meeting_id=meeting_id,
                segments=artefact.segments,
                metadata=generation_metadata,
                trace=trace,
                strict_bm25=True,
            )
            if artefact.aux_segments:
                index_meeting_segments(
                    meeting_id=meeting_id,
                    segments=artefact.aux_segments,
                    metadata={**generation_metadata, "source_kind": "image"},
                    trace=trace,
                    strict_bm25=True,
                )
        elif artefact.parsed_doc is not None:
            index_meeting_pages(
                meeting_id=meeting_id,
                parsed=artefact.parsed_doc,
                metadata=generation_metadata,
                trace=trace,
                replace_existing=False,
                strict_bm25=True,
            )
        else:
            index_meeting(
                meeting_id=meeting_id,
                text=text,
                metadata=generation_metadata,
                trace=trace,
                strict_bm25=True,
            )
    return inspect_native_index_generation(
        meeting_id,
        file_id,
        generation,
        str(metadata["index_config_fingerprint"]),
    )


def _should_skip_unchanged_index(
    file_record: dict,
    *,
    new_hash: str,
    force_native_reindex: bool,
) -> bool:
    """Skip only an already-ready, verified-by-caller ordinary reprocess."""
    return bool(
        not force_native_reindex
        and file_record.get("status") == "ready"
        and file_record.get("content_hash")
        and file_record["content_hash"] == new_hash
    )


async def process_meeting_file(
    file_id: int,
    trace: TraceContext | None = None,
    *,
    force_meeting_summary: bool = False,
    force_native_reindex: bool = False,
    expected_source_revision: int | None = None,
) -> TraceContext:
    """Process a single meeting file: transcribe/parse -> index -> update status.

    This updates the meeting's overall status based on all files' completion.

    Args:
        file_id: the meeting file ID to process.
        trace: optional trace context for benchmarking; a fresh one is created if None.
        force_meeting_summary: bypass MEETING_AUTO_SUMMARIZE_FILES gate for meeting
            summary rebuild.  Used by reprocess to always rebuild the meeting summary
            even when auto-summarize is disabled.
        force_native_reindex: rebuild Chroma and BM25 even when the source hash
            is unchanged. Required for explicit reprocess and manifest repair jobs.
        expected_source_revision: discard a superseded durable job before it
            can publish retrieval data for an older material review state.

    Returns:
        The TraceContext used during processing.
    """
    from ...core.database import get_connection

    if trace is None:
        trace = TraceContext()

    meeting_id: int | None = None
    file_record = None
    terminal_status = "error"
    ready_status = "error"
    native_index_started = False
    native_manifest: NativeIndexManifest | None = None
    try:
        # Fetch file record
        trace.start_span("fetch_metadata", "metadata")

        def _fetch_file():
            with get_connection() as conn:
                return get_meeting_file(conn, file_id)

        file_record = await asyncio.to_thread(_fetch_file)
        if not file_record:
            trace.finish_span(
                "fetch_metadata",
                "error",
                error=LookupError(f"Meeting file {file_id} not found"),
            )
            logger.error("Meeting file %d not found", file_id)

            def _mark_missing_file_error():
                with get_write_connection() as conn:
                    update_meeting_file_status(
                        conn, file_id, "error", error_message="File record not found"
                    )

            await asyncio.to_thread(_mark_missing_file_error)
            return trace

        if expected_source_revision is not None and int(
            file_record.get("source_revision") or 1
        ) != int(expected_source_revision):
            trace.finish_span("fetch_metadata", "skipped")
            logger.info(
                "Skipping superseded file-processing job for file=%d expected_revision=%d",
                file_id,
                expected_source_revision,
            )
            return trace

        meeting_id = int(file_record["meeting_id"])
        file_path = Path(file_record["file_path"])

        # Fetch meeting date for metadata filtering. Done before the file
        # existence check below so failure notifications still carry user_id.
        def _fetch_meeting_metadata():
            with get_connection() as conn:
                m = get_meeting(conn, meeting_id)
            return (
                (m.get("meeting_date"), m.get("user_id"), m.get("title"))
                if m
                else (None, None, None)
            )

        meeting_date, meeting_user_id, meeting_title = await asyncio.to_thread(
            _fetch_meeting_metadata
        )
        trace.finish_span("fetch_metadata")

        file_path = await asyncio.to_thread(
            resolve_upload_path,
            file_path,
            expected_hash=file_record.get("content_hash"),
        )
        file_type = file_record["file_type"]
        file_name = file_record["file_name"]
        source_file_type = file_type
        source_file_name = file_name
        source_suffix = Path(file_name).suffix.lower().lstrip(".")
        new_hash = _file_content_hash(file_path)

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

        # ── Convert PPT/PPTX to a derived PDF for parsing ──────────────────
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
        except Exception as exc:
            trace.finish_span(extract_span_label, "error", error=exc)
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
        trace.start_span("validate_content", "validate")
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
            trace.finish_span(
                "validate_content",
                "error",
                error=ValueError(error_msg),
            )
            return trace
        trace.finish_span("validate_content")
        if text_len < _MIN_EXTRACTED_LENGTH and has_non_text_content:
            logger.info(
                "File %d: text too short (%d chars) but multimodal/table content exists; continue",
                file_id,
                text_len,
            )

        # Incremental index: skip only for already-ready files with unchanged bytes.
        # New uploads are created in "processing" status with a precomputed file hash.
        # We must not skip the initial indexing pass in that case.
        if _should_skip_unchanged_index(
            file_record,
            new_hash=new_hash,
            force_native_reindex=force_native_reindex,
        ):
            logger.info("File %d content unchanged (hash=%s), skipping re-index", file_id, new_hash)

            def _mark_file_ready_skip():
                with get_write_connection() as conn:
                    update_meeting_file_status(conn, file_id, "ready", error_message=None)
                    _update_meeting_status_from_files(conn, meeting_id)

            await asyncio.to_thread(_mark_file_ready_skip)
            trace.start_span("index_meeting", "index", skipped=True)
            trace.start_span("db_persist", "persist", skipped=True)
            terminal_status = "success"
            ready_status = "ready"
            return trace

        # Index into vector database — use structured indexer when available
        trace.start_span("index_meeting", "index")

        def _mark_index_building() -> None:
            with get_write_connection() as conn:
                mark_native_index_building(conn, file_id=file_id, meeting_id=meeting_id)

        await asyncio.to_thread(_mark_index_building)
        native_index_started = True
        from ..rag._contextual import infer_material_role

        source_content_changed = str(file_record.get("transcript") or "") != str(text or "") or str(
            file_record.get("content_hash") or ""
        ) != str(new_hash or "")
        indexed_source_revision = int(file_record.get("source_revision") or 1) + int(
            source_content_changed
        )
        base_meta = {
            "title": source_file_name,
            "meeting_title": meeting_title,
            "file_type": source_file_type,
            "file_id": file_id,
            "file_name": source_file_name,
            "material_role": file_record.get("material_role")
            or infer_material_role(source_file_name, source_file_type),
            "approval_status": file_record.get("approval_status") or "unreviewed",
            # update_meeting_file_status() increments the row after indexing
            # when parsed content changed, so publish the revision that will be
            # authoritative at the end of this successful processing pass.
            "file_source_revision": indexed_source_revision,
            "document_recorded_at": str(
                file_record.get("content_recorded_at") or file_record.get("created_at") or ""
            ),
            "meeting_date": int(meeting_date.replace("-", "")) if meeting_date else 0,
            "user_id": meeting_user_id or "default",
            "index_config_fingerprint": index_config_fingerprint(),
            "chunk_strategy_route": (
                "text" if _should_route_artefact_to_text_chunking(artefact) else "native"
            ),
            **_build_ingest_observability_metadata(artefact.metrics),
        }
        # Propagate original_format from parsed document (e.g. PPTX→PDF conversion)
        if artefact.parsed_doc and artefact.parsed_doc.metadata.get("original_format"):
            base_meta["original_format"] = artefact.parsed_doc.metadata["original_format"]
        elif source_suffix in {"ppt", "pptx"}:
            base_meta["original_format"] = source_suffix
        native_manifest = await asyncio.to_thread(
            _replace_native_indexes,
            meeting_id=meeting_id,
            file_id=file_id,
            text=text,
            artefact=artefact,
            metadata=base_meta,
            trace=trace,
        )

        # The semantic review may change while parsing or embedding is in
        # flight. The newly written generation carries the old revision and is
        # filtered from reads; stop this worker before it can mark that stale
        # generation ready or schedule extraction from it. The durable queue
        # promotes the coalesced successor with the latest revision.
        if expected_source_revision is not None:

            def _revision_still_current() -> bool:
                with get_connection() as conn:
                    current = get_meeting_file(conn, file_id)
                return bool(
                    current
                    and int(current.get("source_revision") or 1) == int(expected_source_revision)
                )

            if not await asyncio.to_thread(_revision_still_current):
                trace.finish_span("index_meeting", "skipped")
                logger.info(
                    "Discarding superseded index generation for file=%d expected_revision=%d",
                    file_id,
                    expected_source_revision,
                )
                terminal_status = "superseded"
                ready_status = "processing"
                return trace

        if settings.RAGANYTHING_ENABLED:
            doc_id = f"meeting_{meeting_id}_file_{file_id if file_id is not None else 'unknown'}"
            try:
                if file_type in {"pdf", "ppt", "doc", "xls", "csv", "image", "video"}:
                    await asyncio.to_thread(
                        index_file_with_raganything,
                        meeting_id=meeting_id,
                        file_id=file_id,
                        file_path=str(file_path),
                        metadata={
                            **base_meta,
                            "title": source_file_name,
                        },
                    )
                else:
                    await asyncio.to_thread(
                        index_with_raganything,
                        meeting_id=meeting_id,
                        file_id=file_id,
                        parsed=artefact.parsed_doc,
                        text=text if artefact.parsed_doc is None else None,
                        file_path=str(file_path),
                        metadata={
                            **base_meta,
                            "title": source_file_name,
                        },
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

        def _mark_file_ready() -> bool:
            with get_write_connection() as conn:
                if expected_source_revision is not None:
                    current = get_meeting_file(conn, file_id)
                    if not current or int(current.get("source_revision") or 1) != int(
                        expected_source_revision
                    ):
                        return False
                update_meeting_file_status(
                    conn,
                    file_id,
                    post_index_status,
                    transcript=text,
                    content_hash=new_hash,
                    error_message=None,
                )
                mark_native_index_ready(
                    conn,
                    file_id=file_id,
                    meeting_id=meeting_id,
                    indexed_at=datetime.now(UTC).isoformat(),
                    generation=native_manifest.generation if native_manifest else None,
                    config_fingerprint=(
                        native_manifest.config_fingerprint if native_manifest else None
                    ),
                    chroma_chunk_count=(
                        native_manifest.chroma_chunk_count if native_manifest else None
                    ),
                    bm25_chunk_count=(
                        native_manifest.bm25_chunk_count if native_manifest else None
                    ),
                    manifest_checksum=native_manifest.checksum if native_manifest else None,
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
                return True

        committed_current_revision = await asyncio.to_thread(_mark_file_ready)
        if not committed_current_revision:
            trace.finish_span("db_persist", "skipped")
            logger.info(
                "Skipping stale index readiness commit for file=%d expected_revision=%d",
                file_id,
                expected_source_revision,
            )
            terminal_status = "superseded"
            ready_status = "processing"
            return trace
        trace.finish_span("db_persist")

        # Mine durable project facts from the complete source independently of
        # what a user later happens to ask in chat. A material review changes
        # the source fence even when bytes match: replace stale extraction with
        # the reviewed revision. Ordinary manifest repair still skips extraction.
        reviewed_source = force_native_reindex and expected_source_revision is not None
        if (
            meeting_id is not None
            and meeting_user_id
            and (source_content_changed or reviewed_source)
        ):
            from ._memory_extraction import schedule_file_memory_extraction

            await schedule_file_memory_extraction(
                user_id=str(meeting_user_id),
                meeting_id=meeting_id,
                file_id=file_id,
                file_name=source_file_name,
                text=text,
            )

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
            await _maybe_trigger_meeting_summary(meeting_id)

        await _ws_notify_progress(
            meeting_id,
            "processing",
            1.0,
            f"Completed {file_name}",
            user_id=meeting_user_id,
        )
        terminal_status = "success"
        ready_status = post_index_status

    except Exception as e:
        trace.finish_latest_open_span("error", error=e)
        logger.error("File %d processing failed: %s", file_id, e, exc_info=True)
        error_msg = f"{type(e).__name__}: {e}"[:500]

        def _mark_file_failed():
            with get_write_connection() as conn:
                if native_index_started and meeting_id is not None:
                    mark_native_index_failed(
                        conn,
                        file_id=file_id,
                        meeting_id=meeting_id,
                        error=error_msg,
                    )
                update_meeting_file_status(conn, file_id, "error", error_message=error_msg)
                if meeting_id is not None:
                    _update_meeting_status_from_files(conn, meeting_id)

        await asyncio.to_thread(_mark_file_failed)

        if meeting_id is not None:
            fname = str(file_record.get("file_name", "")) if file_record else ""
            await _ws_notify_complete(meeting_id, "failed", fname, user_id=meeting_user_id)
    finally:
        write_ingest_trace(
            trace,
            file_id=file_id,
            meeting_id=meeting_id,
            terminal_status=terminal_status,
            ready_status=ready_status,
        )
        logger.info("ingest_trace %s", json.dumps(trace.to_dict()))
    return trace


async def _convert_pptx_to_pdf(
    *,
    file_id: int,
    meeting_id: int,
    file_path: Path,
    file_name: str,
) -> tuple[Path, str, str]:
    """Convert PPT/PPTX to a managed derivative without replacing the upload.

    Returns (derived_pdf_path, "pdf", original_file_name).
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

    # Keep generated files under the existing managed-assets tree so file and
    # meeting deletion already remove them. The original upload remains the
    # downloadable source of truth and its hash remains stable across reprocess.
    derived_dir = settings.UPLOAD_DIR / "meeting_assets" / str(meeting_id) / str(file_id)
    derived_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = derived_dir / "converted.pdf"
    pdf_path.unlink(missing_ok=True)
    shutil.move(str(converted), str(pdf_path))
    with contextlib.suppress(OSError):
        converted.parent.rmdir()

    logger.info("Converted %s → managed derivative for file %d", suffix, file_id)
    return pdf_path, "pdf", file_name


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
