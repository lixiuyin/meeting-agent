"""Integration tests for native + RAGAnything dual-write in ingest pipeline."""

import asyncio
from contextlib import nullcontext
from pathlib import Path

from src.core.database import (
    create_meeting,
    create_meeting_file,
    get_connection,
    get_write_connection,
)
from src.services.processor import process_meeting_file
from src.services.processor._processors._types import FileArtefact
from src.services.rag._indexer_store import NativeIndexManifest


def _create_meeting_file(tmp_path: Path) -> tuple[int, int]:
    file_path = tmp_path / "notes.txt"
    file_path.write_text(
        "Roadmap notes include milestones, owners, deliverables, and timeline details.",
        encoding="utf-8",
    )
    with get_write_connection() as conn:
        meeting_id = create_meeting(
            conn,
            title="Dualwrite",
            description="",
            meeting_date="2026-01-01",
            user_id="test",
        )
        file_id = create_meeting_file(
            conn,
            meeting_id=meeting_id,
            file_type="txt",
            file_name="notes.txt",
            file_path=str(file_path),
        )
    return meeting_id, file_id


def _native_manifest() -> NativeIndexManifest:
    return NativeIndexManifest(
        generation="test-generation",
        config_fingerprint="test-fingerprint",
        chroma_chunk_count=1,
        bm25_chunk_count=1,
        checksum="test-checksum",
    )


class _StubProcessor:
    async def process(self, _ctx):
        return FileArtefact(
            text="Roadmap notes include milestones, owners, deliverables, and timeline details.",
            structured_json=None,
            structured_kind=None,
            metrics={"word_count": 10},
            parsed_doc=None,
        )


def test_dualwrite_marks_raganything_doc_id_on_success(monkeypatch, tmp_path):
    _, file_id = _create_meeting_file(tmp_path)
    native_indexed: list[int] = []
    rag_indexed: list[int] = []

    monkeypatch.setattr(
        "src.services.processor._pipeline._resolve_processor", lambda _ft: _StubProcessor()
    )
    monkeypatch.setattr(
        "src.services.processor._pipeline.index_meeting",
        lambda meeting_id, text, metadata, trace=None, **_kwargs: native_indexed.append(meeting_id),
    )
    monkeypatch.setattr(
        "src.services.processor._pipeline.atomic_file_index_replacement",
        lambda *_args, **_kwargs: nullcontext("test-generation"),
    )
    monkeypatch.setattr(
        "src.services.processor._pipeline.inspect_native_index_generation",
        lambda *_args, **_kwargs: _native_manifest(),
    )
    monkeypatch.setattr(
        "src.services.processor._pipeline.index_with_raganything",
        lambda **kwargs: rag_indexed.append(int(kwargs["file_id"])),
    )
    monkeypatch.setattr("src.services.processor._pipeline.settings.RAGANYTHING_ENABLED", True)
    monkeypatch.setattr(
        "src.services.processor._pipeline.settings.MEETING_AUTO_SUMMARIZE_FILES", False
    )

    asyncio.run(process_meeting_file(file_id))

    assert native_indexed
    assert rag_indexed == [file_id]
    with get_connection() as conn:
        row = conn.execute(
            "SELECT status, meeting_id, raganything_doc_id FROM meeting_files WHERE id=?",
            (file_id,),
        ).fetchone()
    assert row is not None
    assert row["status"] == "ready"
    assert row["raganything_doc_id"] == f"meeting_{row['meeting_id']}_file_{file_id}"


def test_dualwrite_raganything_failure_keeps_native_success(monkeypatch, tmp_path):
    _, file_id = _create_meeting_file(tmp_path)
    native_indexed: list[int] = []

    monkeypatch.setattr(
        "src.services.processor._pipeline._resolve_processor", lambda _ft: _StubProcessor()
    )
    monkeypatch.setattr(
        "src.services.processor._pipeline.index_meeting",
        lambda meeting_id, text, metadata, trace=None, **_kwargs: native_indexed.append(meeting_id),
    )
    monkeypatch.setattr(
        "src.services.processor._pipeline.atomic_file_index_replacement",
        lambda *_args, **_kwargs: nullcontext("test-generation"),
    )
    monkeypatch.setattr(
        "src.services.processor._pipeline.inspect_native_index_generation",
        lambda *_args, **_kwargs: _native_manifest(),
    )
    monkeypatch.setattr(
        "src.services.processor._pipeline.index_with_raganything",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("raganything unavailable")),
    )
    monkeypatch.setattr("src.services.processor._pipeline.settings.RAGANYTHING_ENABLED", True)
    monkeypatch.setattr(
        "src.services.processor._pipeline.settings.MEETING_AUTO_SUMMARIZE_FILES", False
    )

    asyncio.run(process_meeting_file(file_id))

    assert native_indexed
    with get_connection() as conn:
        row = conn.execute(
            "SELECT status, raganything_doc_id FROM meeting_files WHERE id=?",
            (file_id,),
        ).fetchone()
    assert row is not None
    assert row["status"] == "ready"
    assert row["raganything_doc_id"] is None
