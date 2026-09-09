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
import stat
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
_DEV_KEY_FILE = ".idempotency-key"


_derived_key_cache: dict[str, bytes] = {}
_derived_key_cache_lock = threading.Lock()
_IN_PROGRESS_FIELD = "_idempotency_in_progress"
_RESERVATION_LEASE_SECONDS = 30
_STATE_LEGACY_UNKNOWN = "legacy_unknown"
_STATE_IN_PROGRESS = "in_progress"
_STATE_EFFECTS_COMMITTED = "effects_committed"
_STATE_COMPLETED = "completed"
# Ambiguous commits remain fail-closed long enough for operator recovery, but
# are not immortal when their encryption key is rotated or lost.
_AMBIGUOUS_RETENTION = timedelta(days=7)


def _retention_elapsed(created_at: object, now: datetime) -> bool:
    if created_at is None:
        return False
    cutoff = (now - _AMBIGUOUS_RETENTION).strftime("%Y-%m-%d %H:%M:%S")
    return str(created_at) <= cutoff


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


def _load_or_create_dev_key(data_dir: Path) -> str:
    """Return a restart-stable local key stored in a private data file.

    Development mode deliberately has no API authentication key. Reusing a
    process-random value for encryption made completed idempotency responses
    unreadable after every restart. The data directory is already private and
    durable, so keep a separate 0600 key there without coupling authentication
    and at-rest encryption.
    """
    import secrets

    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    key_path = data_dir / _DEV_KEY_FILE
    nofollow = getattr(os, "O_NOFOLLOW", 0)

    def _read() -> str:
        fd = os.open(key_path, os.O_RDONLY | nofollow)
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode):
                raise RuntimeError("Development idempotency key must be a regular file")
            if stat.S_IMODE(metadata.st_mode) & 0o077:
                raise RuntimeError("Development idempotency key permissions must be 0600")
            value = os.read(fd, 256).decode("ascii").strip()
        finally:
            os.close(fd)
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise RuntimeError("Development idempotency key file is invalid")
        return value

    try:
        return _read()
    except FileNotFoundError:
        value = secrets.token_hex(32)
        try:
            fd = os.open(
                key_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow,
                0o600,
            )
        except FileExistsError:
            return _read()
        try:
            os.write(fd, (value + "\n").encode("ascii"))
            os.fsync(fd)
        finally:
            os.close(fd)
        return value


def _get_raw_key_candidates() -> list[str]:
    """Return ordered key candidates: current key first, then legacy keys."""
    from ..config import settings

    raw_key = settings.API_KEY.get_secret_value().strip()
    if not raw_key:
        from ..constants import DATA_DIR

        global _dev_fallback_key
        if _dev_fallback_key is None:
            _dev_fallback_key = _load_or_create_dev_key(DATA_DIR)
            logger.info(
                "API_KEY is empty; using the private restart-stable development idempotency key"
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


def _decrypt(token: str, *, log_failures: bool = True) -> str:
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
            if log_failures:
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
            payload = json.loads(plaintext)
            if isinstance(payload, dict):
                payload.pop("_idempotency_completed_by", None)
            return payload
        except (ValueError, KeyError, InvalidTag):
            # Corrupted or from a different encryption key — treat as cache miss
            logger.warning(
                "Failed to decrypt cached idempotency response for path=%s",
                path,
            )
            return None
    return None


def claim_idempotency_request(
    conn: sqlite3.Connection,
    *,
    key: str,
    method: str,
    path: str,
    user_id: str,
    body_hash: str | None,
    reservation_id: str | None = None,
) -> tuple[str, dict | None, str | None]:
    """Atomically reserve a logical mutation or return its completed response."""
    token = reservation_id or uuid.uuid4().hex
    now = datetime.now(UTC)
    marker = _encrypt(json.dumps({_IN_PROGRESS_FIELD: token}))
    cursor = conn.execute(
        "INSERT OR IGNORE INTO idempotency_keys "
        "(key, method, path, user_id, body_hash, response_body, expires_at, lifecycle_state) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            key,
            method,
            path,
            user_id,
            body_hash,
            marker,
            (now + timedelta(seconds=_RESERVATION_LEASE_SECONDS)).strftime("%Y-%m-%d %H:%M:%S"),
            _STATE_IN_PROGRESS,
        ),
    )
    if cursor.rowcount == 1:
        return "owner", None, token

    row = conn.execute(
        "SELECT method, path, user_id, body_hash, response_body, expires_at, "
        "created_at, lifecycle_state "
        "FROM idempotency_keys WHERE key=?",
        (key,),
    ).fetchone()
    if row is None:
        return "retry", None, None
    now_text = now.strftime("%Y-%m-%d %H:%M:%S")
    if str(row["expires_at"]) <= now_text:
        lifecycle_state = str(row["lifecycle_state"] or _STATE_LEGACY_UNKNOWN)
        if lifecycle_state in {_STATE_COMPLETED, _STATE_IN_PROGRESS}:
            conn.execute(
                "DELETE FROM idempotency_keys WHERE key=? AND expires_at<=?", (key, now_text)
            )
            return "retry", None, None
        if lifecycle_state == _STATE_EFFECTS_COMMITTED:
            if _retention_elapsed(row["created_at"], now):
                conn.execute(
                    "DELETE FROM idempotency_keys WHERE key=? AND expires_at<=?", (key, now_text)
                )
                return "retry", None, None
            return "recovery_required", None, None
        try:
            expired = json.loads(_decrypt(row["response_body"]))
        except (ValueError, KeyError, InvalidTag, json.JSONDecodeError):
            if _retention_elapsed(row["created_at"], now):
                conn.execute(
                    "DELETE FROM idempotency_keys WHERE key=? AND expires_at<=?", (key, now_text)
                )
                return "retry", None, None
            return "conflict", None, None
        if isinstance(expired, dict) and expired.get("_effects_committed"):
            if _retention_elapsed(row["created_at"], now):
                conn.execute(
                    "DELETE FROM idempotency_keys WHERE key=? AND expires_at<=?", (key, now_text)
                )
                return "retry", None, None
            return "recovery_required", None, None
        conn.execute("DELETE FROM idempotency_keys WHERE key=? AND expires_at<=?", (key, now_text))
        return "retry", None, None
    if row["method"] != method or row["path"] != path or row["user_id"] != user_id:
        return "conflict", None, None
    if row["body_hash"] != body_hash:
        return "conflict", None, None
    try:
        payload = json.loads(_decrypt(row["response_body"]))
    except (ValueError, KeyError, InvalidTag, json.JSONDecodeError):
        return "conflict", None, None
    if isinstance(payload, dict) and payload.get(_IN_PROGRESS_FIELD):
        return "pending", None, None
    if isinstance(payload, dict):
        payload.pop("_idempotency_completed_by", None)
    return "completed", payload if isinstance(payload, dict) else None, None


def complete_idempotency_request(
    conn: sqlite3.Connection,
    *,
    key: str,
    reservation_id: str,
    response_body: dict,
) -> bool:
    """Complete a reservation only when it is still owned by the caller."""
    row = conn.execute(
        "SELECT response_body FROM idempotency_keys WHERE key=? AND expires_at>CURRENT_TIMESTAMP",
        (key,),
    ).fetchone()
    if row is None:
        return False
    try:
        current = json.loads(_decrypt(row["response_body"]))
    except (ValueError, KeyError, InvalidTag, json.JSONDecodeError):
        return False
    if not isinstance(current, dict) or current.get(_IN_PROGRESS_FIELD) != reservation_id:
        return False
    conn.execute(
        "UPDATE idempotency_keys SET response_body=?, expires_at=?, lifecycle_state=? WHERE key=?",
        (
            _encrypt(json.dumps({**response_body, "_idempotency_completed_by": reservation_id})),
            (datetime.now(UTC) + timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S"),
            _STATE_COMPLETED,
            key,
        ),
    )
    return True


def renew_idempotency_request(
    conn: sqlite3.Connection,
    *,
    key: str,
    reservation_id: str,
) -> bool:
    """Extend an owned in-progress reservation lease."""
    row = conn.execute(
        "SELECT response_body FROM idempotency_keys WHERE key=? AND expires_at>CURRENT_TIMESTAMP",
        (key,),
    ).fetchone()
    if row is None:
        return False
    try:
        current = json.loads(_decrypt(row["response_body"]))
    except (ValueError, KeyError, InvalidTag, json.JSONDecodeError):
        return False
    if not isinstance(current, dict) or current.get(_IN_PROGRESS_FIELD) != reservation_id:
        return False
    conn.execute(
        "UPDATE idempotency_keys SET expires_at=? WHERE key=?",
        (
            (datetime.now(UTC) + timedelta(seconds=_RESERVATION_LEASE_SECONDS)).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            key,
        ),
    )
    return True


def release_idempotency_request(
    conn: sqlite3.Connection,
    *,
    key: str,
    reservation_id: str,
) -> bool:
    """Release an unfinished reservation without deleting another owner's result."""
    row = conn.execute("SELECT response_body FROM idempotency_keys WHERE key=?", (key,)).fetchone()
    if row is None:
        return False
    try:
        current = json.loads(_decrypt(row["response_body"]))
    except (ValueError, KeyError, InvalidTag, json.JSONDecodeError):
        return False
    if not isinstance(current, dict) or current.get(_IN_PROGRESS_FIELD) != reservation_id:
        return False
    if current.get("_effects_committed"):
        return False
    return conn.execute("DELETE FROM idempotency_keys WHERE key=?", (key,)).rowcount == 1


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
        "(key, method, path, user_id, body_hash, response_body, expires_at, lifecycle_state) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            key,
            method,
            path,
            user_id,
            body_hash,
            encrypted,
            expires_at.strftime("%Y-%m-%d %H:%M:%S"),
            _STATE_COMPLETED,
        ),
    )


def cleanup_expired_idempotency_keys(conn: sqlite3.Connection) -> int:
    """Purge stale idempotency keys. Returns number of rows deleted."""
    rows = conn.execute(
        "SELECT key, response_body, created_at, lifecycle_state "
        "FROM idempotency_keys WHERE expires_at <= ? LIMIT 1000",
        (datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),),
    ).fetchall()
    now = datetime.now(UTC)
    removed = 0
    for row in rows:
        lifecycle_state = str(row["lifecycle_state"] or _STATE_LEGACY_UNKNOWN)
        if lifecycle_state in {_STATE_COMPLETED, _STATE_IN_PROGRESS}:
            removed += conn.execute(
                "DELETE FROM idempotency_keys WHERE key=?", (row["key"],)
            ).rowcount
            continue
        if lifecycle_state == _STATE_EFFECTS_COMMITTED:
            if _retention_elapsed(row["created_at"], now):
                removed += conn.execute(
                    "DELETE FROM idempotency_keys WHERE key=?", (row["key"],)
                ).rowcount
            continue
        try:
            payload = json.loads(_decrypt(row["response_body"], log_failures=False))
        except (ValueError, KeyError, InvalidTag, json.JSONDecodeError):
            if _retention_elapsed(row["created_at"], now):
                removed += conn.execute(
                    "DELETE FROM idempotency_keys WHERE key=?", (row["key"],)
                ).rowcount
            continue
        if (
            isinstance(payload, dict)
            and payload.get("_effects_committed")
            and not _retention_elapsed(row["created_at"], now)
        ):
            continue
        removed += conn.execute("DELETE FROM idempotency_keys WHERE key=?", (row["key"],)).rowcount
    return removed
