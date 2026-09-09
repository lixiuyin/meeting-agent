"""Security regression tests for file token scope binding."""

import hashlib

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from src.core.config import settings
from src.core.database import (
    create_meeting,
    create_meeting_file,
    get_write_connection,
    update_meeting_file_status,
)
from src.core.security import _derive_user_id_from_api_key
from src.main import app


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def api_key_env(monkeypatch):
    """Enable API key auth (non-dev mode) for tests that need real user binding."""
    monkeypatch.setattr(settings, "API_KEY", SecretStr("real-api-key-123"))


@pytest.mark.asyncio
async def test_production_scoped_token_works_without_api_key_header(
    client, api_key_env, monkeypatch
):
    """Browser token-only requests must retain the HTTP principal."""
    monkeypatch.setattr(
        settings,
        "PRINCIPAL_PEPPER",
        SecretStr("stable-file-token-pepper"),
    )
    owner = _derive_user_id_from_api_key("real-api-key-123")
    upload_dir = settings.UPLOAD_DIR / "test_prod_token"
    upload_dir.mkdir(parents=True, exist_ok=True)
    test_file = upload_dir / "prod-token.pdf"
    test_file.write_text("production-token-content")

    with get_write_connection() as conn:
        meeting_id = create_meeting(
            conn,
            title="Production scoped token",
            description="",
            file_type="pdf",
            file_name=test_file.name,
            file_path=str(test_file),
            user_id=owner,
        )
        file_id = create_meeting_file(
            conn,
            meeting_id=meeting_id,
            file_type="pdf",
            file_name=test_file.name,
            file_path=str(test_file),
        )
        update_meeting_file_status(conn, file_id, "ready")

    async with client as c:
        signed = await c.post(
            f"/api/v1/meetings/{meeting_id}/files/{file_id}/signed-url",
            headers={"X-API-Key": "real-api-key-123"},
        )
        assert signed.status_code == 200

        downloaded = await c.get(signed.json()["url"])

        speaker_url = (
            f"/api/v1/meetings/{meeting_id}/files/{file_id}/speakers/A/audio"
            f"?token={signed.json()['token']}"
        )
        speaker_response = await c.get(speaker_url)

    assert downloaded.status_code == 200
    assert downloaded.content == b"production-token-content"
    # The fixture has no speaker segments, but scoped token authentication
    # must succeed without relying on the reverse proxy's API-key injection.
    assert speaker_response.status_code not in (401, 403)


@pytest.mark.asyncio
async def test_file_token_cannot_be_reused_for_other_file(client, auth_headers, tmp_path):
    upload_dir = settings.UPLOAD_DIR / "test_security_files"
    upload_dir.mkdir(parents=True, exist_ok=True)
    file1 = upload_dir / "f1.pdf"
    file2 = upload_dir / "f2.pdf"
    file1.write_text("file-one")
    file2.write_text("file-two")

    with get_write_connection() as conn:
        meeting_id = create_meeting(
            conn,
            title="Scoped tokens",
            description="",
            file_type="pdf",
            file_name=file1.name,
            file_path=str(file1),
            user_id="test",
        )
        file_id_1 = create_meeting_file(
            conn,
            meeting_id=meeting_id,
            file_type="pdf",
            file_name=file1.name,
            file_path=str(file1),
        )
        file_id_2 = create_meeting_file(
            conn,
            meeting_id=meeting_id,
            file_type="pdf",
            file_name=file2.name,
            file_path=str(file2),
        )
        update_meeting_file_status(conn, file_id_1, "ready")
        update_meeting_file_status(conn, file_id_2, "ready")

    async with client as c:
        signed = await c.post(
            f"/api/v1/meetings/{meeting_id}/files/{file_id_1}/signed-url",
            headers=auth_headers,
        )
        assert signed.status_code == 200
        token = signed.json()["token"]

        ok = await c.get(f"/api/v1/meetings/{meeting_id}/files/{file_id_1}?token={token}")
        assert ok.status_code == 200

        denied = await c.get(f"/api/v1/meetings/{meeting_id}/files/{file_id_2}?token={token}")
        assert denied.status_code == 403


@pytest.mark.asyncio
async def test_global_token_accepted_for_file_access(client, auth_headers, tmp_path):
    upload_dir = settings.UPLOAD_DIR / "test_global_token"
    upload_dir.mkdir(parents=True, exist_ok=True)
    test_file = upload_dir / "global_test.pdf"
    test_file.write_text("global-token-content")

    with get_write_connection() as conn:
        meeting_id = create_meeting(
            conn,
            title="Global token test",
            description="",
            file_type="pdf",
            file_name=test_file.name,
            file_path=str(test_file),
            user_id="test",
        )
        file_id = create_meeting_file(
            conn,
            meeting_id=meeting_id,
            file_type="pdf",
            file_name=test_file.name,
            file_path=str(test_file),
        )
        update_meeting_file_status(conn, file_id, "ready")

    async with client as c:
        global_token_resp = await c.post("/api/v1/meetings/file-token", headers=auth_headers)
        assert global_token_resp.status_code == 200
        global_token = global_token_resp.json()["token"]

        resp = await c.get(f"/api/v1/meetings/{meeting_id}/files/{file_id}?token={global_token}")
        assert resp.status_code == 200


def test_global_token_rejected_for_different_user():
    """A global token generated for user A must fail validation for user B."""
    from src.api.routers.file_download import (
        _generate_global_file_token,
        _validate_global_file_token,
    )

    token_for_alice = _generate_global_file_token(user_id="alice")

    # Token is valid for alice
    assert _validate_global_file_token(token_for_alice, user_id="alice") is True

    # Token is NOT valid for bob (different user_id in HMAC)
    assert _validate_global_file_token(token_for_alice, user_id="bob") is False


def test_dev_mode_signing_key_is_random_per_startup():
    """Dev mode signing key must not be a predictable hardcoded value."""
    from src.api.routers.file_download import _derive_signing_key

    key = _derive_signing_key()
    assert key != hashlib.sha256(b"file-download-token:dev-mode-insecure-key").digest()
