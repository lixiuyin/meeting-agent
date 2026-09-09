# Testing, Quality Gates, and CI

The backend test suite covers unit, integration, property, chaos, benchmark,
API-contract, persistence, and provider-boundary behavior. External LLM, ASR,
OCR, search, and vector services are not default online dependencies; provider
bindings, fixtures, fake servers, and lazy-import fallbacks keep local and CI
runs reproducible. Frontend and browser tests are documented in
[`../../frontend/docs/testing.md`](../../frontend/docs/testing.md).

## 1. Backend tests

`backend/pyproject.toml` configures pytest:

- `testpaths = ["tests"]` and `asyncio_mode = auto`;
- markers: `unit`, `integration`, `benchmark`, `property`, and `chaos`;
- coverage source `src`, with a 60% total minimum in CI;
- Ruff targets Python 3.12, line length 100, and E/F/I/W/UP/B/SIM/C4/RUF rules;
- Pyright and Bandit configuration also lives in `pyproject.toml`; a green pytest run alone is insufficient.

```bash
cd backend
uv sync --dev
uv run pytest -q
uv run pytest --cov=src --cov-report=term-missing --cov-fail-under=60
uv run pytest -m unit -q
uv run pytest -m integration -q
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyright
uv run bandit -r src -ll
```

Test databases, temporary upload directories, Chroma collections, and background-task registries must be isolated. Async tests must wait for or cancel every task and close HTTP clients, executors, and database connections.

## 2. Required behavior coverage

### API and contracts

- Pydantic field boundaries, datetime serialization, enums, and the unified error envelope;
- API keys, dev mode, ownership, and file/WS token expiry and scope;
- 429 rate limiting, Retry-After, request IDs, and sanitized 5xx errors;
- OpenAPI routes and response models, especially upload, source, SSE, and settings-rebuild contracts.

### Data and indexes

- SQLite WAL, thread-isolated connections, write locks, busy timeouts, and rollback;
- consistency between legacy `schema_version` and Alembic revisions;
- cascading meeting/file deletion, pending vector deletion retry, and dead-letter behavior;
- Chroma/BM25/`index_state` writes, rebuild, reconciliation, and failure recovery;
- content-hash idempotent uploads, duplicate requests, and retryability after interruption.

### Processing pipeline

- parser registry branches for PDF/PPT/DOC/XLS/CSV/TXT/images/audio/video;
- ASR polling, speaker diarization, OCR/VLM, and derived pages/keyframes/tables;
- vector, hybrid, multimodal, and hybrid_multimodal RAG selection, fallback, ownership isolation, reranking, and context truncation;
- LLM timeouts, rate limits, unavailable providers, partial results, durable fact-extraction enqueue/execution failures, and dead-letter behavior;
- session memory, automatic summaries, decay, consolidation, KG entity merging, and single-process coordination boundaries.

### Streaming and long-running work

- SSE step/token/sources/trace/web-results/error/done/heartbeat events, sequence numbers, and unknown-event compatibility;
- cancellation, disconnect, reconnect, duplicate events, and error-before-done behavior;
- WebSocket token, client-ID collision, ping/pong, idle timeout, and maximum lifetime;
- supervised-task backoff/restart and graceful-shutdown cancellation/persistence;
- atomic shadow vector rebuild, old-index preservation on failure, and conflict response for concurrent rebuilds.

## 3. CI and security gates

`.github/workflows/ci.yml` covers Python 3.12/3.13. The main backend order is locked dependency installation, Alembic-head check, env-example synchronization, Ruff, format, Pyright, pytest coverage, pip-audit, Bandit, SBOM, and container build. The supported frontend matrix uses Node 22/24 for dependency installation, type-check, lint, unit/coverage, and build. The OpenAPI type-generation job and current frontend Docker build stage still use Node 20; they are known tooling/build-path mismatches with `frontend/package.json`'s `>=22 <26` engine range, not supported-runtime evidence. E2E runs in its dedicated job. CI does not install heavyweight optional parser/provider extras by default; code must lazy-import them gracefully, with mocks or dedicated environments validating the optional paths.

The nightly workflow is primarily manual and includes full-stack E2E, targeted Mutmut, frontend Stryker, and multi-run benchmark comparisons. The security workflow periodically runs blocking Trivy checks for HIGH/CRITICAL container vulnerabilities and an OSV dependency gate. CodeQL is blocking when repository code scanning is available and explicitly skipped otherwise. These jobs are not equivalent to external-service integration validation on every pull request; reports must distinguish skipped and genuinely passing steps.

## 4. Benchmarks and regression

Benchmarks compare retrieval, answer, indexing, and processing performance and should not be mixed with ordinary feature tests. Record code version, configuration snapshot, provider/model, dataset version, warm/cold state, iterations, latency, tokens, error rate, recall/citation quality, and hardware. Save a comparable baseline when changing chunking, embeddings, reranking, prompts, or schema. See [`../../docs/development-guide.md`](../../docs/development-guide.md), [`../../docs/benchmark/`](../../docs/benchmark/), and [`benchmarking.md`](./benchmarking.md).

## 5. Failure triage order

1. Record the complete command, redacted environment, request ID, marker, and failing fixture.
2. Separate code failure, unavailable external provider, missing optional dependency, missing dataset, and resource/timeout failure.
3. Reproduce the smallest test, then run the regression group for the same marker; do not hide a race by increasing timeouts.
4. For database or index failures, inspect transactions, schema version, `index_state`, Chroma/BM25 counts, and pending deletions.
5. Add a regression test that fails reliably in CI and record whether real services or manual acceptance are required.
