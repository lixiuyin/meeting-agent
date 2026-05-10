"""Centralized logging configuration.

Called once at application startup (``main.py`` or ``api/__init__.py``).
Supports two modes:

    - ``LOG_FORMAT=text`` (default) — human-readable console output
    - ``LOG_FORMAT=json`` — structured JSON output
    - ``LOG_FORMAT=json`` — structured JSON with request_id and session_id fields

Level is controlled by ``LOG_LEVEL`` env var (default ``INFO``). Set to
``DEBUG`` to get maximum detail from app code while noisy third-party
loggers (httpx, urllib3, watchfiles, asyncio, chromadb, …) stay capped
at INFO/WARNING so they don't drown out useful output.

Logs are written to both console (stderr) and a rotating file in ``DATA_DIR/logs/``.
"""

import json
import logging
import os
from logging.handlers import RotatingFileHandler

from .constants import LOG_DIR

_initialized = False

_TEXT_FMT = "%(asctime)s [%(name)s] %(levelname)s %(message)s"

# Third-party loggers that get extremely chatty at DEBUG level. Cap them so
# DEBUG mode still leaves a readable trace of our own (`src.*`) code.
_NOISY_LOGGER_LEVELS: dict[str, int] = {
    "httpx": logging.INFO,
    "httpcore": logging.WARNING,
    "urllib3": logging.WARNING,
    "asyncio": logging.WARNING,
    "watchfiles": logging.WARNING,
    "watchfiles.main": logging.WARNING,
    "chromadb": logging.WARNING,
    "chromadb.telemetry": logging.WARNING,
    "openai": logging.INFO,
    "anthropic": logging.INFO,
    "multipart": logging.WARNING,
    "python_multipart": logging.WARNING,
    "PIL": logging.WARNING,
    "matplotlib": logging.WARNING,
    "fsspec": logging.WARNING,
    "filelock": logging.WARNING,
    "sqlalchemy.engine": logging.WARNING,
}


class _JsonFormatter(logging.Formatter):
    """Structured JSON log formatter with trace context."""

    def format(self, record: logging.LogRecord) -> str:
        log: dict = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        # Inject request_id from RequestIdFilter (api/middleware.py)
        req_id = getattr(record, "request_id", "-")
        if req_id != "-":
            log["request_id"] = req_id

        # Inject session_id if present
        session_id = getattr(record, "session_id", None)
        if session_id:
            log["session_id"] = session_id

        # Exception info
        if record.exc_info and record.exc_info[1]:
            log["exc"] = self.formatException(record.exc_info)

        return json.dumps(log, ensure_ascii=False)


def _write_banner(log_path: "os.PathLike[str]") -> None:
    """Append a startup banner to the given log file."""
    from .tz import format_local

    banner = f"\n{'=' * 72}\n  SERVER STARTED — {format_local()}\n{'=' * 72}\n"
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(banner)
    except OSError:
        pass


def _make_file_handler() -> RotatingFileHandler:
    """Create a rotating file handler (10 MB, keep 5 backups).

    Writes a startup banner to both ``dev-console.log`` and ``app.log``
    so every log file marks session boundaries.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / "app.log"
    handler = RotatingFileHandler(
        log_path,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(_TEXT_FMT))

    # Startup banner in app.log
    _write_banner(log_path)
    # Also write banner into dev-console.log (created by `make dev`)
    _write_banner(LOG_DIR / "dev-console.log")

    return handler


def _strip_child_handlers() -> None:
    """Remove all handlers from child loggers so output flows through root only."""
    for name in list(logging.root.manager.loggerDict):
        child = logging.root.manager.loggerDict.get(name)
        if isinstance(child, logging.Logger) and child.handlers:
            child.handlers.clear()
            child.propagate = True


def _resolve_level(default: int) -> int:
    """Resolve effective log level from ``LOG_LEVEL`` env var, fallback to default."""
    raw = os.getenv("LOG_LEVEL", "").strip().upper()
    if not raw:
        return default
    name_to_level = logging.getLevelNamesMapping()
    return name_to_level.get(raw, default)


def configure_logging(level: int | None = None) -> None:
    """Configure application-wide logging.

    Idempotent — safe to call multiple times (subsequent calls are no-ops).

    After configuration, child logger ``addHandler()`` is patched to prevent
    libraries (alembic, uvicorn, httpx, …) from installing their own handlers,
    which would cause duplicate lines in the terminal / ``dev-console.log``.
    """
    global _initialized
    if _initialized:
        return
    _initialized = True

    log_format = os.getenv("LOG_FORMAT", "text").lower().strip()
    effective_level = _resolve_level(level if level is not None else logging.INFO)

    # Console handler
    console = logging.StreamHandler()
    if log_format == "json":
        console.setFormatter(_JsonFormatter())
    else:
        console.setFormatter(logging.Formatter(_TEXT_FMT))

    # File handler (always text format)
    file_handler = _make_file_handler()

    logging.basicConfig(
        level=effective_level,
        handlers=[console, file_handler],
        force=True,
    )

    # Cap chatty third-party loggers so DEBUG mode stays readable.
    for _name, _lvl in _NOISY_LOGGER_LEVELS.items():
        logging.getLogger(_name).setLevel(max(_lvl, effective_level))

    # Redact Authorization headers from log output to prevent API key leaks.
    class _AuthRedactionFilter(logging.Filter):
        _pattern = __import__("re").compile(
            r"(?i)(authorization|api[_-]?key)[=:]\s*['\"]?[^\s'\"}]+",
        )

        def filter(self, record: logging.LogRecord) -> bool:
            record.msg = self._pattern.sub(r"\1=<REDACTED>", str(record.msg))
            if record.args:
                record.args = tuple(
                    self._pattern.sub(r"\1=<REDACTED>", str(a)) if isinstance(a, str) else a
                    for a in record.args
                )
            return True

    _redaction_filter = _AuthRedactionFilter()
    logging.getLogger("httpx").addFilter(_redaction_filter)
    logging.getLogger("httpcore").addFilter(_redaction_filter)

    # Clear any handlers libraries added before us (uvicorn, alembic, …)
    _strip_child_handlers()

    # RequestIdMiddleware already logs every request with method/path/status/elapsed
    # via the `src.api.middleware` logger. Silence uvicorn's access logger so each
    # request isn't printed twice in the dev console / app.log.
    #
    # We can't rely on `logger.disabled = True` because CPython's
    # logging.config.fileConfig (called from alembic env.py during lifespan)
    # unconditionally resets `disabled` to the value of `disable_existing_loggers`
    # for every existing logger not named in the config file. A Filter survives
    # that reset.
    class _DropAll(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            return False

    _access = logging.getLogger("uvicorn.access")
    _access.handlers.clear()
    _access.propagate = False
    _access.addFilter(_DropAll())

    # Prevent future addHandler() calls on child loggers so no library can
    # re-introduce duplicate handlers.  All logging goes through the root
    # logger via propagation.
    _original_addHandler = logging.Logger.addHandler

    def _guarded_addHandler(self: logging.Logger, hdlr: logging.Handler) -> None:
        if self is logging.root:
            _original_addHandler(self, hdlr)
            return
        # Silently skip — propagation to root is sufficient.
        self.propagate = True

    logging.Logger.addHandler = _guarded_addHandler  # type: ignore[assignment]

    logging.getLogger(__name__).info(
        "File logging enabled at %s (level=%s)",
        LOG_DIR / "app.log",
        logging.getLevelName(effective_level),
    )
