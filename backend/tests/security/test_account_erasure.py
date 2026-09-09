"""Account erasure must durably cover SQL, files, assets, and vectors."""

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import database as db
from src.core.config import settings
from src.core.database import get_write_connection
from src.main import app


@pytest.mark.asyncio
async def test_account_erasure_queues_every_external_resource(auth_headers):
    source = settings.UPLOAD_DIR / "erasure-source.txt"
    source.write_text("erase me", encoding="utf-8")
    with get_write_connection() as conn:
        meeting_id = db.create_meeting(conn, title="erase", user_id="default")
        db.create_meeting_file(
            conn,
            meeting_id=meeting_id,
            file_type="txt",
            file_name="erasure-source.txt",
            file_path=str(source),
            user_id="default",
        )
        session_id = db.create_session(conn, user_id="default")
        db.upsert_session_summary(
            conn,
            session_id=session_id,
            user_id="default",
            summary="summary",
            embedding_id="session-vector",
        )
        conn.execute(
            "INSERT INTO user_memories (user_id, key, value, embedding_id) VALUES (?, ?, ?, ?)",
            ("default", "secret", "value", "memory-vector"),
        )
        conn.execute(
            "INSERT INTO memory_entities "
            "(user_id, name, entity_type, embedding_id) VALUES (?, ?, ?, ?)",
            ("default", "person", "person", "entity-vector"),
        )
    asset_dir = settings.UPLOAD_DIR / "meeting_assets" / str(meeting_id) / "1"
    asset_dir.mkdir(parents=True)
    (asset_dir / "page.png").write_bytes(b"png")

    with patch(
        "src.services.memory._service._crud.cleanup_pending_vector_deletions",
        return_value=0,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.delete(
                "/api/v1/settings/account",
                headers={**auth_headers, "Idempotency-Key": "erase-all-1"},
            )

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "pending"
    assert payload["pending_jobs"] == payload["total_jobs"]
    batch_id = payload["deletion_batch_id"]
    with db.get_connection() as conn:
        assert db.count_meetings(conn, user_id="default") == 0
        assert db.count_sessions(conn, user_id="default") == 0
        jobs = {
            (row["collection"], row["embedding_id"])
            for row in conn.execute(
                "SELECT collection, embedding_id FROM pending_vector_deletions "
                "WHERE deletion_batch_id=?",
                (batch_id,),
            ).fetchall()
        }
    assert ("file", str(source)) in jobs
    assert ("directory", str(asset_dir.parent)) in jobs
    assert ("session_summary", "session-vector") in jobs
    assert ("memory", "memory-vector") in jobs
    assert ("entity", "entity-vector") in jobs
    assert ("meeting", str(meeting_id)) in jobs


@pytest.mark.asyncio
async def test_account_erasure_idempotency_reuses_batch(auth_headers):
    with patch(
        "src.services.memory._service._crud.cleanup_pending_vector_deletions",
        return_value=0,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            first = await client.delete(
                "/api/v1/settings/account",
                headers={**auth_headers, "Idempotency-Key": "erase-idempotent"},
            )
            second = await client.delete(
                "/api/v1/settings/account",
                headers={**auth_headers, "Idempotency-Key": "erase-idempotent"},
            )
    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["deletion_batch_id"] == second.json()["deletion_batch_id"]
