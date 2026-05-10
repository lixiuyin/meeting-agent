import asyncio
import json
import threading

from ...core import database as db
from ...core.database import get_write_connection
from . import settings
from ._common import logger
from ._parsers import _parse_summary_json
from ._summary_vectorstore import get_summary_vectorstore

_summary_backfill_lock = threading.Lock()
_summary_vector_upsert_lock = threading.Lock()


class SessionSummaryService:
    """Generates and manages session summaries for cross-session episodic memory."""

    async def summarize_session(self, session_id: str, user_id: str) -> dict | None:
        """Generate a structured summary for a session.

        Returns the summary dict or None if summarization fails or session
        doesn't have enough content.
        """
        if not settings.SESSION_SUMMARY_ENABLED:
            return None

        # Load messages for the session
        with db.get_connection() as conn:
            session = db.get_session(conn, session_id)
            if not session:
                return None
            messages = db.get_messages(conn, session_id)

        if len(messages) < settings.SESSION_SUMMARY_MIN_TURNS:
            return None

        # Truncate very long sessions to avoid exceeding LLM context window
        max_msgs = settings.SESSION_SUMMARY_MAX_MESSAGES
        if len(messages) > max_msgs:
            # Keep the first 10 messages (opening context) + most recent tail
            head = messages[:10]
            tail = messages[-(max_msgs - 10) :]
            messages = head + tail

        # Check if summary already exists
        with db.get_connection() as conn:
            existing = db.get_session_summary(conn, session_id)
        if existing:
            return existing

        # Format conversation for the LLM
        conversation_parts = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            conversation_parts.append(f"{role}: {content}")
        conversation = "\n".join(conversation_parts)

        try:
            from ..llm import cached_retry_invoke, get_llm, get_session_summary_prompt

            llm = get_llm()
            prompt_template = get_session_summary_prompt()
            prompt = prompt_template.format(conversation=conversation)
            response = await asyncio.to_thread(cached_retry_invoke, llm, prompt)
            content = response.content
            if isinstance(content, list):
                return None

            result = _parse_summary_json(content)
            if not result:
                logger.warning("Failed to parse session summary JSON")
                return None

            summary_text = result.get("summary", "")
            topics = result.get("topics", [])
            key_entities = result.get("key_entities", [])
            decisions = result.get("decisions", [])

            if not summary_text:
                return None

            # Persist to SQLite first (DB is the source of truth)
            with get_write_connection() as conn:
                db.upsert_session_summary(
                    conn,
                    session_id=session_id,
                    user_id=user_id,
                    summary=summary_text,
                    topics=json.dumps(topics) if topics else None,
                    key_entities=json.dumps(key_entities) if key_entities else None,
                    decisions=json.dumps(decisions) if decisions else None,
                    turn_count=len(messages),
                    embedding_id=None,
                )

            # M-C4: The full read-upsert-backfill sequence must be atomic
            # so that concurrent summarizations of the same session don't
            # interleave and lose embedding_id references.
            # Runs inside asyncio.to_thread so the blocking lock.acquire
            # does not stall the event loop.
            vs = get_summary_vectorstore()

            def _locked_upsert() -> str | None:
                acquired = _summary_vector_upsert_lock.acquire(timeout=30.0)
                if not acquired:
                    logger.warning(
                        "Could not acquire summary vector lock for session %s "
                        "within 30s; skipping vector upsert for this cycle",
                        session_id,
                    )
                    return None
                try:
                    return vs.upsert(session_id, user_id, summary_text, topics)
                finally:
                    _summary_vector_upsert_lock.release()

            try:
                embedding_id = await asyncio.to_thread(_locked_upsert)
            except Exception as e:
                logger.warning("Failed to index session summary: %s", e)
                embedding_id = None

            # Backfill embedding_id into SQLite if vector upsert succeeded
            if embedding_id:
                try:
                    with get_write_connection() as conn:
                        conn.execute(
                            "UPDATE session_summaries SET embedding_id=? WHERE session_id=?",
                            (embedding_id, session_id),
                        )
                except Exception:
                    logger.warning(
                        "Failed to backfill embedding_id for session %s",
                        session_id,
                        exc_info=True,
                    )

            logger.info("Generated summary for session %s (%d messages)", session_id, len(messages))
            return {
                "session_id": session_id,
                "summary": summary_text,
                "topics": topics,
                "key_entities": key_entities,
                "decisions": decisions,
                "turn_count": len(messages),
            }
        except Exception:
            logger.warning("Session summarization failed for %s", session_id, exc_info=True)
            return None

    async def search_sessions(
        self,
        user_id: str,
        query: str,
        limit: int = 5,
        meeting_ids: list[int] | None = None,
    ) -> list[dict]:
        """Semantic search over session summaries for a user.

        Returns session summaries ranked by relevance to the query.
        """
        vs = get_summary_vectorstore()
        vector_results = await asyncio.to_thread(vs.similarity_search, query, user_id, limit)
        if meeting_ids:
            meeting_scope = set(meeting_ids)
            vector_results = [
                r
                for r in vector_results
                if not r.get("meetings_covered")
                or bool(set(r.get("meetings_covered") or []) & meeting_scope)
            ]
            if not vector_results:
                return []

        # Batch-fetch full summary data (N+1 → 1 query)
        sids = [r["session_id"] for r in vector_results]
        with db.get_connection() as conn:
            batch = await asyncio.to_thread(db.get_session_summaries_batch, conn, sids)

        enriched = []
        for r in vector_results:
            sid = r["session_id"]
            full = batch.get(sid)
            if not full:
                continue
            enriched.append(
                {
                    "session_id": sid,
                    "summary": full.get("summary", ""),
                    "topics": json.loads(full["topics"]) if full.get("topics") else [],
                    "key_entities": (
                        json.loads(full["key_entities"]) if full.get("key_entities") else []
                    ),
                    "decisions": json.loads(full["decisions"]) if full.get("decisions") else [],
                    "turn_count": full.get("turn_count", 0),
                    "created_at": full.get("created_at", ""),
                    "score": r["score"],
                }
            )
        return enriched

    def get_recent_summaries(self, user_id: str, limit: int = 5, offset: int = 0) -> list[dict]:
        """Get the most recent session summaries for a user."""
        with db.get_connection() as conn:
            rows = db.list_session_summaries(conn, user_id=user_id, limit=limit, offset=offset)
        result = []
        for row in rows:
            result.append(
                {
                    "session_id": row["session_id"],
                    "summary": row.get("summary", ""),
                    "topics": json.loads(row["topics"]) if row.get("topics") else [],
                    "key_entities": json.loads(row["key_entities"])
                    if row.get("key_entities")
                    else [],
                    "decisions": json.loads(row["decisions"]) if row.get("decisions") else [],
                    "turn_count": row.get("turn_count", 0),
                    "session_title": row.get("session_title", ""),
                    "created_at": row.get("created_at", ""),
                }
            )
        return result

    async def summarize_unsummarized(self, user_id: str | None = None, max_batch: int = 5) -> int:
        """Scan for sessions without summaries and generate them.

        Processes sessions concurrently with bounded parallelism (M-MEM-4)
        instead of serially, so multiple LLM summarization calls can overlap.
        Returns count of sessions summarized.
        """

        def _acquire_with_timeout() -> bool:
            return _summary_backfill_lock.acquire(timeout=30.0)

        acquired = await asyncio.to_thread(_acquire_with_timeout)
        if not acquired:
            logger.warning("Could not acquire summary backfill lock; skipping this cycle")
            return 0
        try:
            with db.get_connection() as conn:
                unsummarized = db.get_unsummarized_sessions(
                    conn,
                    user_id=user_id,
                    min_messages=settings.SESSION_SUMMARY_MIN_TURNS,
                )
            if max_batch > 0:
                unsummarized = unsummarized[:max_batch]

            if not unsummarized:
                return 0

            # M-MEM-4: Bounded concurrent summarization.
            _sem = asyncio.Semaphore(3)

            async def _summarize_one(session: dict) -> bool:
                async with _sem:
                    try:
                        result = await self.summarize_session(session["id"], session["user_id"])
                        return result is not None
                    except Exception:
                        logger.warning("Summarize session %s failed", session["id"], exc_info=True)
                        return False

            results = await asyncio.gather(
                *[_summarize_one(s) for s in unsummarized], return_exceptions=True
            )
            count = sum(1 for r in results if r is True)
            if count:
                logger.info("Generated %d session summaries", count)
            return count
        finally:
            _summary_backfill_lock.release()

    async def summarize_idle_sessions(self, user_id: str | None = None, max_batch: int = 5) -> int:
        """Summarize sessions that have been idle beyond the configured threshold.

        Selects sessions whose last message is older than
        ``SESSION_SUMMARY_IDLE_MINUTES`` and that don't already have a summary.

        Returns count of sessions summarized.
        """
        idle_minutes = settings.SESSION_SUMMARY_IDLE_MINUTES
        cutoff_sql = f"datetime('now', '-{idle_minutes} minutes')"

        with db.get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT cs.id, cs.user_id
                FROM chat_sessions cs
                JOIN chat_messages cm ON cs.id = cm.session_id
                LEFT JOIN session_summaries ss ON cs.id = ss.session_id
                WHERE ss.id IS NULL
                  AND cs.updated_at < {cutoff_sql}
                GROUP BY cs.id
                HAVING COUNT(cm.id) >= ?
                ORDER BY cs.updated_at DESC
                """,
                (settings.SESSION_SUMMARY_MIN_TURNS,),
            ).fetchall()

        def _acquire_with_timeout() -> bool:
            return _summary_backfill_lock.acquire(timeout=30.0)

        acquired = await asyncio.to_thread(_acquire_with_timeout)
        if not acquired:
            logger.warning("Could not acquire summary backfill lock; skipping this cycle")
            return 0
        try:
            if max_batch > 0:
                rows = rows[:max_batch]
            count = 0
            for idx, row in enumerate(rows):
                result = await self.summarize_session(row["id"], row["user_id"])
                if result:
                    count += 1
                if idx < len(rows) - 1:
                    await asyncio.sleep(0)
            if count:
                logger.info("Auto-summarized %d idle sessions", count)
            return count
        finally:
            _summary_backfill_lock.release()

    async def search_past_conversations(
        self,
        user_id: str,
        query: str,
        limit: int = 10,
    ) -> list[dict]:
        """Combined search: FTS5 full-text + session summary semantic search.

        Returns a merged and deduplicated list of results.
        """

        # Run both searches in parallel
        async def _fts_search() -> list[dict]:
            with db.get_connection() as conn:
                return db.search_chat_messages(conn, user_id=user_id, query=query, limit=limit)

        async def _semantic_search() -> list[dict]:
            return await self.search_sessions(user_id, query, limit=limit)

        fts_results, semantic_results = await asyncio.gather(_fts_search(), _semantic_search())

        # Merge: session summaries first (higher-level context), then individual messages
        seen_sessions = set()
        merged = []

        for s in semantic_results:
            sid = s["session_id"]
            if sid not in seen_sessions:
                seen_sessions.add(sid)
                merged.append(
                    {
                        "type": "session_summary",
                        "session_id": sid,
                        "summary": s["summary"],
                        "topics": s.get("topics", []),
                        "created_at": s.get("created_at", ""),
                    }
                )

        for m in fts_results:
            merged.append(
                {
                    "type": "message",
                    "session_id": m["session_id"],
                    "session_title": m.get("session_title", ""),
                    "role": m["role"],
                    "content": m["content"],
                    "created_at": m.get("created_at", ""),
                }
            )

        return merged[:limit]
