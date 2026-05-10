"""Idempotency key persistence for API contract hardening.

Response bodies are stored AES-GCM encrypted using a key derived from
the application API_KEY so that database-level access does not expose
raw response payloads.
"""

import base64
import json
import logging
import os
import sqlite3
import threading
from datetime import UTC, datetime, timedelta

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# 12-byte nonce is standard for AES-GCM
_NONCE_SIZE = 12
# HKDF info parameter for domain separation
_HKDF_INFO = b"meeting-agent-idempotency-v1"
# Fixed application-level salt for HKDF — prevents cross-derivation attacks.
_HKDF_SALT = b"meeting-agent-idempotency-salt-v1"
logger = logging.getLogger(__name__)
_dev_fallback_key: str | None = None


_derived_key_cache: dict[str, bytes] = {}
_derived_key_cache_lock = threading.Lock()


def _derive_encryption_key(raw_key: str) -> bytes:
    """Derive a 256-bit AES key from a raw key via HKDF (cached, CONC-8)."""
    with _derived_key_cache_lock:
        cached = _derived_key_cache.get(raw_key)
    if cached is not None:
        return cached
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_HKDF_SALT,
        info=_HKDF_INFO,
    )
    key = hkdf.derive(raw_key.encode())
    with _derived_key_cache_lock:
        _derived_key_cache.setdefault(raw_key, key)
    return _derived_key_cache[raw_key]


def _get_raw_key_candidates() -> list[str]:
    """Return ordered key candidates: current key first, then legacy keys."""
    from ..config import settings

    raw_key = settings.API_KEY.get_secret_value().strip()
    if not raw_key:
        # Dev mode: keep one process-stable random key to avoid immediate cache misses.
        import logging
        import secrets

        global _dev_fallback_key
        if _dev_fallback_key is None:
            _dev_fallback_key = secrets.token_hex(32)
            logging.getLogger(__name__).warning(
                "API_KEY is empty — generating a process-stable idempotency encryption key. "
                "Cached responses will not survive restart."
            )
        return [_dev_fallback_key]

    old_keys = [
        k.strip()
        for k in settings.IDEMPOTENCY_OLD_KEYS.split(",")
        if k.strip() and k.strip() != raw_key
    ]
    return [raw_key, *old_keys]


def _encrypt(plaintext: str) -> str:
    """AES-GCM encrypt a string, returning base64url-encoded nonce+ciphertext."""
    key = _derive_encryption_key(_get_raw_key_candidates()[0])
    nonce = os.urandom(_NONCE_SIZE)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode(), None)
    return base64.urlsafe_b64encode(nonce + ciphertext).decode()


def _decrypt(token: str) -> str:
    """Decrypt an AES-GCM encrypted token back to plaintext string."""
    raw = base64.urlsafe_b64decode(token)
    nonce = raw[:_NONCE_SIZE]
    ciphertext = raw[_NONCE_SIZE:]
    for raw_key in _get_raw_key_candidates():
        try:
            key = _derive_encryption_key(raw_key)
            aesgcm = AESGCM(key)
            return aesgcm.decrypt(nonce, ciphertext, None).decode()
        except (ValueError, KeyError, InvalidTag):
            logger.debug("Decryption failed for key candidate, trying next")
            continue
    raise ValueError("Unable to decrypt idempotency payload with configured key candidates")


def get_idempotency_response(
    conn: sqlite3.Connection,
    key: str,
    method: str,
    path: str,
    user_id: str,
    body_hash: str | None = None,
) -> dict | None:
    """Return cached response body if the idempotency key is still valid."""
    row = conn.execute(
        "SELECT response_body FROM idempotency_keys "
        "WHERE key = ? AND method = ? AND path = ? AND user_id = ? "
        "AND body_hash IS ? AND expires_at > ?",
        (key, method, path, user_id, body_hash, datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")),
    ).fetchone()
    if row:
        try:
            plaintext = _decrypt(row[0])
            return json.loads(plaintext)
        except (ValueError, KeyError, InvalidTag):
            # Corrupted or from a different encryption key — treat as cache miss
            logger.warning(
                "Failed to decrypt cached idempotency response for path=%s",
                path,
            )
            return None
    return None


def save_idempotency_response(
    conn: sqlite3.Connection,
    key: str,
    method: str,
    path: str,
    user_id: str,
    response_body: dict,
    body_hash: str | None = None,
) -> None:
    """Cache a response body tied to an idempotency key with a 24-hour TTL."""
    expires_at = datetime.now(UTC) + timedelta(hours=24)
    encrypted = _encrypt(json.dumps(response_body))
    conn.execute(
        "INSERT OR REPLACE INTO idempotency_keys "
        "(key, method, path, user_id, body_hash, response_body, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            key,
            method,
            path,
            user_id,
            body_hash,
            encrypted,
            expires_at.strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )


def cleanup_expired_idempotency_keys(conn: sqlite3.Connection) -> int:
    """Purge stale idempotency keys. Returns number of rows deleted."""
    cursor = conn.execute(
        "DELETE FROM idempotency_keys WHERE expires_at <= ?",
        (datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),),
    )
    return cursor.rowcount
