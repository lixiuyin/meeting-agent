import json
import logging
from pathlib import Path

from ...core.config import settings

logger = logging.getLogger(__name__)

# ---- Session history cache config ----
_CACHE_MAX_SIZE = 256
_CACHE_TTL_SECONDS = 30 * 60
_SESSION_CACHE_PATH = Path(settings.DB_PATH).parent / "session_cache.json"
_SESSION_CACHE_PATH_LEGACY = Path(settings.DB_PATH).parent / "session_cache.pkl"

# M-6: Length limits for memory key/value to prevent vector pollution
_MEMORY_KEY_MAX_LENGTH = 200
_MEMORY_VALUE_MAX_LENGTH = 2000


def scope_from_messages(messages: list[dict]) -> tuple[list[int] | None, list[int] | None]:
    """Extract meeting and file provenance from persisted message sources."""
    meeting_ids: set[int] = set()
    file_ids: set[int] = set()
    for message in messages:
        raw = message.get("sources_json")
        if not raw:
            continue
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            continue
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if not isinstance(item, dict):
                continue
            raw_meetings = item.get("meeting_ids")
            many_meetings = raw_meetings if isinstance(raw_meetings, (list, tuple, set)) else []
            for candidate in [item.get("meeting_id"), *many_meetings]:
                try:
                    meeting_ids.add(int(candidate))
                except (TypeError, ValueError):
                    continue
            raw_files = item.get("file_ids")
            many_files = raw_files if isinstance(raw_files, (list, tuple, set)) else []
            for candidate in [item.get("file_id"), *many_files]:
                try:
                    file_ids.add(int(candidate))
                except (TypeError, ValueError):
                    continue
    return sorted(meeting_ids) or None, sorted(file_ids) or None
