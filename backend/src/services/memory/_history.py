import json
import sqlite3
import threading
from pathlib import Path

from cachetools import TTLCache
from langchain_core.chat_history import BaseChatMessageHistory
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from ...core import database as db
from ...core.database import get_write_connection
from ._common import (
    _CACHE_MAX_SIZE,
    _CACHE_TTL_SECONDS,
    _SESSION_CACHE_PATH,
    _SESSION_CACHE_PATH_LEGACY,
    logger,
)


class SQLiteChatMessageHistory(BaseChatMessageHistory):
    """LangChain-compatible chat message history backed by SQLite"""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._messages: list[BaseMessage] = []
        self._load()

    def _load(self) -> None:
        role_map = {
            "human": HumanMessage,
            "ai": AIMessage,
            "system": SystemMessage,
        }
        with db.get_connection() as conn:
            # Prompt history is a bounded hot window. Older context is read
            # directly from SQL by the durable incremental summarizer.
            rows = db.get_messages(conn, self.session_id, limit=200)
        self._messages = [
            role_map[r["role"]](content=r["content"]) for r in rows if r["role"] in role_map
        ]

        # Do not token-truncate here. The chain must first see the bounded raw
        # message window so it can summarize older turns before applying the
        # generation budget. Resolver and generation consumers sanitize their
        # own copies immediately before prompt construction.

        # Update session access stats
        try:
            with get_write_connection() as conn:
                db.touch_session(conn, self.session_id)
        except Exception:
            logger.warning("Session touch failed", exc_info=True)

    @property
    def messages(self) -> list[BaseMessage]:
        return list(self._messages)

    def add_message(self, message: BaseMessage) -> None:
        content: str
        if isinstance(message.content, list):
            content = json.dumps(message.content)
        else:
            content = message.content
        # Extract sources_json from additional_kwargs (set by save_messages)
        sources_json = (
            message.additional_kwargs.get("sources_json") if message.additional_kwargs else None
        )
        # Persist first; only mutate the in-memory cache after a successful
        # write so a failed INSERT (e.g. FK violation when the parent
        # session was deleted concurrently) doesn't leave the cache out of
        # sync with disk.
        try:
            with get_write_connection() as conn:
                db.add_message(
                    conn,
                    session_id=self.session_id,
                    role=message.type,
                    content=content,
                    sources_json=sources_json,
                )
        except sqlite3.IntegrityError as exc:
            if "FOREIGN KEY" in str(exc).upper():
                logger.warning(
                    "Skipping in-memory append for session %s — parent row "
                    "was deleted (FK violation); upstream caller should drop "
                    "the cache entry.",
                    self.session_id,
                )
                return
            raise
        self._messages.append(message)

    def clear(self) -> None:
        self._messages = []
        with get_write_connection() as conn:
            db.clear_messages(conn, self.session_id)


# ---- Session history cache ----
_histories: TTLCache[str, SQLiteChatMessageHistory] = TTLCache[str, SQLiteChatMessageHistory](
    maxsize=_CACHE_MAX_SIZE,
    ttl=_CACHE_TTL_SECONDS,
)
_histories_lock = threading.Lock()


def get_session_history(session_id: str) -> SQLiteChatMessageHistory:
    """Get or create a cached session history (thread-safe, auto-expires after 30 min)."""
    with _histories_lock:
        cached = _histories.get(session_id)
        if cached is not None:
            return cached
    # Database I/O must not block unrelated cache hits under the global lock.
    history = SQLiteChatMessageHistory(session_id)
    with _histories_lock:
        cached = _histories.get(session_id)
        if cached is not None:
            return cached
        _histories[session_id] = history
        return history


def invalidate_session(session_id: str) -> None:
    """Remove a session from the in-memory cache."""
    with _histories_lock:
        _histories.pop(session_id, None)


# ---- Session cache persistence ----


def _serialize_messages(messages: list[BaseMessage]) -> list[dict[str, str]]:
    """Convert BaseMessage list to JSON-serializable format."""
    return [
        {
            "role": m.type,
            "content": m.content if isinstance(m.content, str) else json.dumps(m.content),
        }
        for m in messages
    ]


def _deserialize_messages(data: list[dict[str, str]]) -> list[BaseMessage]:
    """Convert JSON data back to BaseMessage list."""
    role_map = {"human": HumanMessage, "ai": AIMessage, "system": SystemMessage}
    messages = []
    for item in data:
        cls = role_map.get(item.get("role", ""))
        if cls:
            messages.append(cls(content=item.get("content", "")))
    return messages


def _persist_session_cache() -> None:
    """Serialize session cache to disk as JSON (atomic write via temp file + rename)."""
    import tempfile

    with _histories_lock:
        cache_data = {k: [] for k in _histories}
    try:
        _SESSION_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Write to temp file first, then atomically rename
        fd, tmp_path = tempfile.mkstemp(dir=str(_SESSION_CACHE_PATH.parent), suffix=".json.tmp")
        try:
            with open(fd, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, ensure_ascii=False)
            # Atomic rename (same filesystem, guaranteed atomic on POSIX)
            Path(tmp_path).rename(_SESSION_CACHE_PATH)
        except BaseException:
            Path(tmp_path).unlink(missing_ok=True)
            raise
        logger.info("Session cache persisted (%d entries)", len(cache_data))
    except Exception:
        logger.warning("Failed to persist session cache", exc_info=True)


def _load_session_cache() -> None:
    """Restore session cache from JSON on startup."""
    cache_data: dict | None = None

    if _SESSION_CACHE_PATH.exists():
        try:
            with open(_SESSION_CACHE_PATH, encoding="utf-8") as f:
                cache_data = json.load(f)
        except Exception:
            logger.warning("Failed to load JSON session cache", exc_info=True)

    # Rename legacy pickle file out of the way if it still exists
    if _SESSION_CACHE_PATH_LEGACY.exists():
        try:
            _SESSION_CACHE_PATH_LEGACY.rename(
                _SESSION_CACHE_PATH_LEGACY.with_suffix(".pickle.deprecated")
            )
            logger.info("Renamed legacy pickle cache (no longer loaded)")
        except Exception:
            logger.debug("Could not rename legacy cache file", exc_info=True)

    if not cache_data:
        return

    # JSON is a warm-up hint, never an authoritative copy of the transcript.
    # In particular a stale snapshot must not resurrect deleted sessions or
    # replace newer SQL messages after an unclean shutdown.
    for session_id in cache_data:
        with db.get_connection() as conn:
            exists = conn.execute(
                "SELECT 1 FROM chat_sessions WHERE id=?", (session_id,)
            ).fetchone()
        if not exists:
            continue
        history = SQLiteChatMessageHistory(session_id)
        with _histories_lock:
            _histories[session_id] = history
    logger.info("Session cache loaded (%d entries)", len(cache_data))
