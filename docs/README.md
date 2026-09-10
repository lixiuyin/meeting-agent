# Meeting Agent documentation

This directory is the repository-level documentation hub. It explains how to
use, integrate, operate, develop, and evaluate Meeting Agent. Backend
implementation notes live in [`backend/docs/`](../backend/docs/README.md), and
frontend implementation notes live in [`frontend/docs/`](../frontend/docs/README.md).

**Last implementation reconciliation:** 2026-09-10. The maintained documents
below were checked against the router registry/OpenAPI output, `Settings`, the
canonical file-kind/parser registries, Alembic head, the React route tree, and
Compose/Nginx/Helm configuration.

## Choose a path

| You want to…                           | Start here                                                                                                                         | Then read                                                                                                                                                                        |
| -------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Run the application locally            | [`getting-started.md`](getting-started.md)                                                                                         | [`configuration.md`](../backend/docs/configuration.md), [`ingest-pipeline.md`](../backend/docs/ingest-pipeline.md)                                                               |
| Use the web UI end to end              | [`getting-started.md`](getting-started.md#first-workflow)                                                                          | [`frontend/docs/architecture.md`](../frontend/docs/architecture.md), [`data-lifecycle.md`](data-lifecycle.md)                                                                    |
| Integrate with REST, SSE, or WebSocket | [`api-quickstart.md`](api-quickstart.md)                                                                                           | [`api-reference.md`](../backend/docs/api-reference.md)                                                                                                                           |
| Connect an MCP client                  | [`api-quickstart.md`](api-quickstart.md#mcp)                                                                                       | [`mcp-server.md`](../backend/docs/mcp-server.md)                                                                                                                                 |
| Understand the architecture            | [`diagrams/architecture.md`](diagrams/architecture.md)                                                                             | [`architecture.md`](../backend/docs/architecture.md), [`chain-pipeline.md`](../backend/docs/chain-pipeline.md)                                                                   |
| Understand Memory from the UI          | [`frontend/docs/architecture.md`](../frontend/docs/architecture.md#memory-workspace)                                               | [`memory-and-kg.md`](../backend/docs/memory-and-kg.md), [`diagrams/memory-and-kg.md`](diagrams/memory-and-kg.md)                                                                 |
| Tune retrieval quality                 | [`chunk_retrieval.md`](chunk_retrieval.md)                                                                                         | [`rag.md`](../backend/docs/rag.md)                                                                                                                                               |
| Operate a deployment                   | [`operations-guide.md`](operations-guide.md)                                                                                       | [`lifespan-and-operations.md`](../backend/docs/lifespan-and-operations.md), [`observability.md`](../backend/docs/observability.md), [`operations/`](../backend/docs/operations/) |
| Change the code safely                 | [`development-guide.md`](development-guide.md)                                                                                     | [`CONTRIBUTING.md`](../CONTRIBUTING.md), [`architecture.md`](../backend/docs/architecture.md)                                                                                    |
| Run or extend benchmarks               | [`development-guide.md#benchmarking`](development-guide.md#benchmarking)                                                           | [`benchmark/`](benchmark/), [`benchmarking.md`](../backend/docs/benchmarking.md)                                                                                                 |
| Interpret or publish benchmark results | [`benchmarking.md#publishing-benchmark-and-model-results`](../backend/docs/benchmarking.md#publishing-benchmark-and-model-results) | [`validation/latest-benchmark.json`](validation/latest-benchmark.json)                                                                                                           |

## Product surface

Meeting Agent turns meeting and reference materials into searchable,
citable context. The main lifecycle is:

```text
upload → validate → parse/transcribe → normalize → chunk → embed/index
                                                        ↓
question → classify → route/funnel → retrieve → evidence gate/rerank
                                                        ↓
                              assemble context → answer + citations
                                                        ↓
                                      session history + memory + entities
```

| Capability              | Supported surface                                                                                                                                          | Primary reference                                                                                                                                                     |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Meetings and files      | Create, upload, inspect, reprocess, export, delete                                                                                                         | [`api-reference.md`](../backend/docs/api-reference.md), [`ingest-pipeline.md`](../backend/docs/ingest-pipeline.md)                                                    |
| Input modalities        | Registered audio/video, PDF/Office, text/data, and image families; the complete extension matrix and processor boundary are maintained in the ingest guide | [`ingest-pipeline.md`](../backend/docs/ingest-pipeline.md#45-support-formats-and-old-office)                                                                          |
| Retrieval               | Answer-shape routing, Evidence Filter, vector + BM25/FTS5, RRF, summary routers, funnel scoping, fair allocation, anchors, and optional reranking          | [`rag.md`](../backend/docs/rag.md), [`chunk_retrieval.md`](chunk_retrieval.md)                                                                                        |
| Grounded answers        | Source citations, page/slide/timestamp metadata, optional web results                                                                                      | [`chain-pipeline.md`](../backend/docs/chain-pipeline.md)                                                                                                              |
| Conversation continuity | Sessions, summaries, cross-session search                                                                                                                  | [`memory-and-kg.md`](../backend/docs/memory-and-kg.md)                                                                                                                |
| Long-term context       | Facts, TTL/decay, profiles, entities, relations, alias merging, and a bounded virtualized review workspace                                                 | [`memory-and-kg.md`](../backend/docs/memory-and-kg.md), [`frontend/docs/architecture.md`](../frontend/docs/architecture.md), [`data-lifecycle.md`](data-lifecycle.md) |
| Skills                  | Register, match, list, and invoke custom Markdown skills                                                                                                   | [`SKILLS.md`](../backend/docs/SKILLS.md)                                                                                                                              |
| Integrations            | REST, SSE, WebSocket notifications, MCP stdio/HTTP                                                                                                         | [`api-quickstart.md`](api-quickstart.md), [`mcp-server.md`](../backend/docs/mcp-server.md)                                                                            |
| Operations              | Health probes, metrics, backup/restore, migrations, retention, runbooks                                                                                    | [`operations-guide.md`](operations-guide.md), [`operations/`](../backend/docs/operations/)                                                                            |

## Documentation map

### Maintained repository-level guides

- [`getting-started.md`](getting-started.md) — prerequisites, Docker/manual setup, first upload, first answer, and common failures.
- [`api-quickstart.md`](api-quickstart.md) — copy-paste REST, SSE, WebSocket, error, idempotency, and MCP examples.
- [`data-lifecycle.md`](data-lifecycle.md) — what is stored, how indexes relate to source data, ownership, deletion, retention, and recovery boundaries.
- [`operations-guide.md`](operations-guide.md) — deployment choices, probes, observability, backup/restore, migrations, reindexing, and incident triage.
- [`development-guide.md`](development-guide.md) — repository map, local workflow, tests, linting, frontend/backend changes, and benchmark workflow.

### Architecture and retrieval

- [`diagrams/architecture.md`](diagrams/architecture.md) and [`diagrams/architecture.mmd`](diagrams/architecture.mmd) — system structure.
- [`diagrams/deployment-and-operations.md`](diagrams/deployment-and-operations.md) — Compose/Helm topology, startup, shutdown, backup, and observability.
- [`diagrams/rag-pipeline.md`](diagrams/rag-pipeline.md) — query routing through answer generation.
- [`diagrams/memory-and-kg.md`](diagrams/memory-and-kg.md) — memory layers and knowledge-graph relationships.
- [`chunk_retrieval.md`](chunk_retrieval.md) — retrieval scoring, filters, fair allocation, and reranking.

### Benchmark design material

The benchmark folder contains historical design inputs for multimodal and
audio evaluation. These plans are not a statement of the current runner. The
executable entry points, supported schemas, production-path gates, and metric
semantics are maintained in
[`backend/docs/benchmarking.md`](../backend/docs/benchmarking.md).

- [`benchmark/benchmark_config_guide.md`](benchmark/benchmark_config_guide.md)
- [`benchmark/benchmark_construction_flow.md`](benchmark/benchmark_construction_flow.md)
- [`benchmark/benchmark_implementation_plan.md`](benchmark/benchmark_implementation_plan.md)
- [`benchmark/benchmark_test_plan.md`](benchmark/benchmark_test_plan.md)

### Architecture decision records

Product-level decisions are in [`adr/`](adr/):

- [`ADR-001-migrations.md`](adr/ADR-001-migrations.md) — migration strategy.
- [`ADR-002-deployment.md`](adr/ADR-002-deployment.md) — deployment boundary.
- [`ADR-003-raganything.md`](adr/ADR-003-raganything.md) — optional multimodal branch.
- [`ADR-004-memory-ontology.md`](adr/ADR-004-memory-ontology.md) — memory ontology.
- [`ADR-005-mcp-parity.md`](adr/ADR-005-mcp-parity.md) — MCP parity.
- [`ADR-006-single-instance-deployment.md`](adr/ADR-006-single-instance-deployment.md) and [`ADR-007-shared-breaker-state.md`](adr/ADR-007-shared-breaker-state.md) — single-instance and shared breaker constraints.

Stack-level ADRs are in [`backend/docs/adr/`](../backend/docs/adr/).

### Historical audits and implementation records

Files whose names contain a date, plus `meeting-optimization-followup.md`, are
point-in-time evidence. They intentionally retain the observations, test
counts, incidents, and release decisions from that run; later repairs do not
rewrite that evidence. They are not the current feature or API reference.

The latest dated repair record retained in the repository is the
machine-readable
[`validation/system-audit-remediation-2026-09-08.json`](validation/system-audit-remediation-2026-09-08.json).
Current release-readiness evidence is
[`validation/release-readiness.json`](validation/release-readiness.json); a
green regression run must not be interpreted as production release approval.
The only current public benchmark score artifact is
[`validation/latest-benchmark.json`](validation/latest-benchmark.json). It
combines the latest protocol, evidence-governance, RAG, Multi-turn, Memory,
reranker, Vision, full-stack, and Chat results. Superseded public score
snapshots have been removed so readers cannot mistake old values for the latest
run. The artifact identifies verification-time models and failed gates, but its
results apply only to the captured synthetic workload and OpenRouter route;
they are not repository defaults, a general model ranking, or release
certification.

## Backend reference index

The backend documents are the authoritative source for implementation-level
behavior and are maintained in English:

| Area                       | Reference                                                                                                                        |
| -------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| Architecture and lifecycle | [`architecture.md`](../backend/docs/architecture.md), [`lifespan-and-operations.md`](../backend/docs/lifespan-and-operations.md) |
| Frontend                   | [`frontend/docs/README.md`](../frontend/docs/README.md), [`frontend/docs/architecture.md`](../frontend/docs/architecture.md)     |
| Backend quality            | [`testing.md`](../backend/docs/testing.md)                                                                                       |
| Security and tenancy       | [`security-and-tenancy.md`](../backend/docs/security-and-tenancy.md)                                                             |
| Observability              | [`observability.md`](../backend/docs/observability.md)                                                                           |
| Configuration              | [`configuration.md`](../backend/docs/configuration.md)                                                                           |
| Ingestion                  | [`ingest-pipeline.md`](../backend/docs/ingest-pipeline.md)                                                                       |
| RAG and chain              | [`rag.md`](../backend/docs/rag.md), [`chain-pipeline.md`](../backend/docs/chain-pipeline.md)                                     |
| Memory and KG              | [`memory-and-kg.md`](../backend/docs/memory-and-kg.md)                                                                           |
| Database and migrations    | [`database.md`](../backend/docs/database.md), [`operations/alembic.md`](../backend/docs/operations/alembic.md)                   |
| API and MCP                | [`api-reference.md`](../backend/docs/api-reference.md), [`mcp-server.md`](../backend/docs/mcp-server.md)                         |
| CLI and skills             | [`cli.md`](../backend/docs/cli.md), [`SKILLS.md`](../backend/docs/SKILLS.md)                                                     |
| LLM/provider traffic       | [`llm-and-traffic.md`](../backend/docs/llm-and-traffic.md)                                                                       |
| Benchmarking               | [`benchmarking.md`](../backend/docs/benchmarking.md)                                                                             |
| Operations                 | [`operations/`](../backend/docs/operations/)                                                                                     |

## Source of truth and maintenance

- User-facing commands and ports must agree with the root [`README.md`](../README.md), `Makefile`, and Compose files.
- Runtime configuration must agree with [`backend/config/main.yaml`](../backend/config/main.yaml), [`backend/.env.example`](../backend/.env.example), and `backend/src/core/config.py`.
- Accepted upload types and processor capabilities must agree with
  `backend/src/services/files/_kinds.py`; parser candidates and terminal
  fallback behavior must agree with `backend/src/services/parser/_router.py`
  and `cascade.py`.
- API details must agree with the FastAPI OpenAPI document at `/docs` and `/openapi.json`. The detailed static reference is [`backend/docs/api-reference.md`](../backend/docs/api-reference.md).
- `backend/alembic/versions/` is authoritative for post-v52 schema evolution;
  the frozen legacy migration tuple is only the compatibility baseline.
- Backend implementation behavior belongs in `backend/docs/`; frontend implementation behavior belongs in `frontend/docs/`. Repository guides should link to those directories instead of copying large implementation tables.
- When a behavior changes, update the nearest detailed document, the relevant guide, and `CHANGELOG.md` when the change is user-visible.
- Dated model/benchmark claims must state the workload, sample count, model and
  provider route, relevant configuration, gates, skipped metrics, and release
  boundary. Never turn a route-specific error or one diagnostic run into a
  universal availability, quality, or ranking claim.

Run the repository-maintained reconciliation gates before publishing a
documentation change:

```bash
make lint-docs validate-config validate-migrations contract-openapi
docker compose config -q
helm lint deploy/helm/meeting-agent --set ingress.enabled=false
```

These commands validate links/source references, environment layout, the
single Alembic head and immutable revisions, generated frontend API types, and
the declared deployment topology. They do not prove live provider
availability, browser layout, benchmark quality, or release readiness.

## Other entry points

- [`README.md`](../README.md) / [`README.zh-CN.md`](../README.zh-CN.md) — English and Simplified Chinese product overviews, quick start, ports, configuration highlights, and commands.
- [`CONTRIBUTING.md`](../CONTRIBUTING.md) — contribution workflow and style.
- [`SECURITY.md`](../SECURITY.md) — vulnerability reporting.
- [`CHANGELOG.md`](../CHANGELOG.md) — release history.

## Historical meeting-workflow records

- [2026-09-07 implementation and validation matrix](validation/meeting-workflow-2026-09-07.json)
- [Human review and real holdout-set workflow](meeting-workflow-review-guide.md)
