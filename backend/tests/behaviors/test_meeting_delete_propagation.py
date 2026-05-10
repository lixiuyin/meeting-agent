"""Behavior test: meeting deletion propagates to indexed state and retry queue."""

import json
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.core.database import (
    add_bm25_chunk,
    create_meeting,
    create_meeting_file,
    get_connection,
    get_write_connection,
    mark_chroma_indexed,
    update_meeting_file_status,
)
from src.main import app


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_delete_meeting_cascades_db_and_queues_vector_retry(client, auth_headers, tmp_path):
    file_path = tmp_path / "meeting.txt"
    file_path.write_text("hello")

    with get_write_connection() as conn:
        meeting_id = create_meeting(
            conn,
            title="Delete propagation",
            description="",
            file_type="txt",
            file_name=file_path.name,
            file_path=str(file_path),
            user_id="test",
        )
        file_id = create_meeting_file(
            conn,
            meeting_id=meeting_id,
            file_type="txt",
            file_name=file_path.name,
            file_path=str(file_path),
        )
        update_meeting_file_status(conn, file_id, "ready", transcript="hello")
        add_bm25_chunk(
            conn,
            chunk_id=f"meeting_{meeting_id}_file_{file_id}_chunk_0",
            meeting_id=meeting_id,
            content="hello",
            tokenized=json.dumps(["hello"]),
            metadata=json.dumps({"file_id": file_id}),
        )
        mark_chroma_indexed(
            conn,
            file_id=file_id,
            meeting_id=meeting_id,
            indexed_at="2026-01-01 00:00:00",
        )

    with patch("src.services.rag.delete_meeting_chunks", side_effect=RuntimeError("boom")):
        async with client as c:
            resp = await c.delete(f"/api/v1/meetings/{meeting_id}", headers=auth_headers)
    assert resp.status_code == 200

    with get_connection() as conn:
        assert conn.execute("SELECT 1 FROM meetings WHERE id=?", (meeting_id,)).fetchone() is None
        assert (
            conn.execute("SELECT 1 FROM meeting_files WHERE meeting_id=?", (meeting_id,)).fetchone()
            is None
        )
        assert (
            conn.execute("SELECT 1 FROM bm25_index WHERE meeting_id=?", (meeting_id,)).fetchone()
            is None
        )
        assert (
            conn.execute("SELECT 1 FROM index_state WHERE file_id=?", (file_id,)).fetchone() is None
        )
        queued = conn.execute(
            "SELECT embedding_id FROM pending_vector_deletions WHERE collection='meeting'"
        ).fetchall()
        assert any(row["embedding_id"] == str(meeting_id) for row in queued)
