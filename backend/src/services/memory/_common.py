import logging
from pathlib import Path

from ...core.config import settings

logger = logging.getLogger(__name__)

# ---- Session history cache config ----
_CACHE_MAX_SIZE = 256
_CACHE_TTL_SECONDS = 30 * 60
_SESSION_CACHE_PATH = Path(settings.DB_PATH).parent / "session_cache.json"
_SESSION_CACHE_PATH_LEGACY = Path(settings.DB_PATH).parent / "session_cache.pkl"

# ---- Memory importance/decay config (delegated to settings) ----
_INITIAL_IMPORTANCE = settings.MEMORY_INITIAL_IMPORTANCE
_MAX_IMPORTANCE = settings.MEMORY_MAX_IMPORTANCE
_MIN_IMPORTANCE = settings.MEMORY_MIN_IMPORTANCE
_DECAY_RATE_PER_DAY = settings.MEMORY_DECAY_RATE_PER_DAY
_MEMORY_TTL_DAYS = settings.MEMORY_TTL_DAYS
_MAX_MEMORIES_PER_USER = settings.MEMORY_MAX_PER_USER

# ---- Dedup / clustering threshold ----
_MEMORY_DEDUP_THRESHOLD = settings.MEMORY_DEDUP_THRESHOLD

# M-6: Length limits for memory key/value to prevent vector pollution
_MEMORY_KEY_MAX_LENGTH = 512
_MEMORY_VALUE_MAX_LENGTH = 2000
