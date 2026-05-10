"""Schema migration definitions — aggregator loading categorized migration files.

Each entry is a (version, description, sql) tuple applied sequentially
on startup.  Extracted from ``_migrations.py`` to keep that module focused
on the runner logic.
"""

from ._migrations_core import _MIGRATIONS_CORE
from ._migrations_features import _MIGRATIONS_FEATURES
from ._migrations_memory import _MIGRATIONS_MEMORY

_MIGRATIONS: list[tuple[int, str, str]] = (
    _MIGRATIONS_CORE + _MIGRATIONS_MEMORY + _MIGRATIONS_FEATURES
)

# Exported so tests can reuse the full schema instead of duplicating DDL.
SCHEMA_SQL = "\n".join(sql for _, _, sql in _MIGRATIONS)
