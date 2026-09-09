# Meeting Agent — System Architecture

The diagram below is the implementation-level system map. It reflects the
current router registry, service packages, storage layout, container ports, and
optional provider integrations. The editable Mermaid source is
[`architecture.mmd`](./architecture.mmd).

**Verified against implementation:** 2026-09-09.

```mermaid
flowchart TB
    Browser["Browser"] --> Frontend["Frontend container<br/>Nginx :8307 → :8080<br/>React SPA + /api proxy"]
    Frontend -->|/api/*| API["FastAPI backend :8000<br/>/api/v1"]
    MCP["MCP client"] -->|stdio by default| MCPServer["src/mcp.py<br/>FastMCP — six API-backed tools"]

    subgraph Backend["Backend runtime"]
        Middleware["request ID · CORS · security headers<br/>API-key auth · slowapi"] --> Routers["FastAPI routers<br/>meetings · chat · sessions · memory<br/>settings · health · skills · websocket"]
        Routers --> Chain["chain/<br/>ask() / ask_stream()"]
        Routers --> Ingest["processor/<br/>AV · document · image · text ingestion"]
        Routers --> Memory["memory/ + knowledge_graph/"]
        Routers --> Files["file download and signed assets"]
        Chain --> RAG["rag/<br/>scope → retrieve → rerank → context"]
        Chain --> Memory
        Chain --> LLM["llm/ + traffic_control.py"]
        Chain --> Bus["StreamBus<br/>SSE events"]
        Jobs["SQLite durable_jobs<br/>lease · retry · cancel · dead-letter"] --> Ingest
        Jobs --> Memory
        Ingest --> Providers["transcriber · parser · vision"]
        Ingest --> RAG
        RAG --> Stores["SQLite FTS5/BM25 + Chroma"]
        Memory --> Stores
        Files --> Uploads["data/uploads"]
        Lifespan["lifespan startup/shutdown<br/>migration · recovery · decay · retention"] --> Stores
        Config["defaults: main.yaml<br/>overridden by .env<br/>then process environment"] -. settings .-> Middleware
        Metrics["logs · traces · /metrics · optional OTEL/Sentry"] -. observes .-> Routers
    end

    API --> Middleware
    MCPServer -->|HTTP /api/v1| API
    LLM --> ExternalLLM["Configured LLM provider"]
    Providers --> ExternalParsing["Configured ASR / parser / vision provider"]

    classDef client fill:#2563eb,stroke:#1e3a8a,color:#fff
    classDef boundary fill:#0f766e,stroke:#115e59,color:#fff
    classDef service fill:#d97706,stroke:#92400e,color:#111
    classDef data fill:#be185d,stroke:#831843,color:#fff
    classDef ops fill:#475569,stroke:#1e293b,color:#fff
    class Browser,MCP client
    class Frontend,API,Middleware,Routers boundary
    class Chain,Ingest,Memory,Files,RAG,LLM,Bus,Providers,Jobs service
    class Stores,Uploads data
    class Lifespan,Config,Metrics,ExternalLLM,ExternalParsing ops
```

## Runtime boundaries

| Boundary | Implementation contract |
|---|---|
| Browser → frontend | The production frontend serves the SPA on container port `8080` and proxies `/api/` to the backend; Compose publishes it on host port `8307`. Development Vite also serves on host port `8307` and proxies to `localhost:7008`. |
| Frontend → backend | REST and SSE use `/api/v1`; the browser WebSocket uses `/api/v1/ws`. The frontend injects the configured backend API key through the Nginx template; short-lived file and WebSocket tokens are separate mechanisms. |
| MCP → HTTP API | `backend/src/mcp.py` is a thin FastMCP adapter. The default transport is trusted stdio; HTTP/SSE is loopback-only and requires a downstream API key. Its six tools call the canonical HTTP API rather than importing domain or persistence services. The MCP listener itself has no inbound authentication and must not be published directly. |
| Backend → storage | SQLite at `data/meetings.db` is authoritative for relational state and FTS5/BM25 metadata. Chroma at `data/vectordb` stores semantic collections. Original files and derived assets remain under `data/uploads`. |
| Backend → providers | LLM, embeddings, document parsing, vision, and web search are selected through configuration bindings; ASR currently supports AssemblyAI only. Most provider concurrency and circuit-breaking state is process-local. |
| Request → durable work | Upload processing, file/meeting summaries, speaker rename/re-index, and fact extraction are committed to `durable_jobs` before the producer returns. Embedded workers claim leases inside the single backend process. Vector-generation rebuilds use a separate guarded maintenance task and are not durable jobs. |

## Request and data flows

### Ingestion

```mermaid
flowchart LR
    Upload["POST /api/v1/meetings/upload"] --> Validate["validate ownership, size, type<br/>persist upload metadata"]
    Validate --> Queue["commit file_processing durable job"]
    Queue --> Route{File route}
    Route -->|audio / video| ASR["transcriber.py<br/>timestamps + diarization"]
    Route -->|PDF / office / image| Parse["parser cascade<br/>profile → cloud provider → quality check<br/>PDF-only local text fallback"]
    Route -->|text / CSV / markdown| Text["text processor"]
    ASR --> Artefact["normalized artefact"]
    Parse --> Artefact
    Text --> Artefact
    Artefact --> Index["rag indexer<br/>chunk or segment route"]
    Index --> Vector["Chroma meetings collection"]
    Index --> BM25["SQLite bm25_index + FTS5"]
    Artefact --> Persist["meeting_files status, transcript,<br/>structured metadata and metrics"]
    Persist --> Summary["enqueue optional file/meeting summaries<br/>+ summary vectors"]
```

The processor is implemented in `backend/src/services/processor/` and dispatches
to `_processors/av.py`, `document.py`, `image.py`, and `text.py`. Indexing has
separate entry points for plain text, structured pages, and timestamped
segments; the configured non-text chunking strategy determines which route is
used for audio, video, and image artefacts.

### Answer generation

```mermaid
flowchart LR
    Request["POST /chat or /chat/stream"] --> Intent["classify intent"]
    Intent -->|casual / trivial| Casual["persist short response"]
    Intent -->|retrieval| Prepare["ensure session + resolve/rewrite query"]
    Prepare --> Parallel{{"parallel context loading"}}
    Parallel --> Retrieval["RAG retrieval<br/>meeting router → file scoping → chunks → rerank"]
    Parallel --> History["chat history"]
    Parallel --> Summaries["session summaries"]
    Parallel --> Facts["long-term memories"]
    Parallel --> Entities["knowledge-graph context"]
    Retrieval & History & Summaries & Facts & Entities --> LocalReady{{"local context complete"}}
    LocalReady --> WebMode{"web fallback needed<br/>and enabled?"}
    WebMode -->|yes| Web["web search after<br/>local confidence check"]
    WebMode -->|no| Context["token-budgeted context assembly"]
    Web --> Context
    Context --> Generate["LLM generation<br/>retry + fallback breaker<br/>optional fast-path latency guard"]
    Generate --> Persist["save messages"]
    Persist --> Extract["enqueue durable fact/entity extraction"]
    Generate -->|streaming| Bus["StreamBus → SSE"]
```

The synchronous and streaming paths share the same `PipelineContext` and
retrieval/context stages. Streaming additionally emits ordered `step`,
`token`, `sources`, `web_results`, `trace`, `heartbeat`, and terminal
`done`/`error` events through `backend/src/services/stream_bus.py`.
When the opt-in fast-path latency guard expires, generation returns a labelled,
source-backed extractive fallback (or an explicit timeout message) instead of
presenting an unfinished model response as complete.

## Operational architecture

The FastAPI lifespan performs the Alembic upgrade and fail-closed local/security
checks, then pre-warms provider capabilities and starts recovery and maintenance
loops. Provider pre-warm failures are reported as degraded capabilities rather
than being treated as proof that every route is unavailable. Recovery includes
durable-job lease handling, stale meeting and summary recovery, BM25 drift
checks, vector reconciliation, memory decay, retention cleanup, and WAL
checkpointing. The application rejects multiple production backend workers
because SQLite, local files, token buckets, most circuit breakers, and several
coordination caches are not distributed across processes.

For deployment and failure handling, see [`deployment-and-operations.md`](./deployment-and-operations.md),
[`../operations-guide.md`](../operations-guide.md), and
[`../adr/ADR-006-single-instance-deployment.md`](../adr/ADR-006-single-instance-deployment.md).
