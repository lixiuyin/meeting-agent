# ADR-001: Keep the SQL migration system as the single source of truth

## Status
Accepted

## Context
The repository had two migration paths:
1. Runtime SQL migrations in `backend/src/core/database/_migrations.py`
2. Alembic scaffold files in `backend/alembic/`

Only the SQL migration path is fully implemented and used in application startup (`init_db()`).
Keeping both creates tooling ambiguity for contributors and can cause schema drift.

## Decision
Use the SQL migration system in `_migrations.py` as the only authoritative migration mechanism.
Alembic files remain for future optional adoption, but are explicitly non-authoritative.

## Consequences
1. New schema changes must be added as new numbered SQL migrations in `_migrations.py`.
2. Contributors should not create Alembic revisions for this repository.
3. Runtime startup remains deterministic because all migrations are applied by `init_db()`.
