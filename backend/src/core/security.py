"""API key authentication dependency"""

import hashlib
import hmac
import secrets

from fastapi import Header, HTTPException, Request

from .config import settings

# Per-process random pepper for dev mode.  Regenerated on every restart,
# which is fine — dev sessions don't need stable user IDs across restarts.
_DEV_PEPPER: bytes = secrets.token_hex(32).encode()


def _get_pepper() -> bytes:
    """Return the HMAC pepper for user-id derivation.

    Uses ``PRINCIPAL_PEPPER`` from config when set.  In dev mode (no API_KEY),
    a random per-process pepper is used.  In production, a missing pepper is a
    fatal startup error.
    """
    pepper = settings.PRINCIPAL_PEPPER.get_secret_value()
    if pepper:
        return pepper.encode()
    configured_key = settings.API_KEY.get_secret_value()
    if not configured_key:
        return _DEV_PEPPER
    raise RuntimeError(
        "PRINCIPAL_PEPPER must be set in production. "
        'Generate one with: python -c "import secrets; print(secrets.token_hex(32))"'
    )


def _derive_user_id_from_api_key(api_key: str) -> str:
    """Return a stable, non-reversible user id derived from API key bytes.

    Uses HMAC-SHA256 with a configurable pepper so the same API key always maps to
    the same user id, but the original key cannot be recovered.

    Note: the resulting user id is deterministic per API key. For deployments
    that need to hide the fact that user ids are derived from API keys, a
    random UUID mapping table (e.g. ``api_key_id_map``) should be introduced
    in a future migration.
    """
    digest = hmac.new(
        _get_pepper(),
        api_key.encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"api_{digest[:24]}"


def _derive_user_id_from_client(request: Request) -> str:
    """Derive a stable per-client user id for dev mode (no API key).

    Uses client IP + per-process pepper so the same client gets a consistent
    user_id within a process lifetime, but ids are not stable across restarts.
    """
    ip = request.client.host if request.client else "anonymous"
    digest = hmac.new(
        _DEV_PEPPER,
        ip.encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"dev_{digest[:16]}"


# Prefix for dev-mode derived user IDs (per-client IP-based identity).
_DEV_USER_ID_PREFIX = "dev_"

# Legacy dev-mode user ID — kept for backward compatibility with existing
# databases that were seeded with "default" before per-client identity was
# implemented (HIGH-2).
_LEGACY_DEV_USER_ID = "default"


def is_dev_user(user_id: str) -> bool:
    """Return True if *user_id* is a dev-mode identity (no real auth).

    Dev-mode identities are either the legacy ``"default"`` marker or the new
    per-client ``dev_<hash>`` format derived in :func:`_derive_user_id_from_client`.
    Ownership filtering should be skipped (return ``None``) for dev users since
    all data is effectively shared in local development.
    """
    return user_id == _LEGACY_DEV_USER_ID or user_id.startswith(_DEV_USER_ID_PREFIX)


async def verify_api_key(
    request: Request,
    x_api_key: str = Header(None, alias="X-API-Key"),
) -> dict[str, str]:
    """
    Dependency that validates the API key header and returns the authenticated principal.

    In production, requests must include a matching X-API-Key header and the
    principal is derived from the API key via HMAC-SHA256.

    In dev mode (empty API_KEY), returns the legacy ``"default"`` user_id.
    Dev mode is intended for single-user local development only
    (HIGH-2).  For multi-user dev instances, set API_KEY per-client.
    """
    configured_key = settings.API_KEY.get_secret_value()
    if not configured_key:
        return {"user_id": "default"}

    if not x_api_key or not hmac.compare_digest(x_api_key, configured_key):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key. Set X-API-Key header.",
        )

    return {"user_id": _derive_user_id_from_api_key(x_api_key)}
