"""Settings account deletion endpoints (GDPR right-to-erasure)."""

import asyncio
import hashlib
import uuid

from fastapi import Depends, Header, HTTPException, Request, status
from pydantic import BaseModel

from ....api.middleware import limiter
from ....core.audit import audit_log
from ....core.security import verify_api_key
from ._common import logger, router


class AccountDeletionResponse(BaseModel):
    deletion_batch_id: str
    status: str
    message: str
    total_jobs: int
    pending_jobs: int
    dead_letter_jobs: int


def _deletion_status(batch_id: str, user_id: str) -> AccountDeletionResponse:
    from ....core import database as db

    with db.get_connection() as conn:
        request_row = conn.execute(
            "SELECT total_jobs FROM account_deletion_requests WHERE id=? AND user_id=?",
            (batch_id, user_id),
        ).fetchone()
        if not request_row:
            raise HTTPException(404, "Deletion request not found")
        counts = {
            row["status"]: row["count"]
            for row in conn.execute(
                "SELECT COALESCE(status, 'pending') AS status, COUNT(*) AS count "
                "FROM pending_vector_deletions WHERE deletion_batch_id=? "
                "GROUP BY COALESCE(status, 'pending')",
                (batch_id,),
            ).fetchall()
        }
    pending = int(counts.get("pending", 0))
    dead = int(counts.get("dead_letter", 0))
    status_value = "failed" if dead else ("pending" if pending else "completed")
    return AccountDeletionResponse(
        deletion_batch_id=batch_id,
        status=status_value,
        message={
            "pending": "Account data erased from the primary database; external cleanup pending",
            "failed": "Account data erased from the primary database; external cleanup needs retry",
            "completed": "Account data deletion completed",
        }[status_value],
        total_jobs=int(request_row["total_jobs"]),
        pending_jobs=pending,
        dead_letter_jobs=dead,
    )


@router.delete(
    "/account",
    response_model=AccountDeletionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
@limiter.limit("1/minute")
async def delete_account(
    request: Request,
    principal: dict = Depends(verify_api_key),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> AccountDeletionResponse:
    """Erase primary data and durably schedule deletion of external resources."""
    if not idempotency_key:
        raise HTTPException(400, "Idempotency-Key header is required for account deletion")

    from ....core import database as db
    from ....core.config import settings

    user_id = principal["user_id"]
    idempotency_hash = hashlib.sha256(f"{user_id}:{idempotency_key}".encode()).hexdigest()

    def _delete_all() -> tuple[int, str, bool]:
        with db.get_write_connection() as conn:
            existing = conn.execute(
                "SELECT id FROM account_deletion_requests "
                "WHERE idempotency_key_hash=? AND user_id=?",
                (idempotency_hash, user_id),
            ).fetchone()
            if existing:
                return 0, str(existing["id"]), True

            batch_id = uuid.uuid4().hex
            meetings = conn.execute(
                "SELECT id FROM meetings WHERE user_id=?", (user_id,)
            ).fetchall()
            meeting_ids = [m["id"] for m in meetings]
            file_paths = conn.execute(
                "SELECT mf.file_path FROM meeting_files mf "
                "JOIN meetings m ON m.id=mf.meeting_id WHERE m.user_id=?",
                (user_id,),
            ).fetchall()
            session_vector_ids = conn.execute(
                "SELECT embedding_id FROM session_summaries "
                "WHERE user_id=? AND embedding_id IS NOT NULL",
                (user_id,),
            ).fetchall()
            memory_vector_ids = conn.execute(
                "SELECT embedding_id FROM user_memories "
                "WHERE user_id=? AND embedding_id IS NOT NULL",
                (user_id,),
            ).fetchall()
            entity_vector_ids = conn.execute(
                "SELECT embedding_id FROM memory_entities "
                "WHERE user_id=? AND embedding_id IS NOT NULL",
                (user_id,),
            ).fetchall()

            jobs: list[tuple[str, str]] = []
            jobs.extend(("meeting", str(mid)) for mid in meeting_ids)
            jobs.extend(("file", str(row["file_path"])) for row in file_paths)
            jobs.extend(
                ("directory", str(settings.UPLOAD_DIR / "meeting_assets" / str(mid)))
                for mid in meeting_ids
            )
            jobs.extend(("session_summary", str(row["embedding_id"])) for row in session_vector_ids)
            jobs.extend(("memory", str(row["embedding_id"])) for row in memory_vector_ids)
            jobs.extend(("entity", str(row["embedding_id"])) for row in entity_vector_ids)
            jobs = list(dict.fromkeys(jobs))

            conn.execute(
                "INSERT INTO account_deletion_requests "
                "(id, idempotency_key_hash, total_jobs, user_id) VALUES (?, ?, ?, ?)",
                (batch_id, idempotency_hash, len(jobs), user_id),
            )
            conn.executemany(
                "INSERT INTO pending_vector_deletions "
                "(collection, embedding_id, deletion_batch_id) VALUES (?, ?, ?) "
                "ON CONFLICT(collection, embedding_id) DO UPDATE SET "
                "deletion_batch_id=excluded.deletion_batch_id, status='pending', "
                "attempts=0, last_error=NULL, lease_owner=NULL, lease_expires_at=NULL, "
                "updated_at=CURRENT_TIMESTAMP",
                [(collection, resource_id, batch_id) for collection, resource_id in jobs],
            )

            conn.execute(
                "DELETE FROM chat_messages WHERE session_id IN "
                "(SELECT id FROM chat_sessions WHERE user_id=?)",
                (user_id,),
            )
            conn.execute("DELETE FROM chat_sessions WHERE user_id=?", (user_id,))
            conn.execute("DELETE FROM session_summaries WHERE user_id=?", (user_id,))
            conn.execute(
                "DELETE FROM memory_scopes WHERE memory_id IN "
                "(SELECT id FROM user_memories WHERE user_id=?)",
                (user_id,),
            )
            conn.execute("DELETE FROM user_memories WHERE user_id=?", (user_id,))
            conn.execute(
                "DELETE FROM entity_scopes WHERE entity_id IN "
                "(SELECT id FROM memory_entities WHERE user_id=?)",
                (user_id,),
            )
            conn.execute("DELETE FROM memory_relations WHERE user_id=?", (user_id,))
            conn.execute("DELETE FROM memory_entities WHERE user_id=?", (user_id,))
            conn.execute("DELETE FROM meetings WHERE user_id=?", (user_id,))
            conn.execute("DELETE FROM idempotency_keys WHERE user_id=?", (user_id,))
            return len(meeting_ids), batch_id, False

    meeting_count, batch_id, replayed = await asyncio.to_thread(_delete_all)

    from ....services.memory._service._crud import cleanup_pending_vector_deletions

    try:
        await asyncio.to_thread(cleanup_pending_vector_deletions, deletion_batch_id=batch_id)
    except Exception:
        logger.warning("Immediate account cleanup failed; batch remains queued", exc_info=True)
    audit_log("delete_account", "user", "erased", user_id="erased")
    logger.info(
        "Account data deletion scheduled (%d meetings, replayed=%s)", meeting_count, replayed
    )
    return await asyncio.to_thread(_deletion_status, batch_id, user_id)


@router.get("/account/deletions/{batch_id}", response_model=AccountDeletionResponse)
async def get_account_deletion_status(
    batch_id: str,
    principal: dict = Depends(verify_api_key),
) -> AccountDeletionResponse:
    """Return external-cleanup status for an account erasure request."""
    return await asyncio.to_thread(_deletion_status, batch_id, principal["user_id"])


@router.post("/account/deletions/{batch_id}/retry", response_model=AccountDeletionResponse)
@limiter.limit("5/minute")
async def retry_account_deletion(
    request: Request,
    batch_id: str,
    principal: dict = Depends(verify_api_key),
) -> AccountDeletionResponse:
    """Requeue failed external deletions for an erasure batch and retry them."""
    from ....core import database as db
    from ....services.memory._service._crud import cleanup_pending_vector_deletions

    user_id = principal["user_id"]

    def _requeue() -> None:
        with db.get_write_connection() as conn:
            exists = conn.execute(
                "SELECT 1 FROM account_deletion_requests WHERE id=? AND user_id=?",
                (batch_id, user_id),
            ).fetchone()
            if not exists:
                raise HTTPException(404, "Deletion request not found")
            conn.execute(
                "UPDATE pending_vector_deletions SET status='pending', attempts=0, "
                "last_error=NULL, updated_at=CURRENT_TIMESTAMP "
                "WHERE deletion_batch_id=? AND status='dead_letter'",
                (batch_id,),
            )

    await asyncio.to_thread(_requeue)
    try:
        await asyncio.to_thread(cleanup_pending_vector_deletions, deletion_batch_id=batch_id)
    except Exception:
        logger.warning(
            "Immediate account cleanup retry failed; batch remains queued", exc_info=True
        )
    return await asyncio.to_thread(_deletion_status, batch_id, user_id)
