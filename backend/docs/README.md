# Meeting Agent Backend Documentation

`backend/docs/` is the subsystem-level documentation set for the Meeting Agent backend. Each document focuses on one subsystem and is cross-referenced from [`architecture.md`](./architecture.md).

English diagrams, including Mermaid flowcharts, are in [`docs/diagrams/`](../../docs/diagrams/). The repository-wide index is [`docs/README.md`](../../docs/README.md).

**Last implementation reconciliation:** 2026-09-10. Current behavior is
defined by source and generated OpenAPI. The current reconciliation also
covers every `Settings` field plus the file-kind, parser-route, MCP-tool, and
Alembic registries; dated repository audit records are historical evidence
rather than backend reference material.

If you are running the project for the first time, start with [`docs/getting-started.md`](../../docs/getting-started.md). For REST, SSE, WebSocket, and MCP examples, read [`docs/api-quickstart.md`](../../docs/api-quickstart.md). The operator entry point is [`docs/operations-guide.md`](../../docs/operations-guide.md); deletion, backup, and recovery boundaries are described in [`docs/data-lifecycle.md`](../../docs/data-lifecycle.md).

## Recommended reading order

1. **New contributor:** read [`architecture.md`](./architecture.md) for backend layers and data flow, then [`../../frontend/docs/architecture.md`](../../frontend/docs/architecture.md) for the UI and API client.
2. **Deployment:** read [`configuration.md`](./configuration.md), [`lifespan-and-operations.md`](./lifespan-and-operations.md), [`security-and-tenancy.md`](./security-and-tenancy.md), [`database.md`](./database.md), and [`operations/alembic.md`](./operations/alembic.md).
3. **API integration:** read [`api-reference.md`](./api-reference.md) and [`mcp-server.md`](./mcp-server.md).
4. **CLI usage:** read [`cli.md`](./cli.md).
5. **Core business logic:** read [`chain-pipeline.md`](./chain-pipeline.md) → [`rag.md`](./rag.md) → [`memory-and-kg.md`](./memory-and-kg.md); see [`SKILLS.md`](./SKILLS.md) for Skill extensions.
6. **Storage and performance:** read [`database.md`](./database.md), [`llm-and-traffic.md`](./llm-and-traffic.md), [`observability.md`](./observability.md), and [`benchmarking.md`](./benchmarking.md).
7. **Upload troubleshooting:** read [`ingest-pipeline.md`](./ingest-pipeline.md).

## Documentation index

### Overview

| Document                                                     | Scope                                                                                        |
| ------------------------------------------------------------ | -------------------------------------------------------------------------------------------- |
| [`architecture.md`](./architecture.md)                       | System architecture, layers, data flow, and cross-cutting concerns                           |
| [`lifespan-and-operations.md`](./lifespan-and-operations.md) | FastAPI lifespan, critical/best-effort paths, operations, and recovery                       |
| [`configuration.md`](./configuration.md)                     | Configuration precedence, complete settings reference, and deployment templates              |
| [`security-and-tenancy.md`](./security-and-tenancy.md)       | API keys, principals, ownership, short-lived tokens, idempotency payloads, and HTTP security |
| [`observability.md`](./observability.md)                     | Logs, request IDs, pipeline traces, Prometheus metrics, probes, and troubleshooting          |

### Business pipelines

| Document                                     | Scope                                                                               |
| -------------------------------------------- | ----------------------------------------------------------------------------------- |
| [`ingest-pipeline.md`](./ingest-pipeline.md) | Upload → parse/transcribe → index end-to-end flow                                   |
| [`rag.md`](./rag.md)                         | RAG architecture, chunking, retrieval, reranking, post-processing, and tuning       |
| [`chain-pipeline.md`](./chain-pipeline.md)   | `ask()` / `ask_stream()` orchestration, parallel context loading, and stream events |
| [`memory-and-kg.md`](./memory-and-kg.md)     | Memory, decay, merging, profiles, and knowledge graph                               |
| [`SKILLS.md`](./SKILLS.md)                   | Skill loading, intent matching, and chain integration                               |

### Infrastructure

| Document                                     | Scope                                                                                                          |
| -------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| [`database.md`](./database.md)               | SQLite read/write separation, **Alembic + legacy migrations**, `_MIGRATIONS` summary, tables, and repositories |
| [`llm-and-traffic.md`](./llm-and-traffic.md) | Provider registry, caching, concurrency, rate limiting, and circuit breaking                                   |
| [`observability.md`](./observability.md)     | Logs, traces, metrics, health probes, and incident diagnosis                                                   |

### Operations and practice

| Document                                                                                | Scope                                                                                     |
| --------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| [`operations/alembic.md`](./operations/alembic.md)                                      | Alembic and `init_db`, stamping, `upgrade head`, and team conventions                     |
| [`operations/backup.md`](./operations/backup.md)                                        | Backup strategy and script entry points                                                   |
| [`operations/restore.md`](./operations/restore.md)                                      | Recovery procedure                                                                        |
| [`operations/retention.md`](./operations/retention.md)                                  | Data retention and cleanup                                                                |
| [`operations/sla.md`](./operations/sla.md) / [`operations/slo.md`](./operations/slo.md) | SLA and SLO definitions                                                                   |
| [`operations/runbooks/`](./operations/runbooks/)                                        | AssemblyAI timeouts, 429 storms, Chroma dimension mismatches, breaker incidents, and more |

### Integrations

| Document                                 | Scope                                                        |
| ---------------------------------------- | ------------------------------------------------------------ |
| [`api-reference.md`](./api-reference.md) | REST routes, request/response schemas, and error semantics   |
| [`mcp-server.md`](./mcp-server.md)       | MCP tools, transports, debugging, and extension              |
| [`cli.md`](./cli.md)                     | CLI commands, interactive setup, export, and troubleshooting |

### Backend quality

| Document                     | Scope                                                                |
| ---------------------------- | -------------------------------------------------------------------- |
| [`testing.md`](./testing.md) | Backend tests, CI, coverage, security gates, and regression strategy |

### Performance

| Document                               | Scope                                                                               |
| -------------------------------------- | ----------------------------------------------------------------------------------- |
| [`benchmarking.md`](./benchmarking.md) | Benchmark tools, commands, metric interpretation, and public model-claim boundaries |

## Maintenance rules

- Write all new documentation in **English** and keep terminology consistent.
- Use repository-relative source paths such as `backend/src/...` in documentation.
- Never hard-code secrets or production URLs.
- Keep documentation synchronized with implementation: update the nearest subsystem document whenever behavior changes.
- Use `backend/alembic/versions/` as the schema source of truth after the frozen
  v52 legacy baseline; never infer the current schema from `_MIGRATIONS` alone.
- When adding a subsystem:
  1. Add a Markdown file under `backend/docs/`.
  2. Add it to this README index.
  3. Add it to the subsystem index in [`architecture.md`](./architecture.md).
