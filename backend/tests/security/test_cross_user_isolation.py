"""Cross-user isolation tests: user A cannot access user B's resources.

Tests verify that meetings/sessions endpoints enforce ownership filtering.
Uses app.dependency_overrides to simulate two different users.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import database as db
from src.core.database._connection import _connections, _local
from src.core.security import verify_api_key
from src.main import app

_UID_A = "test_user_a"
_UID_B = "test_user_b"


async def _verify_a():
    return {"user_id": _UID_A}


async def _verify_b():
    return {"user_id": _UID_B}


@pytest.fixture(autouse=True)
def _setup_db(tmp_path, monkeypatch):
    """Set up isolated database with auth enabled."""
    monkeypatch.setenv("API_KEY", "test-key")
    from src.core import config as config_mod
    from src.core.config import Settings

    new_settings = Settings(
        _env_file=None,
        UPLOAD_DIR=tmp_path / "uploads",
        VECTOR_DB_DIR=tmp_path / "vectors",
        DB_PATH=tmp_path / "test.db",
    )
    monkeypatch.setattr(config_mod, "settings", new_settings)

    # Reset thread-local connections so new DB is used
    for attr in ("conn", "write_conn"):
        if hasattr(_local, attr):
            delattr(_local, attr)
    _connections.clear()

    from src.core.database import init_db

    init_db()

    yield

    app.dependency_overrides.clear()
    for attr in ("conn", "write_conn"):
        if hasattr(_local, attr):
            delattr(_local, attr)
    _connections.clear()


def _seed_meeting(user_id: str) -> int:
    with db.get_write_connection() as conn:
        mid = db.create_meeting(conn, title="Test Meeting", user_id=user_id)
        db.update_meeting_status(conn, mid, "processing")
        db.update_meeting_status(conn, mid, "ready", transcript="hello world")
        fid = db.create_meeting_file(
            conn,
            meeting_id=mid,
            file_type="txt",
            file_name="test.txt",
            file_path="/tmp/test.txt",
            user_id=user_id,
        )
        db.update_meeting_file_status(conn, fid, "processing")
        db.update_meeting_file_status(conn, fid, "ready", transcript="hello world")
    return mid


def _seed_session(user_id: str) -> str:
    with db.get_write_connection() as conn:
        sid = db.create_session(conn, user_id=user_id, title="Test Session")
        db.add_message(conn, session_id=sid, role="human", content="hello")
        db.add_message(conn, session_id=sid, role="ai", content="hi")
    return sid


def _client_as(user_id: str) -> AsyncClient:
    """Create an AsyncClient authenticated as the given user."""
    if user_id == _UID_A:
        app.dependency_overrides[verify_api_key] = _verify_a
    else:
        app.dependency_overrides[verify_api_key] = _verify_b
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------------
# Meetings endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_meeting_cross_user():
    mid = _seed_meeting(_UID_A)
    async with _client_as(_UID_B) as c:
        resp = await c.get(f"/api/v1/meetings/{mid}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_owner_can_get_meeting():
    mid = _seed_meeting(_UID_A)
    async with _client_as(_UID_A) as c:
        resp = await c.get(f"/api/v1/meetings/{mid}")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_list_meetings_scoped():
    _seed_meeting(_UID_A)
    async with _client_as(_UID_A) as ca:
        resp_a = await ca.get("/api/v1/meetings")
    async with _client_as(_UID_B) as cb:
        resp_b = await cb.get("/api/v1/meetings")
    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    data_a = resp_a.json()
    data_b = resp_b.json()
    meetings_a = data_a.get("meetings", [])
    meetings_b = data_b.get("meetings", [])
    assert len(meetings_a) > 0, f"User A should see their meeting, got {data_a}"
    assert len(meetings_b) == 0, f"User B should see no meetings, got {data_b}"


@pytest.mark.asyncio
async def test_delete_meeting_cross_user():
    mid = _seed_meeting(_UID_A)
    async with _client_as(_UID_B) as c:
        resp = await c.delete(f"/api/v1/meetings/{mid}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_meeting_cross_user():
    mid = _seed_meeting(_UID_A)
    async with _client_as(_UID_B) as c:
        resp = await c.put(f"/api/v1/meetings/{mid}", json={"title": "hacked"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_transcript_cross_user():
    mid = _seed_meeting(_UID_A)
    async with _client_as(_UID_B) as c:
        resp = await c.get(f"/api/v1/meetings/{mid}/transcript")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_summary_cross_user():
    mid = _seed_meeting(_UID_A)
    async with _client_as(_UID_B) as c:
        resp = await c.get(f"/api/v1/meetings/{mid}/summary")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Sessions endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_session_messages_cross_user():
    sid = _seed_session(_UID_A)
    async with _client_as(_UID_B) as c:
        resp = await c.get(f"/api/v1/sessions/{sid}/messages")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_session_cross_user():
    sid = _seed_session(_UID_A)
    async with _client_as(_UID_B) as c:
        resp = await c.delete(f"/api/v1/sessions/{sid}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_session_summary_cross_user():
    sid = _seed_session(_UID_A)
    async with _client_as(_UID_B) as c:
        resp = await c.get(f"/api/v1/sessions/{sid}/summary")
    assert resp.status_code == 404
