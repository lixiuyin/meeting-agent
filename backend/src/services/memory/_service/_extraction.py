"""MemoryService fact extraction mixin."""

import asyncio
from typing import Protocol, cast

from ....core.database import get_write_connection
from ....core.metrics import (
    MEMORY_EXTRACT_DROPPED_TOTAL,
    MEMORY_EXTRACT_FACTS,
    MEMORY_EXTRACT_TOTAL,
)
from .._common import logger
from .._extractor import extract_facts
from .._parsers import _is_semantic_duplicate
from . import settings


class _ExtractionHost(Protocol):
    def search_important(
        self,
        user_id: str,
        min_importance: int = 3,
        limit: int = 10,
    ) -> list[dict]: ...

    async def _resolve_contradiction(
        self,
        *,
        existing_key: str,
        existing_value: str,
        new_key: str,
        new_value: str,
    ) -> str: ...

    def set(
        self,
        user_id: str,
        key: str,
        value: str,
        *,
        source: str = "manual",
        importance: float = 3,
        expires_at: str | None = None,
        category: str | None = None,
        session_id: str | None = None,
        meeting_ids: list[int] | None = None,
        file_ids: list[int] | None = None,
    ) -> None: ...


class _MemoryExtractionMixin:
    async def auto_extract_facts(
        self,
        user_id: str,
        question: str,
        answer: str,
        context: str | None = None,
        session_id: str | None = None,
        meeting_ids: list[int] | None = None,
        file_ids: list[int] | None = None,
    ) -> None:
        """Extract key facts with importance scoring, deduplication, and TTL."""
        if not settings.MEMORY_AUTO_EXTRACT:
            MEMORY_EXTRACT_TOTAL.labels(status="skipped").inc()
            return
        _mode_limits = {
            "precise": 1,
            "balanced": settings.MEMORY_MAX_FACTS_PER_TURN,
            "aggressive": 5,
        }
        _max_facts = _mode_limits.get(
            settings.MEMORY_EXTRACTION_MODE, settings.MEMORY_MAX_FACTS_PER_TURN
        )
        try:
            from ...llm import (
                cached_retry_invoke,
                get_extraction_llm,
                get_fact_extraction_prompt,
            )

            host = cast(_ExtractionHost, self)
            existing_ctx = ""
            if settings.MEMORY_EXTRACTION_INCLUDE_EXISTING:
                existing = host.search_important(user_id, min_importance=2, limit=5)
                if existing:
                    lines = ["User profile (existing memories):"]
                    for m in existing:
                        lines.append(f"- {m['key']}: {m['value']}")
                    existing_ctx = "\n".join(lines) + "\n\n"

            llm = get_extraction_llm()
            prompt_template = get_fact_extraction_prompt()
            # Wrap user inputs in XML tags to reduce prompt injection surface:
            # user question/answer are regular text, not prompt instructions.
            prompt = prompt_template.format(
                question=f"<user_question>\n{question}\n</user_question>",
                answer=f"<assistant_answer>\n{answer}\n</assistant_answer>",
                user_context=existing_ctx,
            )
            # H-MEM-3: Route through traffic_controller to cap LLM concurrency.
            from ...traffic_control import traffic_controller

            if traffic_controller is not None:
                async with traffic_controller:
                    try:
                        response = await asyncio.to_thread(cached_retry_invoke, llm, prompt)
                        traffic_controller.record_success()
                    except Exception:
                        traffic_controller.record_failure()
                        raise
                    except BaseException:
                        traffic_controller.record_failure()
                        raise
            else:
                response = await asyncio.to_thread(cached_retry_invoke, llm, prompt)
            content = response.content
            if isinstance(content, list):
                # H-13: Extract text from multi-modal content blocks instead
                # of silently discarding the response.
                text_parts = [
                    b.get("text", "")
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                content = "\n".join(text_parts)
                if not content.strip():
                    MEMORY_EXTRACT_TOTAL.labels(status="non_text_response").inc()
                    return

            facts = extract_facts(
                content=content,
                question=question,
                answer=answer,
                max_facts=_max_facts,
            )
            if not facts:
                MEMORY_EXTRACT_TOTAL.labels(status="success").inc()
                MEMORY_EXTRACT_FACTS.observe(0)
                return

            existing_keys = [m["key"] for m in existing]
            existing_key_set = {k.lower() for k in existing_keys}
            # MEM-9: Fuzzy dedup — also check near-matches to catch
            # "Manager of X" vs "X Manager" semantic duplicates.
            import difflib

            def _fuzzy_match(new_key: str) -> str | None:
                nk = new_key.lower()
                if nk in existing_key_set:
                    return nk
                for ek in existing_keys:
                    if difflib.SequenceMatcher(None, nk, ek.lower()).ratio() > 0.85:
                        return ek
                return None

            for fact in facts:
                key = fact.key
                value = fact.value
                importance = min(fact.importance, settings.MEMORY_AUTO_EXTRACT_INITIAL_IMPORTANCE)
                category = fact.category
                expires_at = fact.expires_at

                matched_existing = _fuzzy_match(key)
                if matched_existing:
                    existing_entry = next(
                        (m for m in existing if m["key"].lower() == matched_existing.lower()), None
                    )
                    if existing_entry and existing_entry.get("value") != value:
                        try:
                            resolution = await host._resolve_contradiction(
                                existing_key=existing_entry["key"],
                                existing_value=existing_entry.get("value", ""),
                                new_key=key,
                                new_value=value,
                            )
                            if resolution == "update":
                                from ....core import database as db

                                # Atomic: mark old superseded + insert new in one tx
                                with get_write_connection() as conn:
                                    db.mark_memory_superseded(
                                        conn,
                                        user_id=user_id,
                                        key=existing_entry["key"],
                                        superseded_by=key,
                                    )
                                    db.set_memory(
                                        conn,
                                        user_id=user_id,
                                        key=key,
                                        value=value,
                                        source="auto_extracted",
                                        importance=importance,
                                        expires_at=expires_at,
                                        category=category,
                                        embedding_id=None,
                                        meeting_ids=meeting_ids,
                                        file_ids=file_ids,
                                    )
                                # Best-effort vector upsert outside the tx.
                                # Offload to a thread: vs.upsert calls the
                                # sync embedder, which refuses to run inside
                                # a running event loop.
                                try:
                                    from .._vectorstore import get_memory_vectorstore

                                    vs = get_memory_vectorstore()
                                    await asyncio.to_thread(
                                        vs.upsert,
                                        user_id,
                                        key,
                                        value,
                                        importance,
                                        category,
                                        meeting_ids=meeting_ids,
                                        file_ids=file_ids,
                                    )
                                except Exception:
                                    logger.warning(
                                        "Vector upsert failed for key %s after DB write",
                                        key,
                                        exc_info=True,
                                    )
                                logger.debug(
                                    "Updating superseded memory: %s → %s",
                                    existing_entry["key"],
                                    key,
                                )
                                existing_key_set.add(key.lower())
                                existing_keys.append(key)
                            else:
                                logger.debug(
                                    "Keeping existing memory %s (resolution: %s)", key, resolution
                                )
                                continue
                        except Exception:
                            logger.warning(
                                "Contradiction resolution failed for key %s",
                                key,
                                exc_info=True,
                            )
                            MEMORY_EXTRACT_DROPPED_TOTAL.labels(reason="contradiction_failed").inc()
                            continue
                    else:
                        logger.debug("Skipping duplicate memory: %s", key)
                        MEMORY_EXTRACT_DROPPED_TOTAL.labels(reason="exact_duplicate").inc()
                        continue

                elif _is_semantic_duplicate(key, existing_keys):
                    logger.debug("Skipping semantically similar memory: %s", key)
                    MEMORY_EXTRACT_DROPPED_TOTAL.labels(reason="semantic_duplicate").inc()
                    continue

                host.set(
                    user_id,
                    key,
                    value,
                    source="auto_extracted",
                    importance=importance,
                    expires_at=expires_at,
                    category=category,
                    session_id=session_id,
                    meeting_ids=meeting_ids,
                    file_ids=file_ids,
                )
                existing_key_set.add(key.lower())
                existing_keys.append(key)

            logger.debug("Extracted facts from conversation turn")
            MEMORY_EXTRACT_TOTAL.labels(status="success").inc()
            MEMORY_EXTRACT_FACTS.observe(len(facts))
        except Exception:
            logger.warning("Fact extraction failed", exc_info=True)
            MEMORY_EXTRACT_TOTAL.labels(status="error").inc()
