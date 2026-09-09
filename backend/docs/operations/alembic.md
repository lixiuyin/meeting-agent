# Alembic database migration workflow

> While retaining **`_migrations.py` legacy tuple migration**, this repository introduces **Alembic** as a reviewable and repeatable schema evolution method.
> Startup path: `backend/src/api/lifespan/` → `run_alembic_upgrade()` → `alembic upgrade head`.
> Baseline revision: `20260414_000001` (file `backend/alembic/versions/20260414_000001_baseline_schema.py`).
> Verified current head on 2026-09-09: `20260908_000003`.

## 1. Current mechanism description

- **Online/local uvicorn**: Alembic is executed first; the baseline `upgrade()` will traverse `_MIGRATIONS` and write `schema_version`, which is consistent with the historical effect of only calling `init_db()`.
- **`init_db()`**: still retained; called by `run_alembic_upgrade` only as a
  development fallback when Alembic or `alembic.ini` is unavailable. It can
  also be used explicitly by tests and diagnostics.
- **Subsequent changes**: `_MIGRATIONS` is frozen at v52. Every schema change
  after that baseline must be a new Alembic revision; published revisions are
  immutable.

## 2. One-time alignment of existing database (stamp)

If the database has been built with `init_db()` before Alembic is introduced, and `schema_version` has reflected the latest version, the Alembic head pointer can be aligned without repeatedly executing the baseline SQL:

```bash
cd backend
uv run alembic stamp 20260414_000001
```

If you are not sure, first **backup** `data/meetings.db`, and then verify it in the test environment.

## 3. Common commands

```bash
cd backend

# View current heads
uv run alembic heads

# Upgrade to the latest
uv run alembic upgrade head

# Create a new empty revision (handwritten upgrade/downgrade)
uv run alembic revision -m "describe schema change"

# Automatically generate a revision from the current library (used when sqlalchemy metadata is aligned with the model)
# uv run alembic revision --autogenerate -m "sync models"
```

## 4. Migration rules

1. Changes after the v52 baseline **must** include a new Alembic revision; do
   not append to or rewrite `_MIGRATIONS`.
2. `upgrade()` / `downgrade()` should be implemented in pairs when feasible; **production environment** is mainly for forward upgrades, and `downgrade()` is mostly used for development rollback.
3. Indicate in the PR description: whether the migration is reversible, whether data backfilling is required, and whether it is related to Chroma/index rebuilding.
4. Before merging, execute `alembic upgrade head` once on the clean library and the "copy close to production volume".

Container image promotion is not a database rollback. The release workflow can
promote an earlier backend/frontend image only after the operator confirms that
schema and application-data recovery are handled through a separately verified
backup/restore plan. Never test an old image only against a fresh development
database and treat that as proof that it is compatible with production data.

## 5. Interaction with logs

`alembic.ini` will adjust logging via `fileConfig`. Project use case `tests/config/test_logging.py` Constraints: **The application file handler must still be available after loading the Alembic configuration**. If you modify `alembic/env.py`, please run this test.

## 6. Related documents

- Table structure and 52 frozen legacy migration summaries: [`../database.md`](../database.md)
- Startup sequence and operation and maintenance: [`../lifespan-and-operations.md`](../lifespan-and-operations.md)
Published revisions are immutable. CI verifies their SHA-256 values from
`alembic/immutable_revisions.json`; schema corrections must be made in a new
forward revision, never by editing or deleting an existing file.
