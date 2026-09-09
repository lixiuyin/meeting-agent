# Observability and Incident Diagnosis

This document describes the implemented logging, request tracing, pipeline traces, Prometheus metrics, and health probes. It answers four operational questions: did the request arrive, where did it spend time, did a dependency fail, and is the data layer usable? It does not claim that an external Grafana or Alertmanager deployment is included.

## 1. Observability surfaces

A request can be correlated through the following chain:

```text
X-Request-ID / response header
        ↓
HTTP access log + request_id
        ↓
chat response trace / trace_id
        ↓
matching trace_id in data/logs/pipeline.jsonl
        ↓
Prometheus counters/histograms aggregated by route, provider, and status
```

| Surface | Default location/entry point | Purpose |
|---|---|---|
| Application logs | stderr + `data/logs/app.log` | Startup, HTTP, provider, exception, and background-task events |
| Development console log | `data/logs/dev-console.log` when used by `make dev` | Local session boundaries and terminal output |
| Request ID | `X-Request-ID` request/response headers | Correlating one HTTP request across logs |
| Pipeline trace | `trace` in chat responses and `data/logs/pipeline.jsonl` | Timing and outcome of retrieval, reranking, generation, and memory stages |
| Prometheus | `GET /metrics` | Long-term trends, capacity, error rates, and dependency latency |
| Health probes | `/api/v1/health/*` | Liveness, readiness, traffic, and index consistency |

Logs and traces may contain a question fragment, session ID, error message, or file count. Production deployments should restrict log-directory permissions, define retention, and avoid exposing logs directly to end users.

## 2. Logging

### 2.1 Configuration and format

- `LOG_LEVEL` defaults to `INFO`. `DEBUG` increases application detail, while noisy loggers such as `httpcore`, Chroma, and SQLAlchemy remain separately capped.
- `LOG_FORMAT=text` (default) produces readable console output; `LOG_FORMAT=json` produces structured JSON on the console.
- The file handler always uses text format and writes to `DATA_DIR/logs/app.log`; each file is limited to 10 MB with five rotated backups.
- Startup banners are written to `app.log` and, when present, `dev-console.log` to mark process lifetimes.
- JSON console fields include `ts`, `level`, `logger`, and `msg`, with optional `request_id`, `session_id`, and `exc` fields.
- httpx/httpcore logs redact `Authorization` and API-key-shaped values as `<REDACTED>`; application code should still never log secrets intentionally.

Key sources: `backend/src/core/logging.py` and `backend/src/api/middleware.py`.

### 2.2 Request IDs

`RequestIdMiddleware` prefers the incoming `X-Request-ID`, keeps only alphanumeric characters, `-`, and `_`, and limits it to 32 characters. If the header is absent or becomes empty after sanitization, the middleware generates a 16-character UUID hex value. Responses include:

- `X-Request-ID: <request_id>`
- `X-Response-Time: <milliseconds>ms`

When diagnosing one API request, save the client-side request ID and search for it in `app.log`. A request ID is a correlation field, not an identity or credential.

### 2.3 Pipeline traces and JSONL

Each chat `ask()` / `ask_stream()` execution creates a `TraceContext`. Top-level trace fields are `trace_id`, `total_ms`, and `spans`. A span contains at least:

- `label`, `phase`, `duration_ms`, and `status` (`running`, `success`, or `error`);
- optional `parent_label`, `skipped`, `tokens_in`, `tokens_out`, and `docs_retrieved`;
- `error_type` and truncated `error_message` on failure;
- metadata subject to size limits.

The flattened event written to `data/logs/pipeline.jsonl` contains a SHA-256
question fingerprint and character count, never raw question text. It also
contains total and per-span durations, status, session, skill, token counts,
and the first internal error span. Structured logs and rotated copies are
created with mode `0600`; their directory is `0700`. The file rotates to
`pipeline.jsonl.1` after exceeding 50 MB. I/O failure is best effort and does
not fail an answer.

Typical diagnosis:

1. Search `pipeline.jsonl` for the `trace.trace_id` returned by the response.
2. Inspect `status` and `error_span` to locate a failure in `retrieve`, `rerank`, `generate`, `memory`, or the wrapper layer.
3. Correlate with `app.log`, then use provider metrics to distinguish local logic from an external dependency.

## 3. Prometheus metrics

`GET /metrics` returns the Prometheus text exposition format. Authentication follows this order:

1. If `PROMETHEUS_API_KEY` is set, use it in `X-API-Key`.
2. Otherwise, the endpoint falls back to `API_KEY`.
3. In production, access is fail-closed when no key is configured; in dev without a key, access is allowed.

The main metric groups are listed below. The complete names are defined in `backend/src/core/metrics.py` and in the runtime exposition:

| Category | Example metrics | Main question |
|---|---|---|
| HTTP | `http_request_duration_seconds` | Route latency and status distribution |
| Uploads | `meeting_upload_total` | Upload success/failure trend |
| Chat/RAG | `chat_request_total` | Ratio of `casual`, `retrieval`, and `search` intents |
| LLM | `llm_request_duration_seconds`, `llm_request_total` | Provider latency and success/timeout/error counts |
| Traffic control | `traffic_controller_inflight`, `traffic_controller_breaker_state` | In-flight capacity and breaker state; 1/0.5/0 means closed/half-open/open |
| Background and SQLite | `background_task_failures_total`, `background_task_exhausted_total`, `sqlite_busy_timeouts_total` | Supervised task failures/exhaustion and write-lock contention |
| Context | `context_step_timeout_total`, `context_step_error_total` | Best-effort memory/entity/history/web branches |
| Episodic memory | `session_summary_search_total` | Effective hybrid/vector/FTS/fallback path for past-session recall |
| RAG routing | `summary_router_request_total`, `summary_router_files_routed` | Summary-router hits and returned file counts |
| RAG quality signals | `fair_retrieve_chunks_per_file`, `funnel_narrow_scope_size`, `funnel_narrow_evidence_filter_ratio` | Fair allocation, funnel scope, and Evidence Filter aggressiveness |
| Indexing | `summary_vector_upsert_failures_total`, `summary_coverage_ratio`, `index_repair_pending` | Summary-vector failures/coverage and native generation repairs |
| Data lifecycle | `pending_vector_deletion_jobs`, `vector_deletion_cleaned_total`, `app_data_disk_usage_ratio` | Deferred cleanup backlog and data-volume capacity |

Do not treat a counter's current value as a success rate; use `rate()` over a time window. Use histogram `_bucket`, `_sum`, and `_count` series to calculate latency quantiles. Metrics are registered in process memory. A single instance exposes the most complete state; multiple workers or instances require Prometheus to scrape and aggregate each target, and the traffic controller's semaphore, token bucket, and breaker state are not shared across processes.

## 4. Health probes and triage order

| Entry point | API key required | Semantics |
|---|---:|---|
| `/api/v1/health/live` | No | The process responds; suitable for a liveness probe |
| `/api/v1/health/ready` | No | Critical startup dependencies are ready; returns 503 when degraded |
| `/api/v1/health` | No | Same dependency checks and 503-on-degraded behavior for legacy clients |
| `/api/v1/health/traffic` | Yes | Breaker, error-rate, token, and in-flight state |
| `/api/v1/health/index-consistency` | Yes | Chroma/RAGAnything and SQLite metadata consistency |
| `GET /metrics` | Configuration-dependent | Prometheus scrape endpoint |

Recommended order:

1. If `live` fails, inspect the process, port, container, and startup logs.
2. If `live` succeeds but `ready` fails, inspect the local startup state, migrations, SQLite/FTS, durable worker, native-index manifest ledger, and storage mounts. Readiness deliberately does not call Chroma or paid providers; startup/repair reconciliation records verified Chroma/BM25 generations in SQLite for this cheap probe.
3. Use authenticated `/health/capabilities` for LLM, embedding, and Chroma probes, and `/health/index-consistency` for manifest/repair state.
4. If `traffic` is open or in-flight requests remain full, inspect provider 429/5xx responses, timeouts, rate limits, and the controller configuration in [`llm-and-traffic.md`](./llm-and-traffic.md).
5. If answers lack evidence, inspect retrieve/rerank spans, RAG metrics, and `/health/index-consistency`.
6. If an upload is stuck, inspect `meeting_upload_total`, processor spans, background-task failures, and the relevant runbook.

For operations, backup/recovery, and index rebuilding, see [`../../docs/operations-guide.md`](../../docs/operations-guide.md). API fields and the error envelope are documented in [`api-reference.md`](./api-reference.md).

## 5. Observability boundaries

- Traces and logs are best-effort diagnostic material, not an immutable audit ledger; use `core/audit.py` and its call sites for operation records.
- Prometheus metrics retain aggregate dimensions and do not provide complete questions, answers, or document contents.
- A healthy `/health` response does not prove answer quality; retrieval, reranking, and groundedness still require benchmarks and human/LLM-judge evaluation.
- This repository defines metrics and probes but does not guarantee that scrape jobs, alert rules, dashboards, or centralized logging are deployed; those are deployment deliverables.

## 6. Memory extraction completeness

Post-ingest fact extraction exposes `memory_ingest_extraction_windows_total`.
`memory_ingest_extraction_truncated_files_total` must remain zero for workloads
that require complete file coverage; a non-zero increase means the configured
The legacy truncation counter should remain zero. `MEMORY_INGEST_MAX_CHUNKS_PER_FILE`
now controls how often large-file scheduling yields to the event loop; it no longer
drops the remainder of a source file.
