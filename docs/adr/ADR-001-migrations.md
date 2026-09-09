# ADR-001: Freeze legacy SQL and use Alembic as the migration source of truth

## Status
Accepted

## Context
The repository historically had two migration representations:
1. Runtime SQL migrations in `backend/src/core/database/_migrations.py`
2. Alembic scaffold files in `backend/alembic/`

Production startup runs Alembic and fails closed if it cannot migrate. The
numbered SQL list remains necessary only for compatibility with installations
created before Alembic became mandatory.

## Decision
Alembic is the only migration execution path for schema changes after legacy
schema version 52. The legacy list is frozen. Fresh bootstrap/test schema SQL
may be cumulative, but it does not create a new `schema_version` entry. Startup
verifies that all legacy versions 1–52 exist and that Alembic is at head.

## Consequences
1. New schema changes require one Alembic revision, not duplicate legacy SQL.
2. `schema_version` remains at 52 while `alembic_version` advances.
3. Production never silently falls back when Alembic is unavailable or fails.
4. A downgrade must not destroy application data merely to remove compatibility columns.
