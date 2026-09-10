# Database layer (SQLite)

> Persistence structure, read-write separation, Alembic migrations, main tables and operations.
>
> Code location: `backend/src/core/database/` (`__init__.py`, `_connection.py`, `_migrations.py`, `_migration_definitions.py`, `_migration_helpers.py`, `_migration_lock.py`, `_scopes.py`, `meetings.py`, `chat.py`, `memories.py`, `knowledge_graph.py`, `bm25.py`, `idempotency.py`, `index_state.py`).
> Upgrade entry when the application starts: `backend/src/api/lifespan/` → `run_alembic_upgrade()`.
> Alembic: `backend/alembic/`, `backend/alembic.ini`. For detailed procedures, see [`operations/alembic.md`](./operations/alembic.md).

## 1. Why SQLite

- **Zero Operations**: works out of the box, no external services required
- **WAL mode**: concurrent reading does not block writing
- **Plenty of Capacity**: Typical meeting assistant scenarios (thousands of meetings, hundreds of thousands of memories) are well within SQLite's comfort zone
- **Single file backup**: `sqlite3 meetings.db ".backup …"` or file-level copy (note WAL consistency)
- **Deliberately single-instance**: repository boundaries reduce coupling, but
  migrating to Postgres would still require schema/data migration, SQL and FTS
  adaptation, transaction/concurrency review, distributed job coordination,
  object storage, and a remote vector-store design. It is an architectural
  migration, not a driver swap.

## 2. Read and write separation + WAL

`core/database/_connection.py` exposes two sets of APIs:

```python
# Read: lock-free, WAL ensures that concurrent reads do not block each other, nor do they block writes
with get_connection() as conn:
    cur = conn.execute("SELECT * FROM meetings WHERE id = ?", (mid,))

# Write: serialization (_write_lock) to ensure that writes do not cross
with get_write_connection() as conn:
    conn.execute("UPDATE meetings SET status = ? WHERE id = ?", ("ready", mid))
```

Key implementation details:

- **Thread local connection pool**: `threading.local()` saves connections to avoid cross-thread reuse
- **PRAGMA**: `journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout=30000` (30 seconds)
- **`_write_lock`**: one process-wide `threading.RLock`, matching SQLite's single-writer semantics and preventing competing in-process writers
- **Context Manager**: `with` correct commit/rollback + release connection back to the pool

### 2.1 How to write in coroutine

All DB write calls leave the event loop via `asyncio.to_thread(...)`:

```python
await asyncio.to_thread(create_meeting, title, user_id)
```

This retains the simplicity of the SQLite synchronization API without blocking FastAPI.

## 3. Migration mechanism (Alembic and `_migrations.py`)

### 3.1 Runtime path (consistent with `lifespan`)

1. **`lifespan.__aenter__` startup step**: `await asyncio.to_thread(run_alembic_upgrade)`
   - If Alembic is installed and `backend/alembic.ini` exists: execute `alembic upgrade head`.
   - **Baseline revision** `20260414_000001` calls `_apply_migration` in `_MIGRATIONS` in a loop within `upgrade()` and writes each record to `schema_version`, semantically aligned with the old version of `init_db()`.
   - Non-development environments fail closed if Alembic or `alembic.ini` is unavailable. Development may use the frozen `init_db()` bootstrap for tests and diagnostics.

2. **`init_db()`** (`core/database/_migrations.py`): can still be called **separately** in tests, scripts, and rollback scenarios; the logic is to read the current maximum `version` of `schema_version`, and execute SQL in sequence for the items `version > current` in `_MIGRATIONS` (defined in `_migration_definitions.py`) and insert version rows.

### 3.2 `schema_version` table

Created by `init_db()` or an Alembic baseline, of the form:

```sql
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    description TEXT NOT NULL,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

One row (version number + description) for each successfully applied legacy migration. **Don't** manually delete rows unless you are explicitly doing a recovery drill.

### 3.3 `alembic_version` table

Maintained by Alembic, recording the current revision id (such as `20260414_000001`). If only Alembic revision is added in the future, it should be promoted in this table; coexistence with `schema_version` is expected behavior.

Applied Alembic revisions are immutable. If a table or column must be reconciled
for databases that already reached an older revision, add a new forward-only,
idempotent revision. Durable chat executions use `chat_runs` and
`chat_run_events`; startup migration must create both before session APIs are
served.

### 3.4 Legacy migration list (`_MIGRATIONS`, 52 items in total)

Authoritative source: `backend/src/core/database/_migration_definitions.py` (imported by `_migrations.py`). The following table is a summary consistent with the code (please update this table simultaneously when PR changes the schema).

| #   | Description (consistent with code `description`)                                     | Key points                                                                                                                                                 |
| --- | ------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Initial schema                                                                       | `meetings`, `chat_sessions`, `chat_messages` (`role`: system/human/ai), `user_memories`                                                                    |
| 2   | Add error_message column to meetings                                                 | `meetings.error_message`                                                                                                                                   |
| 3   | Extend memories and sessions…                                                        | `user_memories`: `importance`, `expires_at`, `last_accessed`, `access_count`, `category`, `embedding_id`; `chat_sessions`: `last_accessed`, `access_count` |
| 4   | Add memory decay state tracking…                                                     | Table `memory_decay_state` (`last_decay_time`)                                                                                                             |
| 5   | Add content hash for upload idempotency                                              | `meetings.content_hash` + index                                                                                                                            |
| 6   | Add BM25 index persistence tables                                                    | `bm25_index`, `bm25_stats`                                                                                                                                 |
| 7   | Add meeting_files table for multi-file support                                       | `meeting_files` + migrate data from old `meetings`                                                                                                         |
| 8   | Add FTS5 virtual table for full-text search                                          | `bm25_chunks` FTS5 + trigger synchronization `bm25_index`                                                                                                  |
| 9   | Relax NOT NULL constraints on meetings…                                              | `meetings` rebuild, file column can be null                                                                                                                |
| 10  | Add session_summaries table…                                                         | `session_summaries` (including `user_id`, `topics`, `embedding_id`, etc.)                                                                                  |
| 11  | Add FTS5 over chat_messages…                                                         | `chat_messages_fts` + trigger                                                                                                                              |
| 12  | Add source provenance columns to user_memories                                       | `session_id`, `turn_index`                                                                                                                                 |
| 13  | Add consolidation and float decay columns…                                           | `superseded_by`, `relevance_score`                                                                                                                         |
| 14  | Add knowledge graph entity table                                                     | `memory_entities` (`entity_type`, entity id, etc.)                                                                                                         |
| 15  | Add knowledge graph relation table                                                   | `memory_relations` (`subject_id` / `object_id` / `predicate`)                                                                                              |
| 16  | Change `user_memories.importance` from INTEGER to REAL                               | Rebuild `user_memories`; `importance` is REAL                                                                                                              |
| 17  | Add idempotency_keys table for API idempotency                                       | `idempotency_keys`                                                                                                                                         |
| 18  | Add segments_json column and speaker_mappings table                                  | `meeting_files.segments_json`, `speaker_mappings`                                                                                                          |
| 19  | Add sources_json column to chat_messages for source provenance                       | `chat_messages.sources_json`                                                                                                                               |
| 20  | Add body hash to idempotency keys                                                    | `body_hash` + composite index                                                                                                                              |
| 21  | Add per-meeting content hash uniqueness for files                                    | Deduplication + `idx_meeting_files_meeting_hash_unique`                                                                                                    |
| 22  | Add typed file artefact columns on meeting_files                                     | `structured_json`, `summary`, `duration_seconds`, etc.                                                                                                     |
| 23  | Add pending_vector_deletions…                                                        | Orphan vector deletion queue                                                                                                                               |
| 24  | Add metrics_json to meeting_files artefacts                                          | `metrics_json`                                                                                                                                             |
| 25  | Add RAGAnything doc tracking columns…                                                | `raganything_doc_id`, `raganything_indexed_at`                                                                                                             |
| 26  | Add index_state table for cross-index consistency                                    | `index_state` (Chroma/RAGAnything consistency)                                                                                                             |
| 27  | Add processing_started_at timestamps…                                                | `processing_started_at` + index of `meetings` / `meeting_files` (for crash recovery)                                                                       |
| 28  | Add scope columns to user_memories and memory_entities                               | `meeting_ids` / `file_ids` scope columns                                                                                                                   |
| 29  | Flag pre-scope memories/entities as legacy…                                          | Flag scope legacy data before migration                                                                                                                    |
| 30  | Add conversational anchor columns to chat_sessions                                   | Conversational anchor columns                                                                                                                              |
| 31  | Add aliases column to memory_entities                                                | Entity aliases (canonical-name merging)                                                                                                                    |
| 32  | Migrate scope IDs from CSV columns to memory_scopes / entity_scopes junction tables  | `memory_scopes`, `entity_scopes` association tables                                                                                                        |
| 33  | Add file-level summary FTS5 index for hybrid routing                                 | File summary FTS5 index                                                                                                                                    |
| 34  | Add summary_status column to meetings table                                          | `meetings.summary_status`                                                                                                                                  |
| 35  | Add meeting_summaries table                                                          | `meeting_summaries` (meeting-level summaries)                                                                                                              |
| 36  | Add summary_status column to meeting_files table                                     | `meeting_files.summary_status`                                                                                                                             |
| 37  | Relax meetings.summary_status CHECK                                                  | Add `generating` + `lock_owner`                                                                                                                            |
| 38  | Relax meeting_files.status CHECK                                                     | Add `summarizing`                                                                                                                                          |
| 39  | Relax meeting_files.summary_status CHECK                                             | Add `generating`                                                                                                                                           |
| 40  | Relax meetings.status CHECK                                                          | Add `summarizing`                                                                                                                                          |
| 41  | Add user_id columns to meetings and meeting_files                                    | Multi-tenant data isolation                                                                                                                                |
| 42  | Add memory_audit_log table                                                           | Memory life cycle audit log                                                                                                                                |
| 43  | Add vector_state column to user_memories                                             | Vector synchronization status tracking                                                                                                                     |
| 44  | Add attempts column to pending_vector_deletions for retry tracking                   | `pending_vector_deletions.attempts` (retry count tracking)                                                                                                 |
| 45  | Add composite index on chat_messages(session_id, id) for efficient DESC pagination   | `idx_messages_session_id_desc`                                                                                                                             |
| 46  | Add FK constraints to file_summary_bm25 for cascade deletion                         | File summary index cascade foreign key to `meeting_files` / `meetings`                                                                                     |
| 47  | Add expires_at to memory_audit_log and composite desc indexes for sessions/memories  | Audit log expiration column, session/memory reverse paging index                                                                                           |
| 48  | Add kv_state key-value table for cross-worker coordination (breaker, advisory locks) | `kv_state(key, value, updated_at)`                                                                                                                         |
| 49  | Add trigram FTS5 index for CJK BM25 retrieval                                        | `bm25_chunks_cjk` and synchronization triggers                                                                                                             |
| 50  | Add lifecycle fields to deferred index deletions                                     | `status`, `last_error`, `updated_at` and retry/dead-letter index                                                                                            |
| 51  | Add observable account deletion batches                                              | `account_deletion_requests` and `pending_vector_deletions.deletion_batch_id`                                                                                |
| 52  | Harden deferred deletion ownership and leasing                                       | per-resource uniqueness, worker leases, and account-deletion ownership                                                                                      |

### 3.5 No rollback and rollback strategy

- **Legacy Migration**: Designed as forward-only; production rollback requires backup and restore or hand-written reverse SQL.
- **Alembic**: `downgrade()` destructively deletes tables in baseline revision (only suitable for dev/test); production mainly uses **forward migration**.

### 3.6 Relationship with [`operations/alembic.md`](./operations/alembic.md)

- If an existing database is connected to Alembic for the first time: `alembic stamp` is required to align the baseline, see the operation and maintenance documentation.
- **`schema_version` is frozen at v52** as the legacy compatibility baseline.
- All changes after v52 use one source of truth: **Alembic revisions only**.
- As verified on 2026-09-10, the single Alembic head is
  **`20260908_000003`** (`idempotency_lifecycle`). Run `uv run alembic heads`
  rather than copying this value into operational automation.
- Fresh test/bootstrap schemas may include the cumulative current schema, but
  must not append a second numbered legacy migration for a new revision.

## 4. Main table (logical model)

The following summarizes the **current cumulative schema**. For a migrated
database, the ordered Alembic revisions and the actual `sqlite3 .schema` are
authoritative; `_migrations.py` covers only the frozen v52 compatibility
baseline and cannot override later revisions.

### 4.1 `meetings`

Parent meeting aggregation row: title, description, `meeting_date`, `status` (including `uploading` / `processing` / `ready` / `failed` / `error` and other life cycles), `error_message`, `content_hash` (legacy), `processing_started_at` (v27), timestamp. The specific files in multi-file scenarios are in `meeting_files`.

### 4.2 `meeting_files`

One line for each uploaded file: `meeting_id`, `file_type`, `file_name`, `file_path`, `content_hash`, `status` (common in business `processing` → `ready` / `error`; retry logic also involves terminal state), `transcript`, structured columns (`structured_json` / `structured_kind`, `segments_json` etc.), `summary`, `duration_seconds`, `page_count`, `word_count`, `language`, `metrics_json`, RAGAnything and `index_state` linkage fields, `processing_started_at`, `error_message`, timestamp.
**Unique constraint**: Same `content_hash` under the same `meeting_id` to remove duplicates (see v21).

### 4.3 Chat sessions, messages, runs, and continuation state

Conversations and messages. The `chat_messages.role` constraint uses `system` / `human` / `ai`, which may differ from API presentation-layer names. `sources_json` (v19) carries citation provenance, and `chat_messages_fts` (v11) provides cross-session FTS5.
`chat_sessions` also records branch ancestry (`parent_session_id`,
`branched_from_message_id`, `branch_reason`). `chat_runs`, `chat_run_events`, and
`chat_context_checkpoints` persist the streaming lifecycle, replayable events,
cancellation state, and checksum-bound continuation context.

### 4.4 `user_memories` / `memory_decay_state`

`user_memories` materializes the current logical fact: stable key/value,
fact/lifecycle/action fields, project and subject-predicate-object identity,
salience/freshness/confidence/usefulness, business validity, source/evidence,
revision, archive state, and vector synchronization state.
`memory_fact_versions` is the append-only bitemporal revision ledger;
`memory_scopes` carries normalized meeting/file scope;
`memory_profile_provenance` binds a generated profile to exact source
revisions; `memory_query_epochs` invalidates stable review/query paging when
the underlying fact set changes. `memory_decay_state` records per-user decay
progress.

### 4.5 Knowledge graph: `memory_entities` / `memory_relations`

Entities are unique by `(user_id, name, entity_type)`; `aliases` column (v30) supports canonical-name merging. The relationship table uses **`subject_id` / `object_id` foreign key** to point to the entity id, and `predicate` represents the relationship type (not the "name to name" handwritten line in the early documentation).

### 4.6 BM25/FTS5

- **`bm25_index` + `bm25_stats`**: BM25 mirror-index metadata and global statistics.
- **`bm25_chunks`**: FTS5 virtual table, `content='bm25_index'`, trigger synchronization.
- **`chat_messages_fts`**: Chat content FTS5.
- **`file_summary_bm25` + `file_summary_fts`**: BM25 and FTS5 indexes of file summaries (v33).

To inspect or repair legacy BM25 metadata, run the configured database in
read-only preview mode first:

```bash
uv run python -m scripts.migrate_bm25_metadata --dry-run
uv run python -m scripts.migrate_bm25_metadata --db /path/to/meetings.db --dry-run
```

Remove `--dry-run` only after backing up the selected database. The repair
recovers `file_id` from canonical chunk IDs and preserves the authoritative
`chunk_id` column in the metadata JSON.

For mixed search, see [`rag.md`](./rag.md).

### 4.7 `session_summaries`

Cache summaries, topics, entities, rounds, `embedding_id`, etc. by session dimensions for cross-session retrieval and summary vectors (the structure is authoritative for v10 and subsequent code queries).

### 4.8 Other operation and maintenance related tables

- **`idempotency_keys`**: HTTP idempotent keys and encrypted response cache metadata.
- **`pending_vector_deletions`**: deferred deletion queue across Chroma, BM25,
  RAGAnything and summary indexes. `attempts` (v44) tracks retries; lifecycle
  fields (v50) retain exhausted jobs as `dead_letter` with `last_error` instead
  of silently deleting them.
- **`account_deletion_requests`**: privacy-safe, opaque deletion batch records used
  to report whether external file/vector cleanup is pending, completed, or needs retry.
- **`index_state`**: File-level native generation, index-config fingerprint, Chroma/BM25 counts, manifest checksum, repair flag, and RAGAnything state. Reconciliation verifies physical stores instead of inferring health from timestamps.
- **`meeting_summaries`**: Meeting-level summaries (v33), linked with `meetings.summary_status` / `meeting_files.summary_status`.
- **`memory_audit_log`**: Memory lifecycle audit log (v42).
- **`speaker_mappings`**: Speaker and speaker mapping (v18).
- **`memory_scopes`**: Memory-session/file-scope association table (v32).
- **`entity_scopes`**: Entity-meeting/file scope association table (v32).
- **`durable_jobs`**: idempotent background work with payload, priority,
  attempts, lease owner/expiry, retry availability, terminal timestamps and
  `dead_letter` state. Added by Alembic revision `20260903_000006`.
- **`projects` / `project_files`**: revision-checked project directory, aliases,
  and explicit material membership used by the Memory project workspace.
- **`meeting_file_semantic_events`**: immutable history of material role,
  approval, domain, and source-revision changes.
- **`chat_runs` / `chat_run_events` / `chat_context_checkpoints`**: durable chat
  execution journal, replay stream, cancellation/withdrawal state, and frozen
  continuation context.

## 5. Repository layer

| Documentation | Responsible |
|---|---|
| `meetings.py` | meetings / meeting_files CRUD, status, search |
| `chat.py` | sessions, messages, FTS, summaries |
| `memories.py` | user_memories, decay, consolidation support |
| `knowledge_graph.py` | entities/relations |
| `bm25.py` | BM25 maintenance and query |
| `_connection.py` | Connection pool, lock, PRAGMA |
| `_migrations.py` | `init_db()`, `_apply_migration()` (internal), import `_MIGRATIONS` from `_migration_definitions.py` |
| `_migration_definitions.py` | Frozen `_MIGRATIONS` migration list (52 items), plus cumulative `SCHEMA_SQL` for fresh test/bootstrap databases |
| `_migration_helpers.py` | Migration helper functions (column checking, SQL splitting, etc.) |
| `_migration_lock.py` | Migration lock (prevent concurrent migration) |
| `_scopes.py` | Memory/entity scope (meeting_ids/file_ids) auxiliary query |
| `idempotency.py` | Idempotent key storage and encrypted response caching |
| `index_state.py` | Multi-index (Chroma/RAGAnything) consistency reconciliation |

All public DB functions are synchronous; the routing/service layer is wrapped with `asyncio.to_thread`.

## 6. Concurrency semantics and transactions

- **Write lock**: one process-wide `threading.RLock` serializes writers to match SQLite's one-writer model
- **Read Concurrency**: Multiple reads without blocking under WAL
- **Transaction Boundary**: The outermost `get_write_connection()` owns commit/rollback; nested write contexts use SQLite savepoints and never commit the outer transaction early.
- **Cross-table atoms**: executed within the same `get_write_connection()` block

## 7. Typical performance optimization

- Hot column index: `meetings(status)`, `meeting_files(meeting_id)`, `processing_started_at`, etc.
- FTS5 replaces `%LIKE%` large table scan
- Batch write: `executemany`
- Avoid extremely large `IN` lists (the upper limit of SQLite variables is about 999/32766 depending on the version, large batches should be fragmented)

## 8. Common maintenance operations

```bash
cd backend

# Schema upgrade equivalent to uvicorn startup (Alembic)
uv run alembic upgrade head

# Without Alembic, only apply legacy migration queue (testing/troubleshooting)
uv run python -c "from src.core.database import init_db; init_db()"

# FTS5 rebuild (when corrupted or out of sync)
uv run python -c "
from src.core.database._connection import get_write_connection
with get_write_connection() as c:
    c.execute(\"INSERT INTO chat_messages_fts(chat_messages_fts) VALUES('rebuild')\")
"

# VACUUM
uv run python -c "
from src.core.database._connection import get_write_connection
with get_write_connection() as c:
    c.execute('VACUUM')
"

# Hot backup to another file
sqlite3 data/meetings.db ".backup data/meetings.bak.db"
```

## 9. Trap

1. **Write path**: Forgot `get_write_connection()` → lock contention and `database is locked`
2. **Foreign keys**: Depends on `PRAGMA foreign_keys=ON` (`init_db` / will be checked after the application starts)
3. **Change historical migration**: Never rewrite or append to the frozen v52
   `_MIGRATIONS` tuple. Add one new forward Alembic revision instead.
4. **Documentation and DDL**: The interface layer Pydantic field names may not be exactly the same as the SQLite column names. The SQL of `*_migrations.py` and `core/database/*.py` shall prevail.
5. **Time**: Business and log agreement UTC (`datetime.now(timezone.utc)`)
