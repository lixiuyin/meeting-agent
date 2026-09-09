"""Singleton skill loader and intent matcher with thread-safe initialization."""

import hashlib
import logging
import threading

from skills.loader import SkillLoader
from skills.matcher import IntentMatchingService

from ...core.config import settings

logger = logging.getLogger(__name__)

_skill_loader: SkillLoader | None = None
_skill_matcher: IntentMatchingService | None = None
_skill_loader_key: str | None = None
_skill_matcher_key: tuple[str, ...] | None = None
_skill_lock = threading.Lock()


def _secret_digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest() if value else ""


def _matcher_config_key() -> tuple[str, ...]:
    """Identify lazy matcher dependencies without instantiating heavy models."""
    return (
        settings.LLM_BINDING,
        settings.LLM_MODEL,
        settings.LLM_BASE_URL,
        settings.LLM_HOST,
        _secret_digest(settings.LLM_API_KEY.get_secret_value()),
        settings.EMBEDDING_BINDING,
        settings.EMBEDDING_MODEL,
        str(settings.EMBEDDING_DIMENSION),
        settings.EMBEDDING_BASE_URL,
        settings.EMBEDDING_HOST,
        _secret_digest(settings.EMBEDDING_API_KEY.get_secret_value()),
    )


def get_skill_loader() -> SkillLoader:
    """Get or create the singleton SkillLoader (thread-safe).

    Malformed skill.md files are handled gracefully: individual load failures
    are logged and the affected skill is quarantined, but the loader itself
    always returns successfully so the rest of the system can continue.
    """
    global _skill_loader, _skill_loader_key
    loader_key = str(settings.CUSTOM_SKILLS_DIR.resolve())
    if _skill_loader is None or _skill_loader_key != loader_key:
        with _skill_lock:
            if _skill_loader is None or _skill_loader_key != loader_key:
                try:
                    _skill_loader = SkillLoader(custom_skills_dir=loader_key)
                    _skill_loader_key = loader_key
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
    global _skill_matcher, _skill_matcher_key
    matcher_key = _matcher_config_key()
    if _skill_matcher is None or _skill_matcher_key != matcher_key:
        with _skill_lock:
            if _skill_matcher is None or _skill_matcher_key != matcher_key:
                try:
                    _skill_matcher = IntentMatchingService()
                    _skill_matcher_key = matcher_key
                except Exception:
                    logger.exception(
                        "Failed to create IntentMatchingService; skill matching will be unavailable"
                    )
                    raise
    return _skill_matcher


def reset_skill_loader() -> None:
    """Reset the SkillLoader singleton so settings changes can re-create it."""
    global _skill_loader, _skill_loader_key
    with _skill_lock:
        _skill_loader = None
        _skill_loader_key = None


def reset_skill_matcher() -> None:
    """Reset the IntentMatchingService singleton so settings changes can re-create it.

    This also clears the match result and query embedding caches.
    """
    global _skill_matcher, _skill_matcher_key
    with _skill_lock:
        if _skill_matcher is not None:
            _skill_matcher._match_cache.clear()
            _skill_matcher.semantic_matcher._query_embed_cache.clear()
        _skill_matcher = None
        _skill_matcher_key = None
