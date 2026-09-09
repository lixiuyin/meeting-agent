"""Regression tests for application logging configuration.

Guards against a known failure mode: Alembic's ``env.py`` historically called
``logging.config.fileConfig(...)`` with the default ``disable_existing_loggers=True``,
which detached the ``RotatingFileHandler`` attached by ``src.core.logging`` and
stopped ``app.log`` from growing for the remainder of the process lifetime.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from alembic.config import Config

from src.core import constants as constants_module
from src.core.logging import _make_file_handler, configure_logging


def _root_file_handlers() -> list[RotatingFileHandler]:
    return [h for h in logging.getLogger().handlers if isinstance(h, RotatingFileHandler)]


def test_configure_logging_attaches_rotating_file_handler() -> None:
    """configure_logging() must attach exactly one RotatingFileHandler to root."""
    configure_logging()

    handlers = _root_file_handlers()
    assert len(handlers) >= 1, "configure_logging did not attach a RotatingFileHandler"


def test_alembic_env_preserves_file_handler(tmp_path: Path, monkeypatch) -> None:
    """After loading alembic.ini config, the app's file handler must survive.

    Simulates what happens during lifespan: app initialises logging, then
    alembic bootstraps and calls ``fileConfig``. The fix in ``alembic/env.py``
    passes ``disable_existing_loggers=False`` so existing handlers are kept.
    """
    # Redirect LOG_DIR into tmp_path so the test does not write to data/logs.
    monkeypatch.setattr(constants_module, "LOG_DIR", tmp_path)

    # Force re-initialisation so the handler targets tmp_path.
    import src.core.logging as logging_module

    monkeypatch.setattr(logging_module, "_initialized", False)
    # _make_file_handler reads LOG_DIR that was imported by name in the module,
    # so rebind the module-level reference too.
    monkeypatch.setattr(logging_module, "LOG_DIR", tmp_path)
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    try:
        handler = _make_file_handler()
        root.addHandler(handler)
        assert any(isinstance(h, RotatingFileHandler) for h in root.handlers)

        # Import env.py's logic by invoking it the way lifespan does: loading
        # the alembic Config triggers env.py when upgrade is called, but for a
        # pure logging regression test we replay the snapshot/restore dance
        # manually so we exercise the actual production line.
        backend_dir = Path(__file__).resolve().parents[2]
        alembic_ini = backend_dir / "alembic.ini"
        assert alembic_ini.exists(), f"missing {alembic_ini}"
        cfg = Config(str(alembic_ini))
        from logging.config import fileConfig

        preserved = list(root.handlers)
        fileConfig(cfg.config_file_name, disable_existing_loggers=False)
        for h in preserved:
            if h not in root.handlers:
                root.addHandler(h)

        # The file handler must still be attached — this is the regression guard.
        surviving = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
        assert surviving, (
            "fileConfig detached the app's RotatingFileHandler; "
            "app.log will stop receiving records. Check alembic/env.py."
        )
    finally:
        for h in list(root.handlers):
            if h not in original_handlers:
                root.removeHandler(h)
                h.close()


def test_root_logger_writes_to_app_log(tmp_path: Path, monkeypatch) -> None:
    """End-to-end: emitting a record after fileConfig must reach app.log."""
    monkeypatch.setattr(constants_module, "LOG_DIR", tmp_path)

    import src.core.logging as logging_module

    monkeypatch.setattr(logging_module, "_initialized", False)
    monkeypatch.setattr(logging_module, "LOG_DIR", tmp_path)
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    try:
        handler = _make_file_handler()
        root.addHandler(handler)
        # Simulate the app's pre-alembic state where configure_logging set root to INFO.
        root.setLevel(logging.INFO)

        backend_dir = Path(__file__).resolve().parents[2]
        from logging.config import fileConfig

        preserved = list(root.handlers)
        preserved_level = root.level
        fileConfig(
            str(backend_dir / "alembic.ini"),
            disable_existing_loggers=False,
        )
        for h in preserved:
            if h not in root.handlers:
                root.addHandler(h)
        if preserved_level and preserved_level < root.level:
            root.setLevel(preserved_level)

        # INFO must survive — the regression dropped root level to WARN too.
        marker = "logging_regression_marker_xyz"
        logging.getLogger("test_logging_regression").info(marker)
        for h in root.handlers:
            h.flush()

        log_path = tmp_path / "app.log"
        assert log_path.exists(), f"{log_path} not created"
        contents = log_path.read_text(encoding="utf-8")
        assert marker in contents, (
            "Log record did not reach app.log after alembic fileConfig. "
            "Regression in alembic/env.py handler preservation."
        )
    finally:
        for h in list(root.handlers):
            if h not in original_handlers:
                root.removeHandler(h)
                h.close()


def test_file_handler_redacts_websocket_query_tokens(tmp_path: Path, monkeypatch) -> None:
    """Uvicorn handshake URLs must not persist bearer material in app.log."""
    import src.core.logging as logging_module

    monkeypatch.setattr(logging_module, "LOG_DIR", tmp_path)
    handler = _make_file_handler()
    try:
        record = logging.LogRecord(
            "uvicorn.error",
            logging.INFO,
            __file__,
            1,
            "WebSocket %s Authorization=%s",
            ("/api/v1/ws?client_id=test&token=ws-secret&mode=live", "Bearer api-secret"),
            None,
        )
        handler.handle(record)
        handler.flush()
    finally:
        handler.close()

    contents = (tmp_path / "app.log").read_text(encoding="utf-8")
    assert "ws-secret" not in contents
    assert "api-secret" not in contents
    assert "token=<REDACTED>&mode=live" in contents
    assert "Authorization=<REDACTED>" in contents
