# Application Lifespan and Runtime Operations

This document covers FastAPI startup/shutdown, database upgrades, background tasks, recovery, and routine operations.

Source locations: `backend/src/api/lifespan/`, `backend/src/api/middleware.py`, and `backend/src/services/processor/_recovery.py`.

For logs, request IDs, pipeline traces, Prometheus metrics, and probe diagnosis, see [`observability.md`](./observability.md). For authentication and production security boundaries, see [`security-and-tenancy.md`](./security-and-tenancy.md).

## 1. Critical versus best-effort startup

`backend/src/api/lifespan/` divides startup into fail-closed invariants and
best-effort capability/recovery work:

- **Fail-closed path:**
  - configuration validation while `Settings` is constructed;
  - production worker-count guard (`UVICORN_WORKERS` must be 1);
  - runtime-directory permission hardening;
  - Alembic upgrade plus legacy/Alembic consistency verification;
  - non-development `API_KEY` requirement;
  - reranker consistency (`RERANKER_TOP_N >= TOP_K`).
- **Capability pre-warm** in `await run_critical_startup()`:
  - OpenTelemetry `setup_tracing()` (no-op without an endpoint);
  - optional Sentry through `SENTRY_DSN`;
  - `get_llm()` through `asyncio.to_thread`, with a 30-second timeout;
  - `init_traffic_controller()`;
  - `get_embeddings()` plus `embed_query("connectivity check")` to verify dimension and connectivity;
  - primary, memory, session-summary, and entity vector-store warm-up;
  - best-effort Skill matcher embedding warm-up and conditional RAGAnything validation/warm-up.
- **Best-effort recovery and loops** after pre-warm. Failures are logged and
  counted while the process continues:
  - embedded durable-job workers when `DURABLE_JOB_EXECUTION_MODE=embedded`;
  - stale meeting recovery;
  - session cache, expired memory, orphan-vector, and startup-decay maintenance;
  - supervised `memory_decay_loop` and hourly expired-memory purge;
  - FTS5 backfill, memory/entity `sync_missing_vectors`, and session-summary backfill/idle loop;
  - `rebuild_bm25_from_chroma()` and drift checks;
  - hourly WAL checkpoint, ten-minute multimodal index reconciliation, and daily retention purge

The outer lifespan keeps a failed fail-closed phase available for diagnosis in
development and records it in readiness; non-development startup aborts. LLM,
embedding, connectivity, and vector-store pre-warm failures are currently
recorded as degraded capabilities and do not by themselves abort startup. A
healthy process therefore does not guarantee that every configured remote AI
provider is available; use route behavior, capability diagnostics, and metrics
in addition to liveness/readiness.

## 2. Database upgrade: the first startup action

`lifespan.__aenter__` begins with:

```text
await asyncio.to_thread(run_alembic_upgrade)
```

`lifespan._critical.run_alembic_upgrade` behaves as follows:

- run `command.upgrade(alembic_cfg, "head")` when Alembic and `backend/alembic.ini` are available;
- production fails closed if either is unavailable;
- development alone may use the frozen legacy `init_db()` bootstrap for diagnostics and tests.

Operationally, assume **production startup means Alembic at head**, not only `init_db()`.

## 3. Startup security guard

Before initializing the LLM, `run_critical_startup()` checks:

```python
if settings.ENVIRONMENT != "dev" and not settings.API_KEY.get_secret_value():
    raise RuntimeError(
        f"API_KEY must be set when ENVIRONMENT={settings.ENVIRONMENT!r}. "
        "Set API_KEY in your .env file or environment variables."
    )
```

Non-dev environments must configure `API_KEY`; dev is exactly `settings.ENVIRONMENT == "dev"`.

### 3.1 Sentry filtering

When `SENTRY_DSN` is set, lifespan registers a `before_send` callback that removes `X-API-Key`, `Authorization`, and similar request headers, cleans sensitive user context, and prevents API keys or database paths from entering error messages.

## 4. Simplified startup sequence

```text
uvicorn → lifespan.__aenter__
   │
   ├── [fail closed] harden runtime permissions
   ├── [fail closed] await asyncio.to_thread(run_alembic_upgrade)
   │   - Alembic upgrade head; dev-only frozen legacy bootstrap fallback
   │
   ├── await run_critical_startup()
   │   - optional OTEL + Sentry
   │   - API_KEY guard in non-dev
   │   - API-key/reranker invariants; traffic initialization
   │   - best-effort LLM, embedding ping, and vector-store warm-up
   │   - memory, session, entity, and file-summary vector-store warm-up
   │   - orphan memory-vector cleanup
   │   - conditional RAGAnything validation and warm-up
   │
   ├── [durable] start embedded worker pool when configured
   ├── [best-effort] resume interrupted jobs + recover stale meetings
   │   - only rows stale for more than 5 minutes, avoiding races with active work
   │   - COALESCE(processing_started_at, updated_at) < now - 5 minutes
   │   - meeting_files → status='error', error_message='Processing interrupted'
   │   - meetings → status='failed'
   │
   ├── [best-effort] stale recovery loop every 15 minutes
   ├── [best-effort] BM25 drift loop every 6 hours
   ├── [best-effort] stale summary recovery and file-summary requeue
   ├── [best-effort] meeting-summary and rebuild-swap reconciliation
   ├── [best-effort] session cache, expired memory, pending-vector cleanup, startup decay
   ├── [best-effort] memory decay and hourly expired purge
   ├── [best-effort] FTS5 backfill, memory/KG/file-summary vector sync, index reconciliation
   ├── [best-effort] session-summary backfill and idle-summary loop
   ├── [best-effort] BM25 rebuild, drift check, and legacy metadata backfill
   ├── [best-effort] WAL checkpoint, index reconciliation, retention, idempotency cleanup
   ├── [best-effort] Skill matcher embedding warm-up
   └── exact ordering: backend/src/api/lifespan/
```

## 5. Graceful shutdown

`lifespan.__aexit__` approximately performs:

1. `_bg.cancel_all()` cancels supervised tasks (including embedded durable-job
   workers) and waits up to five seconds; claimed-job leases are released by
   worker cancellation handling;
2. persist session cache through `_persist_session_cache`;
3. stop memory decay;
4. persist/flush the shared Chroma wrapper through `persist_vectorstore()`;
5. close shared search, AssemblyAI, vision, reranker, and parser HTTP clients;
6. close pooled SQLite connections.

### 5.1 Two classes of background work

- **Long-running supervised tasks:** registered through `create_supervised_task` in `utils/supervised_task.py`.
- **Durable domain jobs:** stored in `durable_jobs` and claimed by
  `services/jobs.py` with leases, retries, dedupe and dead-letter state.

Do not assume there is one global `app.state.background_tasks` collection.

## 6. Stale-meeting recovery

If the process is killed or OOMs, a meeting or file may remain in `processing`. Recovery runs only when `status='processing'` and `COALESCE(processing_started_at, updated_at) < datetime('now', '-5 minutes')`.

The file row becomes `error` with the fixed message `Processing interrupted`; the meeting row becomes `failed`, and `processing_started_at` is cleared. Users can call `POST /meetings/{id}/reprocess` or reprocess one file. If needed, call `delete_meeting_chunks(meeting_id, file_id=...)` before retrying.

## 7. Middleware and request chain

```text
CORS (optional)
  ↓
RequestIdMiddleware        # X-Request-ID + X-Response-Time
  ↓
slowapi rate limiter       # default and route-specific limits
  ↓
JSON structured logging    # LOG_FORMAT=json
  ↓
FastAPI router
```

Middleware is installed by `setup_middleware()`, not at module import time, so pytest monkey-patching order is not disturbed.

## 8. Background-task inventory

| Task | Behavior | Example location |
|---|---|---|
| `memory_decay_loop` | Periodic decay | `services/memory/_service/_decay_sync.py` |
| `expired_purge_loop` | Hourly expired-memory cleanup | `api/lifespan/_loops.py` |
| `stale_recovery_loop` | Resume/recover interrupted processing every 15 minutes | `api/lifespan/_loops.py` |
| `bm25_drift_loop` | Detect BM25 drift every 6 hours | `api/lifespan/_loops.py` |
| `wal_checkpoint_loop` | Hourly WAL checkpoint | `api/lifespan/_loops.py` |
| `index_reconcile_loop` | Ten-minute multimodal, BM25, and summary-vector reconciliation | `api/lifespan/_loops.py` |
| `retention_loop` | Daily retention plus audit/vector-deletion cleanup | `api/lifespan/_loops.py` |
| idempotency cleanup | Periodic idempotency-key lifecycle cleanup | `api/lifespan/__init__.py` |
| `idle_summary_loop` | Periodic summaries for idle sessions | `api/lifespan/_loops.py` |
| Skill matcher pre-warm | Startup corpus/query embedding warm-up | `api/lifespan/__init__.py` |
| `durable_job_worker_loop` | Executes leased durable jobs | `services/jobs.py` |
| Upload processing | Durable `file_processing` job | `processor/_scheduler.py` |
| Fact extraction | Durable after chat persistence | `chain/_steps_generate.py` |
| File/meeting summary | Durable `file_summary` / `meeting_summary` jobs | `services/summaries.py`, `services/jobs.py` |
| Session summary | Startup backfill and idle-session loop | `memory/_summary_service.py` |
| Chroma persistence | Flush on shutdown | `rag/_vectorstore.py` |

## 9. Common operations

```bash
cd backend

uv sync --dev
uv run uvicorn src.main:app --reload

# Database upgrade equivalent to production startup
uv run alembic upgrade head

# Legacy migration fallback when Alembic is unavailable
uv run python -c "from src.core.database import init_db; init_db()"

uv run python -m src.mcp
uv run python -m pytest -x
```

## 10. Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| `API_KEY must be set` | Missing key in non-dev | Configure `API_KEY` |
| Embedding ping blocks | Upstream unavailable | Check `EMBEDDING_*` and network access; production startup fails when the active retriever requires it |
| Alembic or `init_db` fails | Migration conflict or corrupt database | Back up the DB; inspect `schema_version` and `alembic_version` |
| Meeting remains `processing` | Work is active or has not passed the grace period | Wait at least five minutes or restart to trigger recovery |
| Task-destroyed warning on shutdown | Task was not registered in either lifecycle group | Use `create_supervised_task` or register it with the chain task manager |

## 11. Observability touchpoints

- **Logs:** `LOG_FORMAT=json`;
- **Trace:** spans from `core/trace.py`; ingestion emits `ingest_trace` JSON through the logger, not a separate trace table;
- **Metrics:** `GET /metrics`;
- **Health:** `GET /api/v1/health`;
- **WebSocket:** `WS /api/v1/ws` for progress and completion events.
