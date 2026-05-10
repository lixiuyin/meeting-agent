"""Speaker identification endpoints — list, rename, and play sample audio."""

import asyncio
import json
import logging
from collections import OrderedDict
from pathlib import Path

from fastapi import BackgroundTasks, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse

from ....api.middleware import limiter
from ....api.routers.file_download import _verify_file_access
from ....core import database as db
from ....core.audit import audit_log
from ....core.config import settings
from ....core.constants import UPLOAD_DIR
from ....core.security import _derive_user_id_from_api_key, is_dev_user, verify_api_key
from ....models.schemas import (
    SpeakerInfo,
    SpeakersResponse,
    TranscriptSegment,
    UpdateSpeakersRequest,
    UpdateSpeakersResponse,
)
from ....services.asr._audio_clip import extract_audio_clip
from ._common import _ownership_filter, router

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_meeting_date_int(date_str: str) -> int:
    """Parse 'YYYY-MM-DD' to int YYYYMMDD, returning 0 on invalid input."""
    if not date_str:
        return 0
    try:
        from datetime import date

        d = date.fromisoformat(date_str)
        return d.year * 10000 + d.month * 100 + d.day
    except (ValueError, TypeError):
        return 0


def _format_timestamp(ms: int) -> str:
    total_seconds = ms // 1000
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _resolve_file(meeting_id: int, file_id: int, *, user_id: str | None = None) -> dict:
    """Fetch and validate meeting + file record. Returns file dict."""
    with db.get_connection() as conn:
        m = db.get_meeting(conn, meeting_id, user_id=user_id)
        if not m:
            raise HTTPException(404, "Meeting not found")
        f = db.get_meeting_file(conn, file_id, user_id=user_id)
        if not f or f["meeting_id"] != meeting_id:
            raise HTTPException(404, "File not found")
        if f["file_type"] not in ("video", "audio"):
            raise HTTPException(
                400, "Speaker identification is only available for audio/video files"
            )
        if f["status"] not in ("ready", "summarizing"):
            raise HTTPException(400, "File is not ready")
    return f


async def _load_segments(file_record: dict) -> list[dict]:
    """Load segments from DB cache, or re-transcribe via async path."""
    file_id = file_record["id"]
    with db.get_connection() as conn:
        cached = db.get_segments_json(conn, file_id)
    if cached:
        return json.loads(cached)

    # Fallback: re-transcribe (expensive, for legacy data)
    file_path = Path(file_record["file_path"])
    if not file_path.exists():
        file_path = UPLOAD_DIR / file_path.name
    if not file_path.exists():
        raise HTTPException(404, "Source file not found")

    from ....services.transcriber import transcribe_with_timestamps

    segments = await transcribe_with_timestamps(file_path, provider=settings.ASR_PROVIDER)

    # Cache for future use
    await asyncio.to_thread(_cache_segments_sync, file_id, segments)
    return segments


def _cache_segments_sync(file_id: int, segments: list[dict]) -> None:
    with db.get_write_connection() as conn:
        db.save_segments_json(conn, file_id, json.dumps(segments))


def _apply_speaker_names(segments: list[dict], name_map: dict[str, str]) -> list[dict]:
    """Return new segment list with speaker codes replaced by names."""
    return [
        {**s, "speaker": name_map.get(s.get("speaker", ""), s.get("speaker"))} for s in segments
    ]


def _build_transcript_text(segments: list[dict]) -> str:
    """Build human-readable transcript from segments with timestamps."""
    lines = []
    for seg in segments:
        speaker = seg.get("speaker")
        text_part = seg.get("text", "").strip()
        if not text_part:
            continue
        start_ms = int(seg.get("start", 0) * 1000)
        timestamp = _format_timestamp(start_ms)
        if speaker:
            lines.append(f"[{timestamp}] {speaker}: {text_part}")
        else:
            lines.append(f"[{timestamp}] {text_part}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Background helpers
# ---------------------------------------------------------------------------


async def _regenerate_summaries_after_rename(file_id: int, meeting_id: int) -> None:
    """Background task: reset summary state and re-run the post-ready pipeline."""
    from ....services.chain._meeting_summary_lifecycle import invalidate_meeting_summary
    from ....services.processor._pipeline_common import (
        _update_meeting_status_from_files,
        schedule_post_ready_summary,
    )
    from ....services.rag._summary_vectorstore import delete_file_summary
    from ....services.websocket import websocket_manager

    try:
        # Reset summary_status FIRST — if this fails, file status is untouched.
        await asyncio.to_thread(db.update_file_summary_status, file_id, "pending")

        # Delete stale summary vector before regen so it doesn't linger.
        try:
            await asyncio.to_thread(delete_file_summary, file_id)
        except Exception:
            logger.debug("Stale summary vector delete failed for file %d", file_id, exc_info=True)

        def _reset():
            with db.get_write_connection() as conn:
                db.update_meeting_file_status(conn, file_id, "summarizing")
                _update_meeting_status_from_files(conn, meeting_id)

        await asyncio.to_thread(_reset)
        # force=True bypasses the 30s debounce: speaker rename always
        # invalidates because the transcript text materially changed.
        await asyncio.to_thread(invalidate_meeting_summary, meeting_id, force=True)
        await schedule_post_ready_summary(file_id, meeting_id)
    except Exception:
        logger.error(
            "Failed to regenerate summaries after speaker rename for file %d",
            file_id,
            exc_info=True,
        )

        # Roll back to ready + summary_status=failed so the file isn't stuck
        def _rollback():
            with db.get_write_connection() as conn:
                db.update_meeting_file_status(conn, file_id, "ready")
                _update_meeting_status_from_files(conn, meeting_id)
            db.update_file_summary_status(file_id, "failed")

        await asyncio.to_thread(_rollback)
        await websocket_manager.notify_meeting_update(
            meeting_id, {"event": "summary_failed", "file_id": file_id}
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/{meeting_id}/files/{file_id}/speakers", response_model=SpeakersResponse)
async def get_speakers(
    meeting_id: int,
    file_id: int,
    principal: dict = Depends(verify_api_key),
):
    """List all unique speakers for a file with sample utterances."""
    user_id = _ownership_filter(principal)
    file_record = await asyncio.to_thread(_resolve_file, meeting_id, file_id, user_id=user_id)
    segments = await _load_segments(file_record)

    # Load existing mappings
    def _load_mappings():
        with db.get_connection() as conn:
            return db.list_speaker_mappings(conn, file_id)

    mappings_list = await asyncio.to_thread(_load_mappings)
    name_map = {m["speaker_code"]: m["speaker_name"] for m in mappings_list}

    # Group by speaker code
    speaker_segments: OrderedDict[str, list[dict]] = OrderedDict()
    for seg in segments:
        code = seg.get("speaker")
        if code:
            speaker_segments.setdefault(code, []).append(seg)

    speakers: list[SpeakerInfo] = []
    for code, segs in speaker_segments.items():
        # Find first non-empty utterance as sample
        sample_seg = None
        for s in segs:
            if s.get("text", "").strip():
                sample_seg = s
                break

        speakers.append(
            SpeakerInfo(
                speaker_code=code,
                speaker_name=name_map.get(code),
                utterance_count=len(segs),
                sample=TranscriptSegment(**sample_seg) if sample_seg else None,
            )
        )

    return SpeakersResponse(file_id=file_id, speakers=speakers)


@router.put("/{meeting_id}/files/{file_id}/speakers", response_model=UpdateSpeakersResponse)
@limiter.limit("3/minute")
async def update_speaker_names(
    request: Request,
    meeting_id: int,
    file_id: int,
    body: UpdateSpeakersRequest,
    background_tasks: BackgroundTasks,
    principal: dict = Depends(verify_api_key),
):
    """Save speaker mappings, regenerate transcript, re-index vectors, and regenerate summary."""
    user_id = _ownership_filter(principal)
    file_record = await asyncio.to_thread(_resolve_file, meeting_id, file_id, user_id=user_id)
    segments = await _load_segments(file_record)

    mappings = [(m.speaker_code, m.speaker_name) for m in body.mappings]
    # QR-1: Validate speaker names against a whitelist to prevent prompt injection.
    import re as _re

    _SPEAKER_NAME_RE = _re.compile(
        r"^[A-Za-z0-9一-鿿぀-ゟ゠-ヿ가-힯\s\-'.]{1,80}$"  # noqa: RUF001
    )
    for _code, _name in mappings:
        if not _SPEAKER_NAME_RE.match(_name):
            raise HTTPException(
                400,
                f"Invalid speaker name: {_name!r}. "
                "Use letters, numbers, spaces, hyphens, apostrophes.",
            )
    name_map = dict(mappings)

    # 1. Persist mappings
    def _save_mappings():
        with db.get_write_connection() as conn:
            db.bulk_upsert_speaker_mappings(conn, file_id, meeting_id, mappings)

    await asyncio.to_thread(_save_mappings)

    # 2. Apply name substitution to segments
    updated_segments = _apply_speaker_names(segments, name_map)

    # 3. Regenerate transcript text
    new_transcript = _build_transcript_text(updated_segments)

    # 4. Update transcript in DB (synchronous — must complete before
    #    the HTTP response so the saved transcript is correct).
    #    Set status to "summarizing" immediately so the client sees
    #    "rebuilding" state instead of a transient "ready" that would
    #    flip back to "summarizing" when the background task runs.
    def _update_db_transcript():
        from ....services.processor._pipeline_common import (
            _update_meeting_status_from_files,
        )

        with db.get_write_connection() as conn:
            # Update file transcript + mark as summarizing (H-11: avoid
            # exposing a transient "ready" state before reindex/summary).
            db.update_meeting_file_status(conn, file_id, "summarizing", transcript=new_transcript)
            # Persist renamed segments so downstream consumers (per-file
            # summary speaker timeline, future re-renders) see the new
            # names instead of mixing old codes with the renamed transcript.
            db.save_segments_json(conn, file_id, json.dumps(updated_segments))
            conn.execute(
                "UPDATE meeting_files SET summary_status='pending', "
                "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (file_id,),
            )
            # Recompute meeting status so it reflects the file's new
            # 'summarizing' state. Without this, a previously-Ready meeting
            # keeps showing "Ready" while one of its files is summarizing.
            _update_meeting_status_from_files(conn, meeting_id)
            # Update meeting-level combined transcript
            combined = db.get_meeting_transcripts(conn, meeting_id)
            conn.execute(
                "UPDATE meetings SET transcript=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (combined, meeting_id),
            )

    await asyncio.to_thread(_update_db_transcript)

    # Invalidate query-rewrite cache so that stale entries keyed on old speaker
    # names are not reused after the rename (H-16).
    from ....services.rag._query import _clear_rewrite_cache

    _clear_rewrite_cache()

    # 5. Re-index vectors using a shadow collection for zero-downtime swap (H-12).
    #    Instead of delete-then-reindex (which exposes a gap where RAG reads see
    #    zero results for this file), we:
    #      a) create a shadow collection,
    #      b) copy ALL live vectors into it,
    #      c) delete+reindex just this file's chunks in the shadow,
    #      d) atomically swap shadow → live.
    #    Concurrent RAG reads always see a complete, consistent index.
    async def _reindex_vectors_bg():
        import uuid

        from ....services.embedder import get_embeddings
        from ....services.rag import index_meeting_segments
        from ....services.rag._bm25_maintenance import rebuild_bm25_from_chroma
        from ....services.rag._indexer import _acquire_file_reindex_lock
        from ....services.rag._vectorstore import reset_vectorstore

        shadow_name = f"meetings_shadow_{uuid.uuid4().hex[:8]}"
        chroma_client = None

        try:
            import chromadb
            import chromadb.errors as _chromadb_errors
            from langchain_chroma import Chroma

            chroma_client = chromadb.PersistentClient(
                path=str(settings.VECTOR_DB_DIR),
            )

            def _shadow_reindex():
                reindex_lock = _acquire_file_reindex_lock(meeting_id, file_id)
                with reindex_lock:
                    embeddings = get_embeddings()
                    client = chroma_client
                    live_collection = client.get_collection("meetings")

                    # a) Create shadow collection
                    client.get_or_create_collection(
                        name=shadow_name,
                        metadata={"embedding_dimension": settings.EMBEDDING_DIMENSION},
                    )
                    shadow_vs = Chroma(
                        client=client,
                        collection_name=shadow_name,
                        embedding_function=embeddings,
                    )

                    # b) Copy all live vectors into shadow.
                    #
                    # Chroma's rust backend raises ``InternalError: Nothing
                    # found on disk`` from the HNSW segment reader when the
                    # live collection has no persisted vectors (e.g. all
                    # rows previously deleted, or never populated). Treat
                    # that as an empty source and let the shadow start
                    # blank — the new segments alone become the live state
                    # after swap, which is the correct outcome.
                    try:
                        all_data = live_collection.get(
                            include=["embeddings", "documents", "metadatas"],
                        )
                        ids = all_data.get("ids") or []
                    except _chromadb_errors.InternalError as exc:
                        msg = str(exc).lower()
                        if "nothing found on disk" in msg or "hnsw" in msg:
                            logger.info(
                                "Live 'meetings' collection has no persisted "
                                "vectors; shadow starts empty for file %d",
                                file_id,
                            )
                            ids = []
                            all_data = {}
                        else:
                            raise
                    if ids:
                        embeddings_data = all_data.get("embeddings")
                        documents = all_data.get("documents")
                        metadatas = all_data.get("metadatas")
                        shadow_vs._collection.add(
                            ids=ids,
                            embeddings=embeddings_data if embeddings_data is not None else [],
                            documents=documents if documents is not None else [],
                            metadatas=metadatas if metadatas is not None else [],
                        )
                        logger.debug(
                            "Copied %d vectors to shadow '%s'",
                            len(ids),
                            shadow_name,
                        )

                    # c) Delete this file's old chunks from shadow, then index new ones
                    file_name = file_record.get("file_name", "")
                    with db.get_connection() as conn:
                        meeting = db.get_meeting(conn, meeting_id, user_id=user_id)
                    meeting_date = meeting.get("meeting_date", "") if meeting else ""
                    base_meta = {
                        "title": file_name,
                        "file_type": file_record["file_type"],
                        "file_id": file_id,
                        "file_name": file_name,
                        "meeting_date": _parse_meeting_date_int(meeting_date),
                    }

                    # Delete old chunks for this file from shadow
                    from ....services.rag._indexer_store import (
                        _remove_from_bm25,
                        _remove_summary_vectors,
                    )

                    shadow_vs._collection.delete(
                        where={
                            "$and": [
                                {"meeting_id": {"$eq": meeting_id}},
                                {"file_id": {"$eq": file_id}},
                            ]
                        },
                    )
                    _remove_from_bm25(meeting_id, file_id=file_id)
                    _remove_summary_vectors(meeting_id, file_id=file_id)

                    # Index new segments into shadow (skip BM25 — rebuilt after swap)
                    index_meeting_segments(
                        meeting_id=meeting_id,
                        segments=updated_segments,
                        metadata=base_meta,
                        target_vs=shadow_vs,
                        skip_bm25=True,
                    )

                    # d) Atomic swap: rename live→old, shadow→live, delete old.
                    #     Uses collection.modify(name=...) (ChromaDB ≥1.1.0) instead
                    #     of the non-existent client.rename_collection().
                    old_name = f"meetings_old_{uuid.uuid4().hex[:8]}"
                    live_collection.modify(name=old_name)
                    try:
                        shadow_vs._collection.modify(name="meetings")
                    except Exception:
                        # Rollback: restore original collection name
                        client.get_collection(old_name).modify(name="meetings")
                        raise
                    client.delete_collection(old_name)
                    reset_vectorstore()
                    logger.info(
                        "Shadow swap complete for file %d: '%s' → 'meetings'",
                        file_id,
                        shadow_name,
                    )

                    # e) Rebuild BM25 from the new live collection
                    if settings.HYBRID_SEARCH_ENABLED:
                        rebuild_bm25_from_chroma(force=True)

            await asyncio.to_thread(_shadow_reindex)

            if settings.RAGANYTHING_ENABLED:
                from ....services.rag._raganything import index_with_raganything

                index_with_raganything(
                    meeting_id=meeting_id,
                    file_id=file_id,
                    text=new_transcript,
                    file_path=str(file_record.get("file_path", "")),
                    metadata={
                        "title": file_record.get("file_name", ""),
                        "file_type": file_record["file_type"],
                    },
                )

        except Exception:
            # Cleanup: drop shadow on any failure; live collection is untouched
            if chroma_client is not None:
                try:
                    chroma_client.delete_collection(shadow_name)
                except Exception:
                    logger.debug(
                        "Failed to cleanup shadow '%s'",
                        shadow_name,
                        exc_info=True,
                    )
            logger.warning(
                "Shadow reindex failed for file %d; live collection unchanged",
                file_id,
                exc_info=True,
            )
            # Fall back to in-place reindex so the rename doesn't silently fail
            from ....services.rag import delete_meeting_chunks

            def _fallback_reindex():
                reindex_lock = _acquire_file_reindex_lock(meeting_id, file_id)
                with reindex_lock:
                    delete_meeting_chunks(meeting_id, file_id=file_id)
                    file_name = file_record.get("file_name", "")
                    with db.get_connection() as conn:
                        meeting = db.get_meeting(conn, meeting_id, user_id=user_id)
                    meeting_date = meeting.get("meeting_date", "") if meeting else ""
                    base_meta = {
                        "title": file_name,
                        "file_type": file_record["file_type"],
                        "file_id": file_id,
                        "file_name": file_name,
                        "meeting_date": _parse_meeting_date_int(meeting_date),
                    }
                    index_meeting_segments(
                        meeting_id=meeting_id,
                        segments=updated_segments,
                        metadata=base_meta,
                    )

            await asyncio.to_thread(_fallback_reindex)

    background_tasks.add_task(_reindex_vectors_bg)

    # 6. Regenerate summaries in the background (LLM calls are slow and
    #      should not block the HTTP response).
    background_tasks.add_task(_regenerate_summaries_after_rename, file_id, meeting_id)

    # 7. Build response
    speaker_info: list[SpeakerInfo] = []
    seen_codes: set[str] = set()
    for seg in segments:
        code = seg.get("speaker")
        if code and code not in seen_codes:
            seen_codes.add(code)
            count = sum(1 for s in segments if s.get("speaker") == code)
            sample_seg = next(
                (s for s in segments if s.get("speaker") == code and s.get("text", "").strip()),
                None,
            )
            speaker_info.append(
                SpeakerInfo(
                    speaker_code=code,
                    speaker_name=name_map.get(code),
                    utterance_count=count,
                    sample=TranscriptSegment(**sample_seg) if sample_seg else None,
                )
            )

    audit_log(
        "update",
        "meeting_speakers",
        f"{meeting_id}/{file_id}",
        detail=f"renamed {len(mappings)} speaker(s)",
    )

    return UpdateSpeakersResponse(
        file_id=file_id,
        mappings=speaker_info,
        message=f"Updated {len(mappings)} speaker name(s)",
    )


@router.get("/{meeting_id}/files/{file_id}/speakers/{speaker_code}/audio")
async def get_speaker_audio(
    meeting_id: int,
    file_id: int,
    speaker_code: str,
    token: str | None = Query(None, description="Short-lived file download token"),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
):
    """Stream a short audio clip of a speaker's first utterance.

    Authentication: Either X-API-Key header or ?token= query parameter.
    """
    configured_key = settings.API_KEY.get_secret_value()
    user_id = "default"
    if configured_key and x_api_key:
        user_id = _derive_user_id_from_api_key(x_api_key)

    _verify_file_access(x_api_key, token, meeting_id=meeting_id, file_id=file_id, user_id=user_id)
    ownership_filter = user_id if not is_dev_user(user_id) else None
    file_record = await asyncio.to_thread(
        _resolve_file, meeting_id, file_id, user_id=ownership_filter
    )
    segments = await _load_segments(file_record)

    # Find first segment for this speaker
    sample = None
    for seg in segments:
        if seg.get("speaker") == speaker_code and seg.get("text", "").strip():
            sample = seg
            break
    if not sample:
        raise HTTPException(404, "No utterance found for the specified speaker")

    source_path = Path(file_record["file_path"])
    if not source_path.exists():
        source_path = UPLOAD_DIR / source_path.name
    if not source_path.exists():
        raise HTTPException(404, "Source file not found")

    # Extract audio clip
    clip_path = await asyncio.to_thread(
        extract_audio_clip,
        source_path,
        sample["start"],
        sample["end"],
    )

    return FileResponse(
        path=str(clip_path),
        media_type="audio/wav",
        filename=f"speaker_{speaker_code}.wav",
    )
