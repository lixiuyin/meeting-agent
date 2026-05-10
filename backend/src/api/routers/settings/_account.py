"""Settings account deletion endpoint (GDPR right-to-erasure)."""

from fastapi import Depends, Header, HTTPException, Request

from ....api.middleware import limiter
from ....core.audit import audit_log
from ....core.security import verify_api_key
from ....models.schemas._common import MessageResponse
from ._common import logger, router


@router.delete("/account", response_model=MessageResponse)
@limiter.limit("1/minute")
async def delete_account(
    request: Request,
    principal: dict = Depends(verify_api_key),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> dict[str, str]:
    """Delete all user data (GDPR right-to-erasure / account deletion).

    Removes meetings, files, chat sessions, memories, and associated vectors
    for the authenticated user. This is irreversible.

    Requires an Idempotency-Key header to prevent accidental duplicate deletions.
    """
    if not idempotency_key:
        raise HTTPException(
            status_code=400,
            detail="Idempotency-Key header is required for account deletion",
        )

    import asyncio

    from ....api.dependencies import IdempotencyGuard
    from ....core import database as db
    from ....services.rag import delete_meeting_chunks
    from ....services.rag._meeting_summary_vectorstore import (
        delete_meeting_summary,
    )

    user_id = principal["user_id"]
    guard = IdempotencyGuard(idempotency_key, request, user_id)

    cached = await guard.check()
    if cached is not None:
        return {"message": cached.get("message", "Account data deleted (idempotent)")}

    def _delete_all():
        with db.get_write_connection() as conn:
            # Collect meeting ids for vector cleanup
            meetings = conn.execute(
                "SELECT id FROM meetings WHERE user_id=?", (user_id,)
            ).fetchall()
            meeting_ids = [m["id"] for m in meetings]

            # Delete chat data
            conn.execute(
                "DELETE FROM chat_messages WHERE session_id IN "
                "(SELECT id FROM chat_sessions WHERE user_id=?)",
                (user_id,),
            )
            conn.execute("DELETE FROM chat_sessions WHERE user_id=?", (user_id,))
            conn.execute("DELETE FROM session_summaries WHERE user_id=?", (user_id,))

            # Delete memories and entities
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

            # Delete meetings (cascades to meeting_files via FK)
            conn.execute("DELETE FROM meetings WHERE user_id=?", (user_id,))

            return meeting_ids

    meeting_ids = await asyncio.to_thread(_delete_all)

    # Clean up vectors
    for meeting_id in meeting_ids:
        try:
            delete_meeting_chunks(meeting_id)
            delete_meeting_summary(meeting_id)
        except Exception:
            logger.warning("Vector cleanup failed for meeting %d", meeting_id, exc_info=True)

    audit_log("delete_account", "user", user_id, user_id=user_id)
    logger.info("Account data deleted for user %s (%d meetings)", user_id, len(meeting_ids))
    result = {"message": f"Account data deleted ({len(meeting_ids)} meetings removed)"}
    await guard.save(result)
    return result
