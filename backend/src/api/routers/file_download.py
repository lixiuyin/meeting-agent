"""File download with short-lived token authentication.

Supports two auth methods for file downloads:
1. X-API-Key header (for programmatic / axios requests)
2. ?token= query parameter (for <audio>/<video>/<img> src attributes)

The token approach avoids exposing the API key in URLs (browser history,
server logs, referrer headers). Tokens are HMAC-SHA256 signed, derived
from the API key, and expire after 5 minutes.
"""

import asyncio
import base64
import hashlib
import hmac as hmac_mod
import logging
import mimetypes
import secrets
import time
from pathlib import Path as FilePath

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ...api.middleware import limiter
from ...core import database as db
from ...core.config import settings
from ...core.security import _derive_user_id_from_api_key, is_dev_user, verify_api_key

router = APIRouter(prefix="/meetings", tags=["meetings"])
logger = logging.getLogger(__name__)

# Token TTL in seconds
_FILE_TOKEN_TTL = 300  # 5 minutes


_dev_signing_key: str | None = None


def _derive_signing_key() -> bytes:
    """Derive a signing key from the configured API key.

    In dev mode (no API key), a per-startup random key is generated so that
    tokens are still HMAC-signed and cannot be forged, but they are not
    portable across restarts.
    """
    global _dev_signing_key
    key = settings.API_KEY.get_secret_value()
    if not key:
        if settings.ENVIRONMENT == "dev":
            if _dev_signing_key is None:
                _dev_signing_key = secrets.token_hex(32)
            key = _dev_signing_key
        else:
            raise RuntimeError(
                "API_KEY must be configured when ENVIRONMENT is not dev. "
                "File download signing requires a secret key."
            )
    return hashlib.sha256(f"file-download-token:{key}".encode()).digest()


def _generate_global_file_token(
    user_id: str = "default",
    *,
    purpose: str = "assets",
    expires_in: int = _FILE_TOKEN_TTL,
) -> str:
    """Generate a short-lived HMAC token bound to a user and purpose."""
    key = _derive_signing_key()
    expiry = int(time.time()) + expires_in
    message = f"{purpose}:{user_id}:{expiry}".encode()
    signature = hmac_mod.new(key, message, hashlib.sha256).digest()
    token = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return f"{token}.{expiry}"


def _generate_scoped_file_token(
    meeting_id: int,
    file_id: int,
    *,
    user_id: str = "default",
    expires_in: int = _FILE_TOKEN_TTL,
) -> tuple[str, int]:
    """Generate a short-lived HMAC token bound to one meeting file and user."""
    key = _derive_signing_key()
    expiry = int(time.time()) + expires_in
    message = f"{user_id}:{meeting_id}:{file_id}:{expiry}".encode()
    signature = hmac_mod.new(key, message, hashlib.sha256).digest()
    token = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return f"{token}.{expiry}", expiry


def _validate_global_file_token(
    token: str, *, user_id: str = "default", purpose: str = "assets"
) -> bool:
    """Validate a global file token bound to a user and purpose (HMAC + expiry check)."""
    if not token or "." not in token:
        return False
    sig_b64, _, expiry_str = token.rpartition(".")
    if not sig_b64 or not expiry_str:
        return False
    try:
        expiry = int(expiry_str)
    except ValueError:
        return False
    if time.time() > expiry:
        return False
    key = _derive_signing_key()
    payload = f"{purpose}:{user_id}:{expiry}".encode()
    expected = hmac_mod.new(key, payload, hashlib.sha256).digest()
    expected_b64 = base64.urlsafe_b64encode(expected).decode().rstrip("=")
    return hmac_mod.compare_digest(sig_b64, expected_b64)


def _validate_scoped_file_token(
    token: str, *, meeting_id: int, file_id: int, user_id: str = "default"
) -> bool:
    """Validate a file token bound to a specific meeting/file/user triplet."""
    if not token or "." not in token:
        return False
    sig_b64, _, expiry_str = token.rpartition(".")
    if not sig_b64 or not expiry_str:
        return False
    try:
        expiry = int(expiry_str)
    except ValueError:
        return False
    if time.time() > expiry:
        return False
    key = _derive_signing_key()
    payload = f"{user_id}:{meeting_id}:{file_id}:{expiry}".encode()
    expected = hmac_mod.new(key, payload, hashlib.sha256).digest()
    expected_b64 = base64.urlsafe_b64encode(expected).decode().rstrip("=")
    return hmac_mod.compare_digest(sig_b64, expected_b64)


def _verify_file_access(
    x_api_key: str | None,
    token: str | None,
    *,
    meeting_id: int,
    file_id: int,
    user_id: str = "default",
) -> None:
    """Verify that the request is authenticated via header or token.

    Accepts scoped tokens (bound to user_id:meeting_id:file_id).
    Global tokens (from POST /file-token) are purpose-bound to "assets" and
    are **not** accepted for per-file access — use signed-url for that.
    Raises HTTPException on auth failure.
    In dev mode (no API key configured), all requests are allowed.
    """
    configured_key = settings.API_KEY.get_secret_value()
    if not configured_key:
        # Dev mode: allow direct access. If a token is provided, validate it
        # (either scoped or global) to preserve token integrity.
        if (
            token
            and not _validate_scoped_file_token(
                token, meeting_id=meeting_id, file_id=file_id, user_id=user_id
            )
            and not _validate_global_file_token(token, user_id=user_id)
        ):
            raise HTTPException(403, "Invalid or expired token")
        return

    # Check scoped token first (for file-bound media element src attributes)
    if token and _validate_scoped_file_token(
        token, meeting_id=meeting_id, file_id=file_id, user_id=user_id
    ):
        return

    # Global tokens are purpose-scoped to "assets" only — they must NOT grant
    # access to per-file endpoints (/{meeting_id}/files/{file_id}).  Callers
    # that need per-file access must use a scoped token (signed-url) or the
    # API key header.

    # Check API key header (for programmatic access)
    if x_api_key and hmac_mod.compare_digest(x_api_key, configured_key):
        return

    # A provided but invalid token is an authorization failure.
    if token:
        raise HTTPException(403, "Invalid or expired token")

    raise HTTPException(401, "Invalid or missing authentication")


class FileTokenResponse(BaseModel):
    token: str


@router.post("/file-token", dependencies=[Depends(verify_api_key)])
@limiter.limit("60/minute")
async def create_file_token(
    request: Request,
    principal: dict[str, str] = Depends(verify_api_key),
) -> FileTokenResponse:
    """Generate a short-lived token for authenticating file downloads.

    Requires X-API-Key header. The returned token is bound to the requesting
    user, valid for 5 minutes, and can be used as a ``?token=`` query
    parameter for extracted asset URLs. Per-file downloads must use the
    file-bound ``signed-url`` endpoint instead.

    This avoids exposing the API key in URLs (browser history, server logs,
    referrer headers) while still allowing media elements to access files.
    """
    return FileTokenResponse(token=_generate_global_file_token(user_id=principal["user_id"]))


class SignedFileUrlResponse(BaseModel):
    url: str
    token: str
    expires_at: int


@router.post("/{meeting_id}/files/{file_id}/signed-url", dependencies=[Depends(verify_api_key)])
async def create_signed_file_url(
    meeting_id: int,
    file_id: int,
    user: dict[str, str] = Depends(verify_api_key),
) -> SignedFileUrlResponse:
    """Generate a short-lived token and signed URL bound to one file and user."""
    requester_id = user.get("user_id", "default")

    def _ensure_file() -> None:
        # Enforce per-user ownership when authentication is active.  Dev mode
        # bypasses the join so local tooling works without auth.
        ownership_filter = requester_id if not is_dev_user(requester_id) else None
        # Single connection ensures consistent snapshot (no TOCTOU gap).
        with db.get_connection() as conn:
            m = db.get_meeting(conn, meeting_id, user_id=ownership_filter)
            if not m:
                raise HTTPException(404, "Meeting not found")
            f = db.get_meeting_file(conn, file_id, user_id=ownership_filter)
            if not f or f["meeting_id"] != meeting_id:
                raise HTTPException(404, "File not found")

    await asyncio.to_thread(_ensure_file)
    token, expiry = _generate_scoped_file_token(meeting_id, file_id, user_id=requester_id)
    signed_url = f"/api/v1/meetings/{meeting_id}/files/{file_id}?token={token}"
    return SignedFileUrlResponse(url=signed_url, token=token, expires_at=expiry)


@router.get("/{meeting_id}/files/{file_id}")
@limiter.limit("60/minute")
async def get_meeting_file(
    request: Request,
    meeting_id: int,
    file_id: int,
    token: str | None = Query(None, description="Short-lived file download token"),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
):
    """Stream or download a specific meeting file.

    Authentication: Either ``X-API-Key`` header or ``?token=`` query parameter.
    """
    configured_key = settings.API_KEY.get_secret_value()
    # A token-only browser request has no API-key header, but the deployment
    # currently has one configured proxy principal. Validate and authorize the
    # token against that same principal rather than the legacy dev principal.
    user_id = _derive_user_id_from_api_key(configured_key) if configured_key else "default"

    _verify_file_access(x_api_key, token, meeting_id=meeting_id, file_id=file_id, user_id=user_id)

    # Ownership filter: None in dev mode, otherwise user_id.
    ownership_filter = user_id if not is_dev_user(user_id) else None

    def _fetch():
        with db.get_connection() as conn:
            m = db.get_meeting(conn, meeting_id, user_id=ownership_filter)
            if not m:
                raise HTTPException(404, "Meeting not found")
            f = db.get_meeting_file(conn, file_id, user_id=ownership_filter)
            if not f or f["meeting_id"] != meeting_id:
                raise HTTPException(404, "File not found")
            return f

    f = await asyncio.to_thread(_fetch)

    file_path = FilePath(f["file_path"])
    if not file_path.exists():
        raise HTTPException(404, "File not found on disk")
    if not file_path.resolve().is_relative_to(settings.UPLOAD_DIR.resolve()):
        raise HTTPException(400, "Invalid file path")
    if file_path.is_symlink():
        raise HTTPException(400, "Invalid file path")
    # Use Content-Disposition: inline so the browser displays the file
    # instead of downloading it. Only set filename as a name hint (RFC 6266).
    media_type, _ = mimetypes.guess_type(f["file_name"])
    return FileResponse(
        path=str(file_path),
        filename=f["file_name"],
        media_type=media_type or "application/octet-stream",
        content_disposition_type="inline",
    )


@router.get("/assets")
@limiter.limit("30/minute")
async def get_meeting_asset(
    request: Request,
    path: str = Query(..., description="Relative path under uploads/meeting_assets"),
    token: str | None = Query(None, description="Short-lived file download token"),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
):
    """Stream an extracted image asset (or thumbnail) by relative path."""
    configured_key = settings.API_KEY.get_secret_value()
    if configured_key:
        user_id = _derive_user_id_from_api_key(configured_key)
        valid_token = token and _validate_global_file_token(token, user_id=user_id)
        valid_api_key = x_api_key and hmac_mod.compare_digest(x_api_key, configured_key)
        if not (valid_token or valid_api_key):
            raise HTTPException(401, "Invalid or missing authentication")
    if not path:
        raise HTTPException(400, "Invalid asset path")
    # Decode any URL-encoded path components before validation
    from urllib.parse import unquote

    decoded_path = unquote(path)
    rel = FilePath(decoded_path)
    if rel.is_absolute():
        raise HTTPException(400, "Invalid asset path")
    root = (settings.UPLOAD_DIR / "meeting_assets").resolve()
    raw_full = root / rel
    # Check for symlink in any path component before resolve()
    for parent in (raw_full, *raw_full.parents):
        try:
            if parent.is_symlink():
                raise HTTPException(400, "Invalid asset path")
        except OSError:
            break
        if parent == root:
            break
    full_path = raw_full.resolve()
    if not full_path.is_relative_to(root):
        raise HTTPException(400, "Invalid asset path")
    if not full_path.exists() or not full_path.is_file():
        raise HTTPException(404, "Asset not found")
    media_type, _ = mimetypes.guess_type(full_path.name)
    return FileResponse(
        path=str(full_path),
        filename=full_path.name,
        media_type=media_type or "application/octet-stream",
        content_disposition_type="inline",
    )
