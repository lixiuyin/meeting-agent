from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from src.api.routers.meetings import _evidence
from src.core import database as db
from src.models.schemas.evidence import EvidenceLocationRequest
from src.models.schemas.meetings import PagesTimeline


@pytest.mark.asyncio
async def test_locator_checks_ownership_version_and_exact_unicode_range(monkeypatch):
    source = "😀 Intro\n\nRelease in September."
    with db.get_write_connection() as conn:
        meeting = db.create_meeting(conn, title="source", user_id="locator")
        file = db.create_meeting_file(
            conn,
            meeting_id=meeting,
            file_type="pdf",
            file_name="minutes.pdf",
            file_path="not-used.pdf",
            user_id="locator",
            content_hash="source-hash",
        )
        conn.execute(
            "UPDATE meeting_files SET transcript=?,status='ready' WHERE id=?", (source, file)
        )
    timeline = PagesTimeline(
        kind="pages",
        file_id=file,
        file_name="minutes.pdf",
        page_count=2,
        pages=[
            {"page_num": 1, "text": "😀 Intro"},
            {"page_num": 2, "text": "Release in September."},
        ],
    )
    reader = AsyncMock(return_value=timeline)
    monkeypatch.setattr(_evidence, "get_file_timeline", reader)
    request = EvidenceLocationRequest(source_revision="source-hash", excerpt="in September")
    result = await _evidence.locate_file_evidence(meeting, file, request, {"user_id": "locator"})
    assert result.status == "exact" and result.page == 2 and result.evidence_id
    assert source[result.window_start : result.window_end] == "in September"
    from src.services.chain._memory_sources import memory_evidence_sources

    entry = {
        "key": "decision.release",
        "evidence_excerpt": "in September",
        "evidence_refs": [
            {"meeting_id": meeting, "file_id": file, "source_revision": "source-hash"}
        ],
    }
    sources = memory_evidence_sources([entry], "locator")
    assert len(sources) == 1 and sources[0]["memory_key"] == "decision.release"
    assert sources[0]["document_revision"] == "source-hash"
    assert memory_evidence_sources([entry], "private") == []
    assert (
        memory_evidence_sources(
            [{**entry, "evidence_refs": [{"file_id": file, "source_revision": "old"}]}], "locator"
        )
        == []
    )
    with pytest.raises(HTTPException) as error:
        await _evidence.locate_file_evidence(meeting, file, request, {"user_id": "private"})
    assert error.value.status_code == 404
    stale = await _evidence.locate_file_evidence(
        meeting, file, request.model_copy(update={"source_revision": "old"}), {"user_id": "locator"}
    )
    assert stale.status == "version_changed" and stale.page is None
    assert reader.await_count == 1


@pytest.mark.asyncio
async def test_locator_rechecks_file_after_timeline_read(monkeypatch):
    with db.get_write_connection() as conn:
        meeting = db.create_meeting(conn, title="race", user_id="locator-race")
        file = db.create_meeting_file(
            conn,
            meeting_id=meeting,
            file_type="pdf",
            file_name="minutes.pdf",
            file_path="not-used.pdf",
            user_id="locator-race",
            content_hash="v1",
        )
        conn.execute(
            "UPDATE meeting_files SET transcript='original',status='ready' WHERE id=?", (file,)
        )

    async def replace(*args):
        with db.get_write_connection() as conn:
            conn.execute(
                "UPDATE meeting_files SET content_hash='v2',source_revision=source_revision+1 WHERE id=?",
                (file,),
            )
        return PagesTimeline(
            kind="pages",
            file_id=file,
            file_name="minutes.pdf",
            page_count=1,
            pages=[{"page_num": 1, "text": "original"}],
        )

    monkeypatch.setattr(_evidence, "get_file_timeline", replace)
    result = await _evidence.locate_file_evidence(
        meeting, file, EvidenceLocationRequest(excerpt="original"), {"user_id": "locator-race"}
    )
    assert result.status == "version_changed"
