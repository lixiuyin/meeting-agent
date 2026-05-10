"""Tests for meeting search endpoint."""

import json

import pytest
from httpx import ASGITransport, AsyncClient

from src.core.database import create_meeting, get_write_connection, update_meeting_status
from src.core.database.bm25 import add_bm25_chunk
from src.main import app


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


def _insert_searchable_meeting(title: str, transcript: str) -> int:
    with get_write_connection() as conn:
        meeting_id = create_meeting(
            conn,
            title=title,
            description="",
            file_type="pdf",
            file_name="test.pdf",
            file_path="/tmp/test.pdf",
            user_id="test",
        )
        update_meeting_status(conn, meeting_id, "processing")
        update_meeting_status(conn, meeting_id, "ready", transcript=transcript)
        # Also index into FTS5 so the search endpoint can find it
        add_bm25_chunk(
            conn,
            chunk_id=f"meeting_{meeting_id}_chunk_0",
            meeting_id=meeting_id,
            content=transcript,
            tokenized=json.dumps(transcript.split()),
            metadata=json.dumps({"title": title}),
        )
        conn.commit()
    return meeting_id


class TestSearchMeetings:
    @pytest.mark.asyncio
    async def test_search_content_success(self, client, auth_headers):
        _insert_searchable_meeting(
            title="Quarterly Review",
            transcript="We discussed quarterly goals and revenue.",
        )
        async with client as c:
            resp = await c.get(
                "/api/v1/meetings/search/content?q=quarterly&limit=10",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["query"] == "quarterly"
        assert data["total"] >= 1

    @pytest.mark.asyncio
    async def test_search_content_snippet(self, client, auth_headers):
        mid = _insert_searchable_meeting(
            title="Budget Meeting",
            transcript="The marketing budget was approved for Q1.",
        )
        async with client as c:
            resp = await c.get(
                "/api/v1/meetings/search/content?q=marketing&limit=10",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        result = next(r for r in data["results"] if r["meeting_id"] == mid)
        assert "marketing" in result["snippet"].lower()

    @pytest.mark.asyncio
    async def test_search_content_no_results(self, client, auth_headers):
        _insert_searchable_meeting(
            title="Random",
            transcript="Nothing relevant here.",
        )
        async with client as c:
            resp = await c.get(
                "/api/v1/meetings/search/content?q=xyznonexistent&limit=10",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["results"] == []

    @pytest.mark.asyncio
    async def test_search_content_empty_query(self, client, auth_headers):
        async with client as c:
            resp = await c.get(
                "/api/v1/meetings/search/content?q=&limit=10",
                headers=auth_headers,
            )
        assert resp.status_code == 422
