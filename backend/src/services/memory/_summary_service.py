import asyncio
import json
import threading

from ...core import database as db
from ...core.database import get_write_connection
from ...core.metrics import SESSION_SUMMARY_SEARCH_TOTAL
from . import settings
from ._common import logger, scope_from_messages
from ._parsers import _parse_summary_json
from ._summary_vectorstore import get_summary_vectorstore, summary_vector_write_lock

_summary_backfill_lock = threading.Lock()
_SESSION_SEARCH_RRF_K = 60
_SESSION_SEARCH_OVERSAMPLE = 5


def _merge_session_candidates(
    vector_results: list[dict],
    fts_results: list[dict],
    *,
    limit: int,
) -> list[dict]:
    """Fuse semantic summaries and exact chat hits with deterministic RRF.

    FTS may return several messages from one session. Each session contributes
    only its best lexical rank, so verbose conversations cannot crowd out other
    episodic memories. Fused scores are normalized to [0, 1] for the existing
    response contract; they remain ranking signals, not probabilities.
    """
    fused: dict[str, dict] = {}

    def _add(session_id: str, rank: int, source: dict | None = None) -> None:
        if not session_id:
            return
        candidate = fused.setdefault(
            session_id,
            {
                "session_id": session_id,
                "score": 0.0,
                "meetings_covered": None,
                "files_covered": None,
            },
        )
        candidate["score"] += 1.0 / (_SESSION_SEARCH_RRF_K + rank)
        if source:
            candidate["meetings_covered"] = source.get("meetings_covered")
            candidate["files_covered"] = source.get("files_covered")

    for rank, result in enumerate(vector_results, start=1):
        _add(str(result.get("session_id", "")), rank, result)

    seen_fts: set[str] = set()
    for rank, result in enumerate(fts_results, start=1):
        session_id = str(result.get("session_id", ""))
        if not session_id or session_id in seen_fts:
            continue
        seen_fts.add(session_id)
        _add(session_id, rank)

    ranked = sorted(
        fused.values(),
        key=lambda item: (-float(item["score"]), str(item["session_id"])),
    )[: max(limit, 0)]
    max_score = max((float(item["score"]) for item in ranked), default=0.0)
    if max_score > 0:
        for item in ranked:
            item["score"] = float(item["score"]) / max_score
    return ranked


class SessionSummaryService:
    """Generates and manages session summaries for cross-session episodic memory."""

    async def summarize_session(
        self, session_id: str, user_id: str, *, force: bool = False
    ) -> dict | None:
        """Generate a structured summary for a session.

        Returns the summary dict or None if summarization fails or session
        doesn't have enough content.
        """
        if not force and not settings.SESSION_SUMMARY_ENABLED:
            return None

        def _load_session() -> tuple[dict | None, list[dict]]:
            with db.get_connection() as conn:
                session = db.get_session(conn, session_id)
                messages = db.get_messages(conn, session_id) if session else []
                return session, messages

        session, all_messages = await asyncio.to_thread(_load_session)
        if not session:
            return None

        message_count = len(all_messages)
        if message_count < settings.SESSION_SUMMARY_MIN_TURNS:
            return None

        # Existing summaries are valid only through their recorded message
        # count.  A session can resume after being idle, so a summary is not a
        # permanent terminal state.
        def _load_existing() -> dict | None:
            with db.get_connection() as conn:
                return db.get_session_summary(conn, session_id, user_id=user_id)

        existing = await asyncio.to_thread(_load_existing)
        summarized_count = int((existing or {}).get("turn_count") or 0)
        if existing and summarized_count >= message_count:
            return existing

        if existing and summarized_count > 0:
            messages = all_messages[summarized_count:]
        else:
            messages = list(all_messages)

        # Truncate very long sessions to avoid exceeding LLM context window
        max_msgs = settings.SESSION_SUMMARY_MAX_MESSAGES
        if len(messages) > max_msgs:
            # Keep the first 10 messages (opening context) + most recent tail
            head = messages[:10]
            tail = messages[-(max_msgs - 10) :]
            messages = head + tail

        # Format conversation for the LLM
        conversation_parts = []
        if existing:
            conversation_parts.append("previous_summary: " + str(existing.get("summary", "")))
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            conversation_parts.append(f"{role}: {content}")
        conversation = "\n".join(conversation_parts)

        try:
            from ..llm import (
                cached_retry_invoke,
                escape_prompt_data,
                get_llm,
                get_session_summary_prompt,
            )

            llm = get_llm()
            prompt_template = get_session_summary_prompt()
            prompt = prompt_template.format(conversation=escape_prompt_data(conversation))
            response = await asyncio.to_thread(cached_retry_invoke, llm, prompt)
            content = response.content
            result = _parse_summary_json(content) if isinstance(content, str) else None
            if not result or not result.get("summary"):
                # Provider-level retry handles transport failures, but a model
                # can still return a successful, malformed response. A distinct
                # corrective prompt bypasses the response cache and gives this
                # structured-output boundary one bounded recovery attempt.
                previous = content if isinstance(content, str) else str(content)
                repair_prompt = (
                    f"{prompt}\n\n"
                    "The previous response was not a valid session-summary JSON object. "
                    "Return only one JSON object with a non-empty string field `summary` and "
                    "array fields `topics`, `key_entities`, and `decisions`. "
                    f"<previous_response>{escape_prompt_data(previous[:4000])}"
                    "</previous_response>"
                )
                repair_response = await asyncio.to_thread(
                    cached_retry_invoke,
                    llm,
                    repair_prompt,
                )
                repair_content = repair_response.content
                result = (
                    _parse_summary_json(repair_content) if isinstance(repair_content, str) else None
                )
            if not result or not result.get("summary"):
                logger.warning("Failed to parse session summary JSON after corrective retry")
                return None

            summary_text = result.get("summary", "")
            topics = result.get("topics", [])
            key_entities = result.get("key_entities", [])
            decisions = result.get("decisions", [])

            # SQLite is authoritative. The monotonic upsert rejects a late
            # result if another task has already summarized more messages.
            def _persist_summary() -> None:
                with get_write_connection() as conn:
                    db.upsert_session_summary(
                        conn,
                        session_id=session_id,
                        user_id=user_id,
                        summary=summary_text,
                        topics=json.dumps(topics) if topics else None,
                        key_entities=json.dumps(key_entities) if key_entities else None,
                        decisions=json.dumps(decisions) if decisions else None,
                        turn_count=message_count,
                        embedding_id=None,
                    )

            def _persist_and_index() -> str | None:
                """Publish one summary version without interleaving vector writers."""
                vector_lock = summary_vector_write_lock()
                acquired = vector_lock.acquire(timeout=30.0)
                if not acquired:
                    # Preserve the authoritative SQL summary even if a slow
                    # vector operation holds the lock. Startup/periodic repair
                    # will fill the intentionally missing embedding later.
                    _persist_summary()
                    logger.warning(
                        "Could not acquire summary vector lock for session %s "
                        "within 30s; skipping vector upsert for this cycle",
                        session_id,
                    )
                    return None
                try:
                    # Keep SQL version selection, Chroma replacement, and the
                    # guarded embedding-id backfill in one in-process critical
                    # section. This is the consistency boundary used by live
                    # summarization and background vector repair.
                    _persist_summary()

                    # A slower LLM call may have loaded fewer messages but
                    # finish after a newer summary. The SQL upsert rejects that
                    # stale row; re-check the authoritative version under the
                    # vector lock so the stale task cannot overwrite the shared
                    # deterministic Chroma document afterwards.
                    with db.get_connection() as conn:
                        current = db.get_session_summary(conn, session_id, user_id=user_id)
                    if (
                        not current
                        or int(current.get("turn_count") or 0) != message_count
                        or str(current.get("summary") or "") != summary_text
                    ):
                        logger.info(
                            "Skipping stale session-summary vector write for %s "
                            "(candidate_turns=%d, current_turns=%d)",
                            session_id,
                            message_count,
                            int((current or {}).get("turn_count") or 0),
                        )
                        return None
                    meetings_covered, files_covered = scope_from_messages(all_messages)
                    embedding_id = get_summary_vectorstore().upsert(
                        session_id,
                        user_id,
                        summary_text,
                        topics,
                        meetings_covered=meetings_covered,
                        files_covered=files_covered,
                    )
                    # Compare-and-set fields ensure this reference is attached
                    # only to the exact summary version that was embedded.
                    with get_write_connection() as conn:
                        conn.execute(
                            "UPDATE session_summaries SET embedding_id=? "
                            "WHERE session_id=? AND user_id=? AND turn_count=? AND summary=?",
                            (
                                embedding_id,
                                session_id,
                                user_id,
                                message_count,
                                summary_text,
                            ),
                        )
                    return embedding_id
                finally:
                    vector_lock.release()

            try:
                await asyncio.to_thread(_persist_and_index)
            except Exception as e:
                logger.warning("Failed to index session summary: %s", e, exc_info=True)

            # Return the authoritative row rather than this task's local LLM
            # output. A concurrent newer summarization may have won while this
            # task was indexing or backfilling.
            canonical = await asyncio.to_thread(_load_existing)
            if canonical:
                logger.info(
                    "Generated summary for session %s (%d messages)",
                    session_id,
                    int(canonical.get("turn_count") or 0),
                )
                return {
                    "session_id": session_id,
                    "summary": canonical.get("summary", ""),
                    "topics": json.loads(canonical["topics"]) if canonical.get("topics") else [],
                    "key_entities": (
                        json.loads(canonical["key_entities"])
                        if canonical.get("key_entities")
                        else []
                    ),
                    "decisions": (
                        json.loads(canonical["decisions"]) if canonical.get("decisions") else []
                    ),
                    "turn_count": int(canonical.get("turn_count") or 0),
                }
            return None
        except Exception:
            logger.warning("Session summarization failed for %s", session_id, exc_info=True)
            return None

    async def search_sessions(
        self,
        user_id: str,
        query: str,
        limit: int = 5,
        meeting_ids: list[int] | None = None,
        file_ids: list[int] | None = None,
    ) -> list[dict]:
        """Hybrid search over session summaries and authoritative chat FTS.

        Vector retrieval supplies semantic recall; tenant-scoped FTS5 supplies
        exact-name/number recall and remains available when Chroma is degraded.
        The two ranked lists are fused by session identity with RRF.
        """
        fetch_limit = max(limit * _SESSION_SEARCH_OVERSAMPLE, limit)

        def _vector_search() -> list[dict]:
            vs = get_summary_vectorstore()
            return vs.similarity_search(
                query,
                user_id,
                fetch_limit,
                raise_on_error=True,
            )

        def _fts_search() -> list[dict]:
            with db.get_connection() as conn:
                return db.search_chat_messages(
                    conn,
                    user_id=user_id,
                    query=query,
                    limit=fetch_limit,
                )

        vector_outcome, fts_outcome = await asyncio.gather(
            asyncio.to_thread(_vector_search),
            asyncio.to_thread(_fts_search),
            return_exceptions=True,
        )
        vector_failed = isinstance(vector_outcome, BaseException)
        fts_failed = isinstance(fts_outcome, BaseException)
        vector_results = [] if vector_failed else vector_outcome
        fts_results = [] if fts_failed else fts_outcome

        if vector_failed:
            logger.warning("Session-summary vector search degraded", exc_info=vector_outcome)
        if fts_failed:
            logger.warning("Session-summary FTS search degraded", exc_info=fts_outcome)

        if vector_results and fts_results:
            search_path = "hybrid"
        elif vector_results:
            search_path = "vector_only"
        elif fts_results:
            search_path = "fts_fallback" if vector_failed else "fts_only"
        elif vector_failed and fts_failed:
            search_path = "error"
        elif vector_failed:
            search_path = "fts_fallback"
        else:
            search_path = "empty"
        SESSION_SUMMARY_SEARCH_TOTAL.labels(path=search_path).inc()

        vector_results = _merge_session_candidates(
            vector_results,
            fts_results,
            limit=limit,
        )
        if meeting_ids or file_ids:
            meeting_scope = set(meeting_ids or [])
            file_scope = set(file_ids or [])
            strict = settings.SCOPED_MEMORY_STRICT

            # Older summary vectors predate scope metadata. Recover provenance
            # from authoritative message sources before applying strict mode.
            missing_scope = [
                str(result["session_id"])
                for result in vector_results
                if (
                    not result.get("meetings_covered")
                    or (file_scope and not result.get("files_covered"))
                )
                and result.get("session_id")
            ]
            if missing_scope:

                def _recover_scopes() -> dict[str, tuple[list[int] | None, list[int] | None]]:
                    with db.get_connection() as conn:
                        return {
                            sid: scope_from_messages(db.get_messages(conn, sid))
                            for sid in missing_scope
                        }

                recovered = await asyncio.to_thread(_recover_scopes)
                for result in vector_results:
                    sid = result.get("session_id")
                    if isinstance(sid, str) and recovered.get(sid):
                        meetings, files = recovered[sid]
                        result["meetings_covered"] = meetings
                        result["files_covered"] = files
            vector_results = [
                r
                for r in vector_results
                if (
                    (
                        bool(set(r.get("files_covered") or []) & file_scope)
                        if file_scope
                        else bool(set(r.get("meetings_covered") or []) & meeting_scope)
                    )
                    or (
                        not strict
                        and not (
                            r.get("files_covered") if file_scope else r.get("meetings_covered")
                        )
                    )
                )
            ]
            if not vector_results:
                return []

        # Batch-fetch full summary data (N+1 → 1 query)
        sids = [r["session_id"] for r in vector_results]

        def _load_batch() -> dict[str, dict]:
            with db.get_connection() as conn:
                return db.get_session_summaries_batch(conn, sids)

        batch = await asyncio.to_thread(_load_batch)

        enriched = []
        for r in vector_results:
            sid = r["session_id"]
            full = batch.get(sid)
            if not full:
                continue
            enriched.append(
                {
                    "session_id": sid,
                    "session_title": full.get("session_title", ""),
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

            def _load_unsummarized() -> list[dict]:
                with db.get_connection() as conn:
                    return db.get_unsummarized_sessions(
                        conn,
                        user_id=user_id,
                        min_messages=settings.SESSION_SUMMARY_MIN_TURNS,
                    )

            unsummarized = await asyncio.to_thread(_load_unsummarized)
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
        ``SESSION_SUMMARY_IDLE_MINUTES`` and whose summary is missing or stale.

        Returns count of sessions summarized.
        """
        idle_minutes = settings.SESSION_SUMMARY_IDLE_MINUTES
        cutoff_sql = f"datetime('now', '-{idle_minutes} minutes')"

        def _load_idle_rows():
            with db.get_connection() as conn:
                return conn.execute(
                    f"""
                    SELECT cs.id, cs.user_id
                    FROM chat_sessions cs
                    JOIN chat_messages cm ON cs.id = cm.session_id
                    LEFT JOIN session_summaries ss ON cs.id = ss.session_id
                    WHERE cs.updated_at < {cutoff_sql}
                    GROUP BY cs.id
                    HAVING COUNT(cm.id) >= ?
                       AND COUNT(cm.id) > COALESCE(MAX(ss.turn_count), 0)
                    ORDER BY cs.updated_at DESC
                    """,
                    (settings.SESSION_SUMMARY_MIN_TURNS,),
                ).fetchall()

        rows = await asyncio.to_thread(_load_idle_rows)

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
            def _search() -> list[dict]:
                with db.get_connection() as conn:
                    return db.search_chat_messages(conn, user_id=user_id, query=query, limit=limit)

            return await asyncio.to_thread(_search)

        async def _semantic_search() -> list[dict]:
            return await self.search_sessions(user_id, query, limit=limit)

        fts_outcome, semantic_outcome = await asyncio.gather(
            _fts_search(),
            _semantic_search(),
            return_exceptions=True,
        )
        if isinstance(fts_outcome, BaseException):
            logger.warning("Past-conversation FTS search degraded", exc_info=fts_outcome)
            fts_results: list[dict] = []
        else:
            fts_results = fts_outcome
        if isinstance(semantic_outcome, BaseException):
            logger.warning(
                "Past-conversation summary search degraded",
                exc_info=semantic_outcome,
            )
            semantic_results: list[dict] = []
        else:
            semantic_results = semantic_outcome

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
                        "session_title": s.get("session_title", ""),
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
