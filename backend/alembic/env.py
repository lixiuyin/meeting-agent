"""Alembic environment configuration."""

from __future__ import annotations

import logging
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import create_engine, pool

from alembic import context
from src.core.constants import DB_PATH

config = context.config

if config.config_file_name is not None:
    # fileConfig unconditionally replaces root.handlers and root.level whenever
    # [logger_root] is configured in alembic.ini (see CPython logging/config.py).
    # When alembic runs inside the app process (via lifespan), that detaches
    # the RotatingFileHandler attached by src.core.logging and lowers root to
    # WARN, so app.log stops growing and INFO records are dropped for the rest
    # of the process. Snapshot root state before loading alembic.ini and fully
    # restore it after — discard any handlers fileConfig installed (otherwise
    # alembic's console handler stays on root and every log line is emitted
    # twice with two different formats). Pass disable_existing_loggers=False
    # so non-root app loggers stay enabled.
    _root = logging.getLogger()
    _preserved_handlers = list(_root.handlers)
    _preserved_level = _root.level
    try:
        fileConfig(config.config_file_name, disable_existing_loggers=False)
    finally:
        for _handler in list(_root.handlers):
            _root.removeHandler(_handler)
        for _handler in _preserved_handlers:
            _root.addHandler(_handler)
        if _preserved_level and _preserved_level < _root.level:
            _root.setLevel(_preserved_level)

target_metadata = None  # SQLite schema managed via raw SQL in revision files


def _db_url() -> str:
    """Build a sqlite URL from runtime constants."""
    resolved = Path(DB_PATH).resolve()
    return f"sqlite:///{resolved}"


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    context.configure(
        url=_db_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = create_engine(_db_url(), poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
