"""Fixture ingestion helpers for benchmark harness."""

from __future__ import annotations

import shutil
from pathlib import Path

from src.core.database import (
    create_meeting,
    create_meeting_file,
    get_write_connection,
)
from src.core.trace import TraceContext
from src.services.processor import process_meeting_file

FIXTURE_DIR = Path(__file__).parent.parent / "tests" / "fixtures" / "benchmark"


def _ensure_meeting(
    conn,
    title: str,
    meeting_date: str,
    *,
    user_id: str = "benchmark",
) -> int:
    """Create a meeting and return its ID."""
    return create_meeting(
        conn,
        title=title,
        description="Benchmark fixture",
        meeting_date=meeting_date,
        user_id=user_id,
    )


async def _ingest_fixture_file(
    fixture_name: str,
    meeting_id: int | None = None,
    trace: TraceContext | None = None,
    *,
    user_id: str = "benchmark",
) -> tuple[int, TraceContext]:
    """Copy a fixture file into the upload dir and process it.

    Returns:
        (file_id, trace_context)
    """
    from src.core.config import settings
    from src.core.database import get_write_connection

    src_path = FIXTURE_DIR / fixture_name
    if not src_path.exists():
        raise FileNotFoundError(f"Fixture not found: {src_path}")

    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest_path = upload_dir / fixture_name
    shutil.copy2(src_path, dest_path)

    ext = src_path.suffix.lower()
    if ext in {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v", ".3gp"}:
        file_type = "video"
    elif ext in {".mp3", ".wav", ".aac", ".flac", ".m4a", ".ogg", ".wma", ".opus"}:
        file_type = "audio"
    elif ext == ".pdf":
        file_type = "pdf"
    elif ext in {".ppt", ".pptx"}:
        file_type = "ppt"
    elif ext in {".doc", ".docx"}:
        file_type = "doc"
    elif ext in {".xls", ".xlsx"}:
        file_type = "xls"
    elif ext == ".csv":
        file_type = "csv"
    elif ext in {".txt", ".md", ".markdown", ".html", ".htm", ".json", ".xml", ".rtf"}:
        file_type = "txt"
    elif ext in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}:
        file_type = "image"
    else:
        file_type = "document"

    with get_write_connection() as conn:
        mid = meeting_id
        if mid is None:
            mid = _ensure_meeting(
                conn,
                f"Benchmark {fixture_name}",
                "2026-01-15",
                user_id=user_id,
            )
        file_id = create_meeting_file(
            conn,
            meeting_id=mid,
            file_name=fixture_name,
            file_path=str(dest_path),
            file_type=file_type,
            user_id=user_id,
        )

    if trace is None:
        trace = TraceContext()

    trace_out = await process_meeting_file(file_id, trace=trace)
    return file_id, trace_out


async def ingest_fixtures(
    fixture_names: list[str],
    *,
    user_id: str = "benchmark",
    meeting_title: str = "Benchmark Fixtures",
) -> dict[str, tuple[int, int]]:
    """Ingest the requested benchmark fixtures and return their IDs.

    Returns:
        Dict mapping fixture_name -> (meeting_id, file_id)
    """
    results: dict[str, tuple[int, int]] = {}
    allowed = {"sample.pdf", "scanned.pdf", "sample.pptx"}
    requested = list(dict.fromkeys(fixture_names))
    unknown = sorted(set(requested) - allowed)
    if unknown:
        raise ValueError(f"Unknown benchmark fixtures: {unknown}")

    with get_write_connection() as conn:
        meeting_id = _ensure_meeting(
            conn,
            meeting_title,
            "2026-01-15",
            user_id=user_id,
        )

    for fixture in requested:
        file_id, _ = await _ingest_fixture_file(
            fixture,
            meeting_id=meeting_id,
            user_id=user_id,
        )
        results[fixture] = (meeting_id, file_id)

    return results


async def ingest_all_fixtures() -> dict[str, tuple[int, int]]:
    """Ingest every benchmark fixture (used by ingestion coverage)."""
    return await ingest_fixtures(["sample.pdf", "scanned.pdf", "sample.pptx"])
