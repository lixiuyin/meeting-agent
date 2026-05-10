"""Regression tests for authenticated file downloads."""

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import database as db
from src.core.config import settings
from src.main import app


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_missing_stored_file_does_not_fallback_to_same_basename(client):
    fallback_file = settings.UPLOAD_DIR / "same-name.pdf"
    fallback_file.write_text("other meeting content")
    missing_file = settings.UPLOAD_DIR / "missing" / "same-name.pdf"

    with db.get_write_connection() as conn:
        meeting_id = db.create_meeting(conn, title="missing file", user_id="default")
        file_id = db.create_meeting_file(
            conn,
            meeting_id=meeting_id,
            file_type="pdf",
            file_name="same-name.pdf",
            file_path=str(missing_file),
        )
        db.update_meeting_file_status(conn, file_id, "ready")

    async with client as c:
        response = await c.get(f"/api/v1/meetings/{meeting_id}/files/{file_id}")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_expired_token_returns_403(client):
    """An expired scoped file token returns 403."""
    with db.get_write_connection() as conn:
        meeting_id = db.create_meeting(conn, title="token-expiry", user_id="default")

        test_file = settings.UPLOAD_DIR / "token-expiry-test.txt"
        test_file.write_text("expiry content")
        file_id = db.create_meeting_file(
            conn,
            meeting_id=meeting_id,
            file_type="txt",
            file_name="token-expiry-test.txt",
            file_path=str(test_file),
        )
        db.update_meeting_file_status(conn, file_id, "ready")

    from src.api.routers.file_download import _generate_scoped_file_token

    token, _ = _generate_scoped_file_token(meeting_id, file_id, expires_in=-1)

    async with client as c:
        response = await c.get(
            f"/api/v1/meetings/{meeting_id}/files/{file_id}",
            params={"token": token},
        )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_hmac_tampered_token_returns_403(client):
    """A token with a tampered signature is rejected."""
    with db.get_write_connection() as conn:
        meeting_id = db.create_meeting(conn, title="hmac-tamper", user_id="default")
        test_file = settings.UPLOAD_DIR / "hmac-test.txt"
        test_file.write_text("hmac content")
        file_id = db.create_meeting_file(
            conn,
            meeting_id=meeting_id,
            file_type="txt",
            file_name="hmac-test.txt",
            file_path=str(test_file),
        )
        db.update_meeting_file_status(conn, file_id, "ready")

    from src.api.routers.file_download import _generate_scoped_file_token

    token, _ = _generate_scoped_file_token(meeting_id, file_id)
    # Flip the last character of the signature to tamper
    sig_part, _, expiry_part = token.rpartition(".")
    tampered_sig = sig_part[:-1] + ("B" if sig_part[-1] != "B" else "A")
    tampered_token = f"{tampered_sig}.{expiry_part}"

    async with client as c:
        response = await c.get(
            f"/api/v1/meetings/{meeting_id}/files/{file_id}",
            params={"token": tampered_token},
        )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_path_traversal_rejected(client):
    """A DB record with a path outside UPLOAD_DIR returns 400."""
    import tempfile
    from pathlib import Path as FilePath

    outside_dir = tempfile.mkdtemp()
    outside_file = FilePath(outside_dir) / "outside.txt"
    outside_file.write_text("outside content")

    with db.get_write_connection() as conn:
        meeting_id = db.create_meeting(conn, title="traversal", user_id="default")
        file_id = db.create_meeting_file(
            conn,
            meeting_id=meeting_id,
            file_type="txt",
            file_name="outside.txt",
            file_path=str(outside_file),
        )
        db.update_meeting_file_status(conn, file_id, "ready")

    async with client as c:
        response = await c.get(f"/api/v1/meetings/{meeting_id}/files/{file_id}")

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_missing_meeting_returns_404(client):
    """Requesting a file for a non-existent meeting returns 404."""
    async with client as c:
        response = await c.get("/api/v1/meetings/99999/files/1")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_valid_scoped_token_allows_access(client):
    """A valid scoped token grants access to the specific file."""
    with db.get_write_connection() as conn:
        meeting_id = db.create_meeting(conn, title="valid-token", user_id="default")
        test_file = settings.UPLOAD_DIR / "valid-token-test.txt"
        test_file.write_text("valid access content")
        file_id = db.create_meeting_file(
            conn,
            meeting_id=meeting_id,
            file_type="txt",
            file_name="valid-token-test.txt",
            file_path=str(test_file),
        )
        db.update_meeting_file_status(conn, file_id, "ready")

    from src.api.routers.file_download import _generate_scoped_file_token

    token, _ = _generate_scoped_file_token(meeting_id, file_id)

    async with client as c:
        response = await c.get(
            f"/api/v1/meetings/{meeting_id}/files/{file_id}",
            params={"token": token},
        )

    assert response.status_code == 200
