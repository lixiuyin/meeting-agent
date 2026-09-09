# System Architecture Overview

Meeting Agent is a meeting-record and document knowledge assistant. This document is the entry point for `backend/docs/`; subsystem documents are indexed and cross-referenced here.

**Verified against implementation:** 2026-09-09.

## 1. Product scope

Meeting Agent:

- ingests registered video/audio formats through transcription, plain text/data
  through local extraction, and PDF/Office/image content through profiled,
  quality-gated parsing and optional vision enrichment;
- chunks content into Chroma vector storage plus a BM25/FTS5 inverted index;
- provides synchronous and streaming RAG answers, long-term memory, and a knowledge graph;
- exposes REST API, WebSocket, and MCP Server interfaces.

## 2. Layered structure

```text
backend/src/
├── main.py                 # FastAPI entry point, middleware, routes, lifespan
├── mcp.py                  # Thin MCP → HTTP API adapter
├── api/                    # HTTP integration layer
│   ├── dependencies.py     # Shared FastAPI dependencies
│   ├── lifespan/           # Startup/shutdown orchestration
│   ├── middleware.py       # Request IDs, rate limits, logging, CORS
│   ├── metrics.py          # /metrics endpoint
│   └── routers/            # Domain-specific routes
│       ├── file_download.py # File downloads using API key or short-lived token
│       ├── meetings/        # Meeting CRUD, upload, transcription, summary, search
│       ├── chat.py          # /chat, /chat/stream, /chat/search
│       ├── sessions.py      # Session list, summary, citations, cross-session search
│       ├── memory.py        # Memory CRUD, batch operations, decay, KG entities
│       ├── settings/        # Runtime settings and vector rebuild
│       ├── skills.py        # Skill registration, matching, invocation
│       ├── health.py        # Health, live/ready/traffic/index consistency
│       └── websocket.py     # Real-time progress at /api/v1/ws
├── services/               # Domain and business services
│   ├── chain/              # Ask orchestration and streaming
│   ├── rag/                # Vector store, indexing, retrieval, reranking, rewriting
│   ├── processor/          # Upload → parse/transcribe → persistence pipeline
│   ├── parser/             # Profiles, cloud routing/quality, PDF-only local fallback
│   ├── transcriber.py      # ASR transcription, timestamps, and diarization
│   ├── vision/             # Captions, OCR, deduplication, and clients
│   ├── files/              # File classification and asset management
│   ├── llm/                # Provider registry, cache, prompts, telemetry
│   ├── embedder.py         # Embedding provider singleton
│   ├── memory/             # Extraction, decay, consolidation, profiles, history
│   ├── knowledge_graph/    # Entity, relation, and vector operations
│   ├── search.py           # Web-search adapters
│   ├── stream_bus.py       # SSE producer/consumer event bus
│   ├── jobs.py             # SQLite-backed durable queue and embedded workers
│   ├── summaries.py        # Shared summary job producers
│   ├── concurrency.py      # Request concurrency controls
│   ├── traffic_control.py  # Concurrency, rate, breaker, and error-rate control
│   ├── registry.py          # Resettable service registry
│   ├── retention.py         # Retention policy
│   └── websocket.py         # WebSocketManager singleton
├── utils/                  # Shared utilities and supervised tasks
├── core/                   # Configuration, security, tracing, logging, metrics, database
└── models/                 # Pydantic request/response models and status enums
```

## 3. Core data flows

### 3.1 Ingestion

```text
Upload API → persist file + meeting_files row
           ↓
durable_jobs → worker → process_meeting_file()
           ↓
   ┌────────────┬────────────────┐
   ↓            ↓                ↓
 transcribe   parse (cascade)   local text/data   fetch_metadata
 (video/audio)(PDF/Office/image)
   └────────────┴────────────────┴────────────────┘
           ↓
   index_meeting() → Chroma + BM25 index
           ↓
   status update → durable summary job → WebSocket notification
```

See [`ingest-pipeline.md`](./ingest-pipeline.md) for parser and processor details.

### 3.2 Query and RAG

```text
Chat API → ask() / ask_stream()
       ↓
_run_pipeline(PipelineContext)
       ↓
  1. ensure_session          # create or restore a session
  2. rewrite_query            # LLM rewriting and multi-query variants
  3. parallel local context loading:
     ├ retrieve + rerank + dedup
     ├ load memories
     ├ load session context
     ├ load entity context from KG
     └ load history
  4. optional web fallback    # runs after local retrieval confidence is known
  5. build_context            # assemble the final prompt
  6. generate_answer          # LCEL chain → LLM
  7. save_messages            # persist user and assistant messages
  8. schedule_fact_extraction # commit a durable memory-extraction job
```

Chroma/BM25 and the optional RAGAnything store form a dual-store design. Uploads may write both stores; a request can select `vector`, `hybrid`, `multimodal`, `hybrid_multimodal`, or `auto` through `rag_mode`. Every branch carries the authenticated `user_id`; meeting/file scope is an additional restriction, not a substitute for ownership filtering.

See [`chain-pipeline.md`](./chain-pipeline.md) and [`rag.md`](./rag.md).

## 4. Cross-cutting concerns

| Concern            | Implementation                               | Behavior                                                         |
| ------------------ | -------------------------------------------- | ---------------------------------------------------------------- |
| Configuration      | `core/config.py`, `config/main.yaml`, `.env` | YAML defaults < `.env` < OS environment; request/job snapshots prevent mid-flight drift |
| Authentication     | `core/security.py`                           | `X-API-Key` and `hmac.compare_digest`                            |
| Rate limiting      | `api/middleware.py` with slowapi             | Default and route-specific limits; trusted forwarded IP handling |
| Request tracing    | `RequestIdMiddleware` and `core/trace.py`    | `X-Request-ID`, nested spans, and pipeline events                |
| Audit              | `core/audit.py`                              | Structured records for sensitive changes                         |
| Errors             | `core/exceptions.py` and global handlers     | Unified public errors and internal `exc_info` logging            |
| Logging            | Python logging and `LOG_FORMAT=json`         | Lazy formatting and redaction                                    |
| Concurrency safety | Locking + configuration-keyed singletons     | Old request snapshots cannot publish clients reused by newer settings |
| Blocking calls     | `asyncio.to_thread()`                        | Synchronous I/O and CPU-heavy work leave the event loop          |
| Traffic governance | `services/traffic_control.py`                | Semaphore, token bucket, circuit breaker                         |
| Durable work       | `services/jobs.py`, `durable_jobs`           | Lease, retry, dedupe, cancellation, dead-letter                  |

## 5. Subsystem index

| Document | Scope |
|---|---|
| [`architecture.md`](./architecture.md) | This overview |
| [`lifespan-and-operations.md`](./lifespan-and-operations.md) | Startup, shutdown, and runtime maintenance |
| [`configuration.md`](./configuration.md) | Settings and environment variables |
| [`security-and-tenancy.md`](./security-and-tenancy.md) | Authentication, ownership, tokens, and HTTP security |
| [`observability.md`](./observability.md) | Logs, traces, metrics, and health probes |
| [`api-reference.md`](./api-reference.md) | REST routes and schemas |
| [`../../frontend/docs/architecture.md`](../../frontend/docs/architecture.md) | React routes, API client, SSE/WS, viewers, and security |
| [`testing.md`](./testing.md) | Test layers, CI, gates, and regression strategy |
| [`database.md`](./database.md) | SQLite, Alembic, legacy migrations, tables, repositories |
| [`ingest-pipeline.md`](./ingest-pipeline.md) | Upload, parsing, and persistence |
| [`rag.md`](./rag.md) | Retrieval, reranking, and generation |
| [`chain-pipeline.md`](./chain-pipeline.md) | `ask()` orchestration and stream events |
| [`llm-and-traffic.md`](./llm-and-traffic.md) | LLM/embedding providers and traffic governance |
| [`memory-and-kg.md`](./memory-and-kg.md) | Memory and knowledge graph |
| [`../../docs/diagrams/rag-pipeline.md`](../../docs/diagrams/rag-pipeline.md) | RAG query diagram |
| [`../../docs/diagrams/memory-and-kg.md`](../../docs/diagrams/memory-and-kg.md) | Memory layers and decay formulas |
| [`mcp-server.md`](./mcp-server.md) | MCP Server tools |
| [`cli.md`](./cli.md) | CLI commands and exports |
| [`SKILLS.md`](./SKILLS.md) | Markdown Skill system and intent matching |
| [`benchmarking.md`](./benchmarking.md) | Benchmark runner |
| [`operations/alembic.md`](./operations/alembic.md) | Alembic migration and stamping |
| [`operations/backup.md`](./operations/backup.md) / [`restore.md`](./operations/restore.md) | Backup and recovery |
| [`operations/runbooks/`](./operations/runbooks/) | Incident runbooks |

## 6. Frontend/backend boundary

- The frontend is in `frontend/` and is documented in [`../../frontend/docs/architecture.md`](../../frontend/docs/architecture.md).
- Development Vite proxies `/api` to the host-side backend at `localhost:7008`.
- Containers use nginx to proxy `/api/` to `backend:8000`.
- WebSocket progress and completion events use `/api/v1/ws`.
- Streaming chat uses SSE at `POST /api/v1/chat/stream`.

## 7. Key design decisions

1. **Configuration-keyed singletons with locking:** expensive resources are initialized once per effective provider configuration and reset after `PUT /settings`. Every access verifies its configuration identity, so an older in-flight snapshot cannot repopulate a client that a newer request would reuse.
2. **Critical versus capability startup:** migrations, local storage and process-safety invariants fail closed; remote AI providers are reported as degraded capabilities without blocking local/history APIs.
3. **Blocking isolation:** LLM, parsing, SQLite writes, Chroma writes, and transcoding use `asyncio.to_thread` where needed.
4. **Read/write separation:** SQLite WAL uses `get_connection()` for reads and serialized `get_write_connection()` for writes.
5. **Content-hash idempotency:** duplicate uploads within the same meeting skip parsing when the persisted content hash already exists.
6. **Trace-first observability:** pipeline steps emit timed/error-aware spans that the frontend can render directly.
7. **One business boundary:** HTTP API owns storage and domain services; MCP is
   an authenticated API client and never opens SQLite/Chroma itself.
8. **Durable state transitions:** file processing, summaries, and fact
   extraction are committed to `durable_jobs` before the request returns.
9. **Modular monolith:** durable-job consumers run inside the single API process
   by default so SQLite, Chroma locks and runtime settings share one
   coordination boundary. `DURABLE_JOB_EXECUTION_MODE=off` is an operational
   escape hatch, not a documented external-worker topology.
