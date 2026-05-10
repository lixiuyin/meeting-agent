"""MemoryService user profile refresh mixin."""

import asyncio
import json
import threading
import time
from typing import Protocol, cast

from ....core import database as db
from ....core.database import get_connection, get_write_connection
from .._common import logger
from . import settings

# M-13: Per-user debounce for profile refresh (5 min cooldown)
_PROFILE_REFRESH_COOLDOWN_S = 300.0
_profile_last_refreshed: dict[str, float] = {}
_profile_debounce_lock = threading.Lock()


def _dedup_profile_overlaps(host, user_id: str, profile_value: str) -> int:
    """Reduce importance of regular memories whose key appears verbatim in the profile.

    This is a lightweight text-match dedup — full semantic overlap detection
    needs an LLM call and is deferred to periodic consolidation.
    Returns the number of memories demoted.
    """
    try:
        profile_lower = profile_value.lower()
    except Exception:
        return 0

    demoted = 0
    try:
        with get_connection() as conn:
            memories = db.list_memories(conn, user_id=user_id, include_expired=False)
        for m in memories:
            key = m.get("key", "")
            if not key or key == "__profile__":
                continue
            # Only demote if the key is a meaningful substring (>3 chars)
            # that appears inside the profile text.
            if len(key) > 3 and key.lower() in profile_lower:
                new_imp = max(1.0, m.get("importance", 3) - 2.0)
                try:
                    with get_write_connection() as conn:
                        db.update_memory_importance(
                            conn, user_id=user_id, key=key, importance=new_imp
                        )
                    demoted += 1
                except Exception:
                    logger.debug(
                        "Failed to demote memory %s for user %s",
                        key,
                        user_id,
                        exc_info=True,
                    )
        if demoted:
            logger.info(
                "Profile dedup: demoted %d overlapping memories for user %s",
                demoted,
                user_id,
            )
    except Exception:
        logger.debug("Profile dedup scan failed for user %s", user_id, exc_info=True)
    return demoted


_PROFILE_REFRESH_PROMPT = """\
You are a memory consolidation agent. Given the following list of facts and memories
about a user, produce a concise, structured user profile.

Existing memories:
{memories}

Current profile (if any):
{current_profile}

Produce a JSON object with these fields:
- "preferences": list of user preferences (e.g. language, tools, communication style)
- "expertise_areas": list of technical domains or topics the user works in
- "common_topics": list of subjects the user frequently discusses
- "interaction_patterns": list of observations about how the user interacts
  (e.g. "prefers concise answers", "asks follow-up questions")
- "summary": 2-3 sentence natural language summary of the user profile

If the existing memories don't differ meaningfully from the current profile,
return exactly: NO_CHANGE

Return JSON object only, no other text:"""


class _ProfileHost(Protocol):
    def get(self, user_id: str, key: str) -> str | None: ...

    def set(
        self,
        user_id: str,
        key: str,
        value: str,
        *,
        source: str = "manual",
        importance: int = 3,
        expires_at: str | None = None,
        category: str | None = None,
        session_id: str | None = None,
    ) -> None: ...


class _MemoryProfileMixin:
    async def refresh_user_profile(self, user_id: str) -> bool:
        """Consolidate user memories into a structured profile via LLM.

        Stores the result as a special memory entry with key='__profile__'.
        Returns True if profile was updated, False if NO_CHANGE or error.
        """
        if not settings.MEMORY_PROFILE_ENABLED:
            return False

        # M-13: Debounce — skip if refreshed within the cooldown window
        with _profile_debounce_lock:
            last = _profile_last_refreshed.get(user_id, 0.0)
            if time.monotonic() - last < _PROFILE_REFRESH_COOLDOWN_S:
                logger.debug("Profile refresh debounced for user %s", user_id)
                return False

        try:
            import re

            from ...llm import cached_retry_invoke, get_llm
            from ...traffic_control import traffic_controller

            host = cast(_ProfileHost, self)
            with get_connection() as conn:
                rows = db.list_memories(conn, user_id=user_id, include_expired=False)

            active = [m for m in rows if m["key"] != "__profile__" and not m.get("superseded_by")]
            if not active:
                return False

            mem_lines = [
                f"- [{m.get('category', 'general')}] {m['key']}: {m['value']}" for m in active[:50]
            ]
            memories_text = "\n".join(mem_lines)

            current_profile = host.get(user_id, "__profile__") or "None"

            prompt = _PROFILE_REFRESH_PROMPT.format(
                memories=memories_text,
                current_profile=current_profile,
            )
            llm = get_llm()

            if traffic_controller:
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
                return False

            text = content.strip()
            clean = text.strip().strip("`").strip()
            if clean == "NO_CHANGE":
                logger.debug("Profile refresh: no change for user %s", user_id)
                return False

            fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
            if fence_match:
                text = fence_match.group(1)

            profile_data = json.loads(text)
            if not isinstance(profile_data, dict):
                return False

            profile_value = json.dumps(profile_data, ensure_ascii=False, indent=2)
            host.set(
                user_id,
                "__profile__",
                profile_value,
                source="profile",
                importance=5,
                category="profile",
            )
            # HIGH-15: Dedup AFTER the LLM + set() succeed — if profile save
            # fails, we don't want to have already demoted overlapping memories.
            try:
                _dedup_profile_overlaps(host, user_id, profile_value)
            except Exception:
                logger.debug("Profile dedup skipped", exc_info=True)
            logger.info("Updated user profile for %s", user_id)
            with _profile_debounce_lock:
                _profile_last_refreshed[user_id] = time.monotonic()
            return True
        except Exception:
            logger.warning("Profile refresh failed for user %s", user_id, exc_info=True)
            return False
