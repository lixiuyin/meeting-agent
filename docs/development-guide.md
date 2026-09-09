# Development guide

This guide is for contributors who need to understand where a change belongs,
how to verify it, and which documentation must move with it.

## Repository map

| Path                                      | Responsibility                                                             |
| ----------------------------------------- | -------------------------------------------------------------------------- |
| `backend/src/api/`                        | FastAPI routers, middleware, lifespan, metrics, WebSocket                  |
| `backend/src/core/`                       | Configuration, security, SQLite repositories, migrations, logging, tracing |
| `backend/src/models/`                     | Pydantic request/response schemas and domain enums                         |
| `backend/src/services/processor/`         | Upload processing orchestration and modality-specific processors           |
| `backend/src/services/parser/`            | Document parser routing and provider adapters                              |
| `backend/src/services/asr/`, `transcriber.py`, and `vision/` | AssemblyAI transcription facade and image/video enrichment      |
| `backend/src/services/rag/`               | Chunking, indexing, lexical/vector retrieval, routing, reranking           |
| `backend/src/services/chain/` and `llm/`  | Prompt assembly, generation, streaming, provider traffic                   |
| `backend/src/services/memory/` and `knowledge_graph/` | Revisioned facts, decay, profiles, sessions, entities, and relations |
| `backend/src/services/jobs.py`             | SQLite durable jobs, leases, retries, cancellation, and dead-letter handling |
| `backend/tests/`                          | Unit, integration, property, benchmark, and contract coverage              |
| `frontend/src/`                           | React pages, API clients, viewers, i18n, and UI tests                      |
| `frontend/docs/`                          | Frontend architecture, browser contracts, and frontend quality gates       |
| `docs/`                                   | Overall guides, diagrams, ADRs, and benchmark design                       |
| `backend/docs/`                           | Backend implementation-level subsystem documentation                       |

The data-flow overview is [`backend/docs/architecture.md`](../backend/docs/architecture.md).
The frontend implementation map is [`frontend/docs/architecture.md`](../frontend/docs/architecture.md).

## Local quality loop

```bash
# Backend
cd backend
uv sync --dev
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pyright
uv run python -m pytest

# Frontend
cd ../frontend
npm install
npm run lint
npm run type-check
npm run test:run
npm run build
```

From the repository root, `make lint`, `make test`, and `make qa` provide the
project-level shortcuts. Tests use temporary data through `conftest.py`; do
not point them at a production `data/meetings.db`.

## Change workflow by feature

### Ingestion or parser changes

Trace the real caller from the upload router through the processor and update
[`backend/docs/ingest-pipeline.md`](../backend/docs/ingest-pipeline.md). Add
fixtures for valid signatures, malformed input, provider failure, partial
output, and retry behavior where relevant.

### RAG changes

Keep score semantics, metadata, chunk IDs, scope behavior, and fallback paths
explicit. Update [`backend/docs/rag.md`](../backend/docs/rag.md) or
[`chunk_retrieval.md`](chunk_retrieval.md), and add a retrieval regression test
before tuning thresholds.

### API changes

Update the Pydantic schema, router, OpenAPI behavior, client types, API tests,
[`backend/docs/api-reference.md`](../backend/docs/api-reference.md), and the
short example in [`api-quickstart.md`](api-quickstart.md). Update the affected
frontend client and [`frontend/docs/architecture.md`](../frontend/docs/architecture.md)
when the browser contract changes. Preserve
`request_id`, error envelope, authentication, ownership, and idempotency
semantics unless the change explicitly revises the contract.

### Configuration changes

Update `backend/src/core/config.py`, `backend/config/main.yaml`,
`backend/.env.example`, settings schemas/validation, tests, and
[`backend/docs/configuration.md`](../backend/docs/configuration.md). State
whether a restart, vector rebuild, multimodal rebuild, or full reprocess is
required.

### Database changes

Add a migration under `backend/alembic/versions/`, update database docs and
ADR material when the storage boundary changes, and test both a fresh database
and an upgrade from a representative existing database. Read
[`backend/docs/operations/alembic.md`](../backend/docs/operations/alembic.md).
Do not append to the frozen v52 `_MIGRATIONS` tuple for post-baseline changes.

### Frontend layout changes

Treat viewport containment as a behavior contract, not only a screenshot
detail. A full-height page with controls above a list must use one bounded flex
column: fixed controls stay `flex: 0 0 auto`, the list wrapper uses `flex: 1`
and `min-height: 0`, and only the intended inner region scrolls. Do not combine
an out-of-flow header or selector with a sibling list that still has
`height: 100%`; their heights add and can push content below the page card.

Add Playwright geometry assertions for both the containing card and the scroll
region, test the affected breakpoint, then inspect the production build at the
reported viewport. For Memory-page changes, the focused entry point is
`frontend/e2e/memory.spec.ts`.

## Streaming contract

Streaming code has two independent contracts: SSE chat/summary events and
WebSocket processing notifications. Add tests for event ordering, terminal
events, disconnects, malformed input, and provider errors. Clients must be able
to ignore unknown event types so the protocol can evolve.

## Benchmarking

The executable benchmark CLI is run from `backend/`:

```bash
uv run python -m scripts.benchmark chat --iterations 5
uv run python -m scripts.benchmark ingest --iterations 3
uv run python -m scripts.benchmark micro
uv run python -m scripts.benchmark rag-all
```

Benchmark design material is under [`benchmark/`](benchmark/), while the
current runner, fixtures, output format, and interpretation are in
[`backend/docs/benchmarking.md`](../backend/docs/benchmarking.md). Report the
configuration, fixture version, provider/model, iteration count, and whether a
result is synthetic or production-like. Do not present a documented baseline
as a rerun result. When publishing model-specific findings, also state the run
date, sample count, reasoning/token/timeout settings, gate thresholds, routing
boundary, and whether provider endpoints were pinned. Different sample counts
or configurations are not a head-to-head comparison. Keep raw reports and
private holdouts ignored; publish only sanitized aggregates, fingerprints,
hashes, failures, and explicit evaluated/skipped counts. The complete claim
boundary is in
[`backend/docs/benchmarking.md#publishing-benchmark-and-model-results`](../backend/docs/benchmarking.md#publishing-benchmark-and-model-results).

## Documentation review checklist

Before opening a change:

- [ ] The nearest subsystem document describes the new behavior and limits.
- [ ] Commands use the current package manager, paths, ports, and route prefix.
- [ ] Secrets and production URLs are represented as placeholders.
- [ ] API examples include auth and show asynchronous status where applicable.
- [ ] Links resolve from the file containing them.
- [ ] Model and benchmark claims are dated and scoped to the tested workload,
      route, configuration, and gates; route-specific failures are not described
      as universal model availability or quality conclusions.
- [ ] A regression test covers the failure mode or contract that motivated the change.
- [ ] Rendered UI, diagrams, or generated OpenAPI behavior was checked when affected.
- [ ] Full-height workspaces keep their inner scroll region within the visible
      card at desktop and mobile breakpoints.
