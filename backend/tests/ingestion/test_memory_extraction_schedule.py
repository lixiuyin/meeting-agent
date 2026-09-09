from __future__ import annotations

import pytest

from src.services.processor import _memory_extraction as subject


def test_evidence_chunks_cover_tail_with_overlap() -> None:
    text = "A" * 12_345
    chunks = subject._evidence_chunks(text)
    assert len(chunks) == 3
    assert chunks[0][-300:] == chunks[1][:300]
    assert chunks[-1].endswith("A" * 300)


def test_evidence_chunks_do_not_drop_tail_at_legacy_file_cap(monkeypatch) -> None:
    monkeypatch.setattr(subject.settings, "MEMORY_INGEST_CHUNK_CHARS", 100)
    monkeypatch.setattr(subject.settings, "MEMORY_INGEST_CHUNK_OVERLAP", 10)
    monkeypatch.setattr(subject.settings, "MEMORY_INGEST_MAX_CHUNKS_PER_FILE", 2)
    chunks = subject._evidence_chunks("A" * 350)
    assert len(chunks) == 4
    assert chunks[-1].endswith("A" * 80)


@pytest.mark.asyncio
async def test_schedule_file_memory_extraction_is_scoped_and_content_addressed(monkeypatch) -> None:
    calls: list[dict] = []

    async def _enqueue(**kwargs):
        calls.append(kwargs)
        return "job"

    monkeypatch.setattr(subject.settings, "MEMORY_AUTO_EXTRACT", True)
    monkeypatch.setattr(subject, "enqueue_durable_job", _enqueue)
    monkeypatch.setattr(subject.db, "get_connection", lambda: _NullConnection())
    monkeypatch.setattr(subject.db, "get_meeting_file", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(subject.db, "get_meeting", lambda *_args, **_kwargs: None)
    count = await subject.schedule_file_memory_extraction(
        user_id="u1",
        meeting_id=7,
        file_id=9,
        file_name="minutes.txt",
        text="Alice owns Project Orbit.",
    )

    assert count == 1
    assert calls[0]["kind"] == "fact_extraction"
    assert calls[0]["payload"]["meeting_ids"] == [7]
    assert calls[0]["payload"]["file_ids"] == [9]
    assert calls[0]["payload"]["evidence_text"] == "Alice owns Project Orbit."
    assert calls[0]["payload"]["source_window_start"] == 0
    assert calls[0]["payload"]["source_window_end"] == 25
    assert len(calls[0]["payload"]["source_revision"]) == 64
    assert calls[0]["dedupe_key"].startswith("meeting:7:file:9:part:1:")


class _NullConnection:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False
