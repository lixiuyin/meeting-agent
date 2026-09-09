"""AMI corpus ingestion helpers for benchmark harness."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

from src.core.database import (
    create_meeting,
    create_meeting_file,
    get_write_connection,
    update_meeting_file_status,
    update_meeting_status,
)
from src.core.trace import TraceContext
from src.services.processor import process_meeting_file
from src.services.rag import delete_meeting_chunks, index_meeting, index_meeting_segments

DATASET_DIR = Path(__file__).parent.parent / "tests" / "fixtures" / "Dataset" / "amicorpus"
PRETRANSCRIBED_DIR = (
    Path(__file__).parent.parent / "tests" / "fixtures" / "benchmark" / "amicorpus_transcripts"
)


def _audio_path(meeting_name: str) -> Path:
    return DATASET_DIR / meeting_name / "audio" / f"{meeting_name}.Mix-Headset.wav"


def _pretranscribed_path(meeting_name: str) -> Path:
    return PRETRANSCRIBED_DIR / f"{meeting_name}_segments.json"


async def _index_from_pretranscribed(
    meeting_id: int,
    file_id: int,
    file_name: str,
    file_path: Path,
    segments: list[dict],
    trace: TraceContext | None = None,
) -> None:
    """Index a meeting file using pre-transcribed segments, skipping ASR."""
    from src.core.config import settings

    # Build artefact-like text
    lines: list[str] = []
    for seg in segments:
        speaker = seg.get("speaker")
        text_part = seg.get("text", "").strip()
        if speaker:
            lines.append(f"{speaker}: {text_part}")
        elif text_part:
            lines.append(text_part)
    text = "\n".join(lines)

    duration = float(segments[-1].get("end", 0.0)) if segments else 0.0
    speakers = {s.get("speaker") for s in segments if s.get("speaker")}
    metrics = {
        "duration_seconds": duration,
        "word_count": len(text.split()),
        "speaker_count": len(speakers),
    }

    base_meta = {
        "title": file_name,
        "file_type": "audio",
        "file_id": file_id,
        "file_name": file_name,
        "meeting_date": "2026-01-15",
        "chunk_strategy_route": (
            "text" if settings.NON_TEXT_CHUNKING_STRATEGY == "text" else "native"
        ),
        **metrics,
    }

    # Clean old index for this file
    await asyncio.to_thread(delete_meeting_chunks, meeting_id, file_id=file_id)

    if settings.NON_TEXT_CHUNKING_STRATEGY == "text":
        # Build enriched text with timestamps
        enriched_lines: list[str] = []
        for seg in segments:
            text_part = (seg.get("text") or "").strip()
            if not text_part:
                continue
            start = float(seg.get("start", 0.0) or 0.0)
            ts = f"{int(start // 60):02d}:{int(start % 60):02d}"
            speaker = seg.get("speaker")
            if speaker and speaker != "image":
                enriched_lines.append(f"[{ts}] {speaker}: {text_part}")
            else:
                enriched_lines.append(f"[{ts}] {text_part}")
        route_text = "\n".join(enriched_lines) if enriched_lines else text

        await asyncio.to_thread(
            index_meeting,
            meeting_id=meeting_id,
            text=route_text,
            metadata=base_meta,
            trace=trace,
        )
    else:
        await asyncio.to_thread(
            index_meeting_segments,
            meeting_id=meeting_id,
            segments=segments,
            metadata=base_meta,
            trace=trace,
        )

    def _mark_ready():
        with get_write_connection() as conn:
            update_meeting_file_status(conn, file_id, "ready", error_message=None)
            # Update meeting status based on all files
            from src.core.database import list_meeting_files

            files = list_meeting_files(conn, meeting_id)
            if all(f.get("status") == "ready" for f in files):
                # Must transition uploading → processing → ready
                update_meeting_status(conn, meeting_id, "processing")
                update_meeting_status(conn, meeting_id, "ready")

    await asyncio.to_thread(_mark_ready)

    # DIAGNOSTIC: verify BM25 rows exist after indexing
    def _diag_bm25():
        with get_write_connection() as conn:
            count_idx = conn.execute(
                "SELECT COUNT(*) AS c FROM bm25_index WHERE meeting_id = ?",
                (meeting_id,),
            ).fetchone()["c"]
            count_fts = conn.execute(
                "SELECT COUNT(*) AS c FROM bm25_chunks WHERE meeting_id = ?",
                (meeting_id,),
            ).fetchone()["c"]
            print(
                f"[DIAG] meeting_id={meeting_id} file_id={file_id} "
                f"bm25_index={count_idx} bm25_chunks={count_fts}"
            )

    await asyncio.to_thread(_diag_bm25)


async def ingest_amicorpus_meeting(meeting_name: str) -> tuple[int, int]:
    """Create a meeting, copy the .wav, and run the ASR pipeline (or use pre-transcribed)."""
    from src.core.config import settings

    audio_src = _audio_path(meeting_name)
    if not audio_src.exists():
        raise FileNotFoundError(f"AMI audio not found: {audio_src}")

    with get_write_connection() as conn:
        meeting_id = create_meeting(
            conn,
            title=meeting_name,
            description="AMI benchmark fixture",
            meeting_date="2026-01-15",
            user_id="benchmark",
        )

    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest_path = upload_dir / audio_src.name
    shutil.copy2(audio_src, dest_path)

    with get_write_connection() as conn:
        file_id = create_meeting_file(
            conn,
            meeting_id=meeting_id,
            file_type="audio",
            file_name=audio_src.name,
            file_path=str(dest_path),
            user_id="benchmark",
        )

    pre_path = _pretranscribed_path(meeting_name)
    if pre_path.exists():
        segments = json.loads(pre_path.read_text(encoding="utf-8"))
        trace = TraceContext()
        trace.start_span("fetch_metadata", "metadata")
        trace.finish_span("fetch_metadata")
        trace.start_span("transcribe", "extract")
        trace.finish_span("transcribe")
        trace.start_span("index_meeting", "index")
        await _index_from_pretranscribed(
            meeting_id, file_id, audio_src.name, dest_path, segments, trace=trace
        )
        trace.finish_span("index_meeting")
        return meeting_id, file_id

    trace = await process_meeting_file(file_id)
    return meeting_id, file_id


async def ingest_all_amicorpus() -> dict[str, tuple[int, int]]:
    """Ingest all 4 AMI meetings and return {name: (meeting_id, file_id)}."""
    meetings = ["ES2015a", "ES2015b", "ES2015c", "ES2015d"]
    results: dict[str, tuple[int, int]] = {}
    for name in meetings:
        mid, fid = await ingest_amicorpus_meeting(name)
        results[name] = (mid, fid)
    return results


async def pretranscribe_all() -> None:
    """Run ASR once for all 4 AMI meetings and save segments to JSON.

    Usage:
        uv run python -m scripts._bench_amicorpus
    """
    from src.services.transcriber import transcribe_with_timestamps

    PRETRANSCRIBED_DIR.mkdir(parents=True, exist_ok=True)
    for name in ["ES2015a", "ES2015b", "ES2015c", "ES2015d"]:
        audio_path = _audio_path(name)
        out_path = _pretranscribed_path(name)
        if out_path.exists():
            print(f"[skip] {name} already pre-transcribed")
            continue
        print(f"[ASR] Transcribing {name} ...")
        segments = await transcribe_with_timestamps(audio_path, provider="assemblyai")
        out_path.write_text(json.dumps(segments, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[done] Saved {len(segments)} segments to {out_path}")


if __name__ == "__main__":
    asyncio.run(pretranscribe_all())
