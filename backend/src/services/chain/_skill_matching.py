"""Singleton skill loader and intent matcher with thread-safe initialization."""

import logging
import threading

from skills.loader import SkillLoader
from skills.matcher import IntentMatchingService

logger = logging.getLogger(__name__)

_skill_loader: SkillLoader | None = None
_skill_matcher: IntentMatchingService | None = None
_skill_lock = threading.Lock()


def get_skill_loader() -> SkillLoader:
    """Get or create the singleton SkillLoader (thread-safe).

    Malformed skill.md files are handled gracefully: individual load failures
    are logged and the affected skill is quarantined, but the loader itself
    always returns successfully so the rest of the system can continue.
    """
    global _skill_loader
    if _skill_loader is None:
        with _skill_lock:
            if _skill_loader is None:
                try:
                    _skill_loader = SkillLoader()
                except Exception:
                    logger.exception(
                        "Failed to create SkillLoader; skill system will be unavailable"
                    )
                    raise
    return _skill_loader


def get_skill_matcher() -> IntentMatchingService:
    """Get or create the singleton IntentMatchingService (thread-safe).

    Wraps creation in try/except so malformed skill definitions do not crash
    the application.  A runtime error is raised only if the matcher itself
    cannot be constructed; individual skill file errors are handled inside
    the loader.
    """
    global _skill_matcher
    if _skill_matcher is None:
        with _skill_lock:
            if _skill_matcher is None:
                try:
                    _skill_matcher = IntentMatchingService()
                except Exception:
                    logger.exception(
                        "Failed to create IntentMatchingService; skill matching will be unavailable"
                    )
                    raise
    return _skill_matcher


def reset_skill_loader() -> None:
    """Reset the SkillLoader singleton so settings changes can re-create it."""
    global _skill_loader
    with _skill_lock:
        _skill_loader = None


def reset_skill_matcher() -> None:
    """Reset the IntentMatchingService singleton so settings changes can re-create it.

    This also clears the match result and query embedding caches.
    """
    global _skill_matcher
    with _skill_lock:
        if _skill_matcher is not None:
            _skill_matcher._match_cache.clear()
            _skill_matcher.semantic_matcher._query_embed_cache.clear()
        _skill_matcher = None
