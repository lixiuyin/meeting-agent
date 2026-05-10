"""Shared FastAPI dependencies: pagination, idempotency, etc."""

import asyncio
import base64
import hashlib

from fastapi import Header, Request

from ..core import database as db
from ..core.database import get_connection, get_write_connection


def encode_cursor(offset: int) -> str:
    """Encode an integer offset into a URL-safe base64 cursor."""
    return base64.urlsafe_b64encode(str(offset).encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> int:
    """Decode a base64 cursor back to an integer offset; returns 0 on invalid input."""
    if not cursor:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded = base64.urlsafe_b64decode(padded).decode()
        return int(decoded)
    except (ValueError, TypeError):
        return 0


class IdempotencyGuard:
    """Helper to check and save idempotent responses for mutating endpoints."""

    def __init__(self, key: str | None, request: Request, user_id: str):
        self.key = key
        self.request = request
        self.method = request.method
        self.path = request.url.path
        self.user_id = user_id
        self._body_hash: str | None = None

    async def _get_body_hash(self) -> str | None:
        if self._body_hash is not None:
            return self._body_hash
        if self.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return None
        body = await self.request.body()
        self._body_hash = hashlib.sha256(body).hexdigest()
        return self._body_hash

    async def check(self) -> dict | None:
        """Return cached response if the idempotency key exists and is valid.

        Invalidates cached responses from a prior settings epoch so config
        changes (model swap, embedding dimension, etc.) are not masked by
        stale idempotent replays (H-11).
        """
        if not self.key:
            return None
        key = self.key
        body_hash = await self._get_body_hash()

        def _fetch():
            with get_connection() as conn:
                return db.get_idempotency_response(
                    conn, key, self.method, self.path, self.user_id, body_hash
                )

        result = await asyncio.to_thread(_fetch)
        if result is None:
            return None

        # H-11: epoch mismatch → treat as cache miss. The epoch is stored
        # inside the encrypted payload as ``_epoch`` by ``save()`` below.
        from ..core.settings_epoch import get_settings_epoch

        stored_epoch = result.pop("_epoch", None)
        if stored_epoch is not None and stored_epoch != get_settings_epoch():
            return None

        return result

    async def save(self, response_body: dict) -> None:
        """Cache the response body for this idempotency key (24h TTL)."""
        if not self.key:
            return
        key = self.key
        body_hash = await self._get_body_hash()

        # H-11: embed current epoch so check() can detect stale replays.
        from ..core.settings_epoch import get_settings_epoch

        payload = {**response_body, "_epoch": get_settings_epoch()}

        def _save():
            with get_write_connection() as conn:
                db.save_idempotency_response(
                    conn,
                    key,
                    self.method,
                    self.path,
                    self.user_id,
                    payload,
                    body_hash,
                )

        await asyncio.to_thread(_save)

    async def invalidate(self) -> None:
        """Delete the cached response for this idempotency key (e.g. stale cache)."""
        if not self.key:
            return
        key = self.key

        def _delete():
            with get_write_connection() as conn:
                conn.execute(
                    "DELETE FROM idempotency_keys WHERE key = ?",
                    (key,),
                )

        await asyncio.to_thread(_delete)


async def idempotency_key_header(
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> str | None:
    return idempotency_key
