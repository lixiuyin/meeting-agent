"""Shared FastAPI dependencies: pagination, idempotency, etc."""

import asyncio
import base64
import binascii
import hashlib
import uuid

from fastapi import Header, HTTPException, Request

from ..core import database as db
from ..core.database import get_write_connection
from ..core.idempotency_context import activate, deactivate, internal_operation

MAX_PAGE_OFFSET = 10000


def encode_cursor(offset: int) -> str:
    """Encode an integer offset into a URL-safe base64 cursor."""
    return base64.urlsafe_b64encode(str(offset).encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> int:
    """Reject malformed/unbounded cursors before binding SQLite integers."""
    if not cursor:
        return 0
    try:
        if len(cursor) > 32:
            raise ValueError("Cursor is too long")
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded = base64.b64decode(padded, altchars=b"-_", validate=True).decode()
        if not decoded.isascii() or not decoded.isdecimal():
            raise ValueError("Cursor must contain a non-negative integer")
        offset = int(decoded)
        if offset > MAX_PAGE_OFFSET:
            raise ValueError("Cursor exceeds pagination limit")
        return offset
    except (ValueError, TypeError, binascii.Error) as exc:
        raise HTTPException(status_code=400, detail="Invalid pagination cursor") from exc


class IdempotencyGuard:
    """Helper to check and save idempotent responses for mutating endpoints."""

    def __init__(
        self,
        key: str | None,
        request: Request,
        user_id: str,
        *,
        body_hash: str | None = None,
    ):
        self.key = key
        self.request = request
        self.method = request.method
        # Use the ASGI scope path, which cannot be reinterpreted through a
        # malformed Host header by URL reconstruction.
        self.path = str(request.scope.get("path") or "/")
        self.user_id = user_id
        # The database schema historically used the caller-provided key as a
        # global primary key. Namespace it before persistence so identical
        # client keys from different users or endpoints cannot overwrite one
        # another's cached response.
        namespace = f"{user_id}\0{self.method}\0{self.path}\0{key or ''}"
        self._storage_key = hashlib.sha256(namespace.encode()).hexdigest()
        # Multipart requests are parsed by FastAPI before the endpoint runs,
        # so calling ``request.body()`` from the endpoint would raise
        # ``RuntimeError: Stream consumed``.  Callers that stream a body can
        # provide a stable semantic fingerprint instead.
        self._body_hash = body_hash
        self._reservation_id: str | None = None
        self._renewal_task: asyncio.Task[None] | None = None
        request.state.idempotency_guard = self

    async def _renew_reservation(self, reservation_id: str) -> None:
        while self._reservation_id == reservation_id:
            await asyncio.sleep(10)

            def _renew() -> bool:
                with internal_operation(), get_write_connection() as conn:
                    return db.renew_idempotency_request(
                        conn,
                        key=self._storage_key,
                        reservation_id=reservation_id,
                    )

            if not await asyncio.to_thread(_renew):
                return

    async def _get_body_hash(self) -> str | None:
        if self._body_hash is not None:
            return self._body_hash
        if self.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return None
        body = await self.request.body()
        self._body_hash = hashlib.sha256(body).hexdigest()
        return self._body_hash

    async def check(self) -> dict | None:
        """Reserve this mutation or return the response of its first execution."""
        if not self.key:
            return None
        key = self._storage_key
        body_hash = await self._get_body_hash()
        reservation_id = uuid.uuid4().hex
        for _attempt in range(600):

            def _claim():
                with internal_operation(), get_write_connection() as conn:
                    return db.claim_idempotency_request(
                        conn,
                        key=key,
                        method=self.method,
                        path=self.path,
                        user_id=self.user_id,
                        body_hash=body_hash,
                        reservation_id=reservation_id,
                    )

            state, result, token = await asyncio.to_thread(_claim)
            if state == "owner":
                self._reservation_id = token
                if token is not None:
                    self._renewal_task = asyncio.create_task(
                        self._renew_reservation(token),
                        name=f"idempotency-lease:{key[:12]}",
                    )
                    activate(key, token)
                return None
            if state == "completed":
                if result is not None:
                    result.pop("_epoch", None)  # compatibility with legacy entries
                return result
            if state == "conflict":
                raise HTTPException(
                    status_code=409,
                    detail="Idempotency-Key was already used with a different request",
                )
            if state == "recovery_required":
                raise HTTPException(
                    409,
                    "Operation committed without a replayable response; reconciliation required",
                )
            await asyncio.sleep(0.05)
        raise HTTPException(status_code=409, detail="Idempotent request is still in progress")

    async def save(self, response_body: dict) -> None:
        """Cache the response body for this idempotency key (24h TTL)."""
        if not self.key:
            return
        key = self._storage_key
        body_hash = await self._get_body_hash()

        def _save():
            with internal_operation(), get_write_connection() as conn:
                if self._reservation_id is not None:
                    return db.complete_idempotency_request(
                        conn,
                        key=key,
                        reservation_id=self._reservation_id,
                        response_body=response_body,
                    )
                db.save_idempotency_response(
                    conn,
                    key,
                    self.method,
                    self.path,
                    self.user_id,
                    response_body,
                    body_hash,
                )
                return True

        completed = await asyncio.to_thread(_save)
        if not completed:
            raise RuntimeError("Idempotency reservation ownership was lost")
        self._reservation_id = None
        deactivate(self._storage_key)
        if self._renewal_task is not None:
            self._renewal_task.cancel()
            await asyncio.gather(self._renewal_task, return_exceptions=True)
            self._renewal_task = None

    async def abandon(self) -> None:
        """Release an unfinished reservation after an endpoint exits without saving."""
        reservation_id = self._reservation_id
        deactivate(self._storage_key)
        if not self.key or reservation_id is None:
            return
        if self._renewal_task is not None:
            self._renewal_task.cancel()
            await asyncio.gather(self._renewal_task, return_exceptions=True)
            self._renewal_task = None

        def _release() -> bool:
            with internal_operation(), get_write_connection() as conn:
                return db.release_idempotency_request(
                    conn,
                    key=self._storage_key,
                    reservation_id=reservation_id,
                )

        try:
            await asyncio.to_thread(_release)
        finally:
            if self._reservation_id == reservation_id:
                self._reservation_id = None

    async def invalidate(self) -> None:
        """Delete the cached response for this idempotency key (e.g. stale cache)."""
        if not self.key:
            return
        key = self._storage_key

        def _delete():
            with internal_operation(), get_write_connection() as conn:
                conn.execute(
                    "DELETE FROM idempotency_keys WHERE key IN (?, ?)",
                    (key, self.key),
                )

        await asyncio.to_thread(_delete)

    def save_in_transaction(self, conn, response_body: dict) -> None:
        """Atomically bind the replay response to the caller's SQL mutation."""
        if not self.key:
            return
        if self._reservation_id is None or not db.complete_idempotency_request(
            conn,
            key=self._storage_key,
            reservation_id=self._reservation_id,
            response_body=response_body,
        ):
            raise RuntimeError("Idempotency reservation ownership was lost")

    async def finish_transaction(self) -> None:
        """Stop renewal only after the business transaction actually committed."""
        self._reservation_id = None
        deactivate(self._storage_key)
        if self._renewal_task is not None:
            self._renewal_task.cancel()
            await asyncio.gather(self._renewal_task, return_exceptions=True)
            self._renewal_task = None


async def idempotency_key_header(
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> str | None:
    return idempotency_key
