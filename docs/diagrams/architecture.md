# Meeting Agent — System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                                  CLIENTS                                        │
│   ┌──────────────────────┐                    ┌──────────────────────┐           │
│   │   React SPA          │                    │   MCP Client         │           │
│   │   Vite+Ant Design    │                    │   FastMCP (stdio)    │           │
│   └──────────┬───────────┘                    └──────────┬───────────┘           │
└──────────────┼──────────────────────────────────────────┼────────────────────────┘
               │ HTTP / SSE / WS                          │ stdio
               ▼                                          ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│  FRONTEND — React 19 + TypeScript + Vite 6 + Ant Design 6                       │
│                                                                                  │
│  ┌─────────────┐   ┌──────────────────┐   ┌─────────────────┐   ┌────────────┐  │
│  │   Pages      │   │   Components     │   │   Hooks         │   │  API Client │  │
│  │             │   │                  │   │                 │   │            │  │
│  │  Home       │──▶│  ChatMessageBubble│──▶│  useChatStream  │──▶│  client-   │  │
│  │  Generate   │   │  ChatParameters  │   │ useSessionManager│   │  chat      │  │
│  │  Materials  │   │  WelcomeScreen   │   │  useMeetings    │   │  meetings  │  │
│  │  Memory     │   │  UploadPanel     │   │  useMemoryAct   │   │  sessions  │  │
│  │  History    │   │  SessionDetail   │   │  useWebSocket   │   │  memory    │  │
│  │  Settings   │   │  TranscriptViewer│   │  useHealthCheck │   │  settings  │  │
│  └─────────────┘   └──────────────────┘   └─────────────────┘   └──────┬─────┘  │
│                                                                        │         │
│  ThemeProvider (dark/light) · ErrorBoundary · Sentry                  │         │
└────────────────────────────────────────────────────────────────────────┼─────────┘
                                                                         │
                                                                         ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│  INGRESS — Nginx (:8307 → /api/ → backend:8000)                                 │
└──────────────────────────────────────────────────────────────────────────────────┘
                                                                         │
                                                                         ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│  MIDDLEWARE                                                                       │
│  ┌──────────────────┐  ┌───────────────┐  ┌──────┐  ┌─────────────────────┐     │
│  │ RequestId + Timer │  │ Rate Limiter  │  │ CORS │  │ Security Headers    │     │
│  │ X-Request-ID      │  │ slowapi       │  │      │  │ HMAC API Key Auth   │     │
│  └──────────────────┘  └───────────────┘  └──────┘  └─────────────────────┘     │
└──────────────────────────────────────────────────────────────────────────────────┘
                                                                         │
                                                                         ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│  API LAYER — FastAPI /api/v1                                                      │
│                                                                                  │
│  ┌────────────┐ ┌──────────────────┐ ┌──────────┐ ┌───────────┐ ┌────────────┐  │
│  │   chat      │ │  meetings/       │ │ sessions │ │  memory   │ │  settings  │  │
│  │             │ │  14 sub-routers  │ │          │ │           │ │            │  │
│  │ POST /chat  │ │ upload · CRUD    │ │ list     │ │ CRUD      │ │ get/update │  │
│  │ POST /stream│ │ files · search   │ │ messages │ │ batch     │ │ bindings   │  │
│  │ POST /search│ │ transcript       │ │ delete   │ │ search    │ │ rebuild-vec│  │
│  └──────┬─────┘ │ summary · export │ │ summarize│ │ decay     │ └─────┬──────┘  │
│         │       │ speakers · times │ │ search   │ │ entities  │       │         │
│         │       └────────┬─────────┘ └────┬─────┘ │ merge     │       │         │
│         │                │                │       └─────┬──────┘       │         │
│  ┌──────┴──────┐ ┌──────┴──────┐ ┌───────┴─────┐       │              │         │
│  │  websocket  │ │  file_download│ │   health    │       │              │         │
│  │  WS /ws     │ │  signed URLs  │ │  /health    │       │              │         │
│  └──────┬──────┘ └──────┬───────┘ └─────────────┘       │              │         │
│         │               │                                 │              │         │
│  ┌──────┴──────┐                                         │              │         │
│  │   skills    │                                         │              │         │
│  │ list · match│                                         │              │         │
│  └──────┬──────┘                                         │              │         │
└─────────┼─────────────────┼──────────────┼───────────────┼──────────────┼─────────┘
          │                 │              │               │              │
          ▼                 ▼              ▼               ▼              ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│  SERVICE LAYER — Business Logic (Singletons, Thread-safe)                         │
│                                                                                  │
│  ┌────────────────────────────────────────────────────────────────────────────┐  │
│  │                     RAG Pipeline Orchestrator (chain/ — 25 modules)         │  │
│  │                                                                            │  │
│  │  ask() / ask_stream()                                                      │  │
│  │       │                                                                    │  │
│  │       ├──▶ Intent Routing (_routing.py)                                    │  │
│  │       │                                                                    │  │
│  │       ├──▶ Context Assembly (_steps_context.py)                            │  │
│  │       │    ├── Memory context (long-term facts)                            │  │
│  │       │    ├── Entity context (knowledge graph)                            │  │
│  │       │    ├── Session context (chat history)                              │  │
│  │       │    └── Web search results                                          │  │
│  │       │                                                                    │  │
│  │       ├──▶ Retrieval (rag/ — 27 modules)                                  │  │
│  │       │    Broad Recall: Summary Router + Funnel → File Scoping            │  │
│  │       │                   → Fair Per-File Retrieval → Filters → Rerank     │  │
│  │       │    Scoped:        Direct retrieve → Filters → Rerank               │  │
│  │       │    ┌──────────────┐  ┌──────────────┐  ┌────────────────────┐      │  │
│  │       │    │  ChromaDB     │  │  FTS5/BM25   │  │  Summary Vectors   │      │  │
│  │       │    │  Vector Store │  │  Full-Text   │  │  (meeting + file)  │      │  │
│  │       │    └──────────────┘  └──────────────┘  └────────────────────┘      │  │
│  │       │                                                                    │  │
│  │       ├──▶ Reranking (_reranker.py — Cohere / BGE Cross-Encoder)          │  │
│  │       │                                                                    │  │
│  │       ├──▶ Generation (llm/) ──▶ Multi-Provider LLM                       │  │
│  │       │    OpenAI · Anthropic · DeepSeek · Ollama · Azure · Groq · ...    │  │
│  │       │    Prompt caching (Anthropic) · Combined extraction               │  │
│  │       │                                                                    │  │
│  │       └──▶ Stream Output ──▶ StreamBus (SSE Events)                       │  │
│  └────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                  │
│  ┌──────────────────────────┐  ┌──────────────────────────────────────────────┐  │
│  │  File Processing Pipeline │  │  Memory & Knowledge Graph                    │  │
│  │  (processor/)             │  │  (memory/ + knowledge_graph/)                │  │
│  │                           │  │                                              │  │
│  │  Upload                   │  │  Fact + Entity Extraction (combined)         │  │
│  │    │                      │  │       │                                       │  │
│  │    ├──▶ ASR (asr/)        │  │       ├──▶ Semantic Clustering               │  │
│  │    │    AssemblyAI        │  │       ├──▶ Importance Decay                   │  │
│  │    │    Speaker Diarize   │  │       └──▶ Consolidation                     │  │
│  │    │                      │  │                                              │  │
│  │    ├──▶ Parser (parser/)  │  │  Session Summaries (summary_vectorstore)     │  │
│  │    │    Cascade Route:    │  │  Cross-session Search                         │  │
│  │    │    local → marker →  │  │  Memory CRUD + Search (_service/)            │  │
│  │    │    mineru → paddle   │  │                                              │  │
│  │    │    + Quality Check   │  │  Vector Embeddings ──▶ Memory Search          │  │
│  │    │    + Format Convert  │  │                                              │  │
│  │    │    + Text Parsers    │  └──────────────────────────────────────────────┘  │
│  │    │                      │                                                    │
│  │    └──▶ Index (rag/)      │  ┌──────────────────────────────────────────────┐  │
│  │         Chunk → Embed     │  │  Cross-cutting Services                       │  │
│  │         → ChromaDB        │  │                                              │  │
│  │                           │  │  Embeddings (embedder.py)                    │  │
│  └──────────────────────────┘  │  Multi-provider: OpenAI · Jina · Cohere · .. │  │
│                                 │                                              │  │
│  ┌──────────────────────────┐  │  Traffic Control (traffic_control.py)        │  │
│  │  Web Search (search.py)  │  │  Rate Limit · Circuit Breaker · Retry        │  │
│  │  DDG · SerpAPI · Tavily  │  │                                              │  │
│  │  Bing · Exa              │  │  Tokenizer (tiktoken)                        │  │
│  └──────────────────────────┘  │  LLM (_providers.py · _cache.py · _prompts)  │  │
│                                 └──────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────────────┘
                                          │
                                          ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│  DATA LAYER                                                                       │
│                                                                                  │
│  ┌──────────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐   │
│  │   SQLite (WAL Mode)   │  │   ChromaDB        │  │   File System            │   │
│  │                       │  │   Vector Store    │  │                          │   │
│  │  meetings             │  │                   │  │  uploads/                │   │
│  │  meeting_files        │  │  meeting chunks   │  │    ├── audio/            │   │
│  │  chat_sessions        │  │  memory vectors   │  │    ├── video/            │   │
│  │  chat_messages        │  │  meeting summaries│  │    ├── documents/        │   │
│  │  session_summaries    │  │  file summaries   │  │    └── images/           │   │
│  │  user_memories        │  │                   │  │                          │   │
│  │  memory_decay_state   │  │  Deterministic IDs│  │                          │   │
│  │  memory_entities      │  │  meeting_{id}_    │  │                          │   │
│  │  memory_relations     │  │  chunk_{i}        │  │                          │   │
│  │  bm25_index           │  │                   │  │                          │   │
│  │  chat_messages_fts    │  │                   │  │                          │   │
│  └──────────────────────┘  └──────────────────┘  └──────────────────────────┘   │
│                                                                                  │
│  Read/Write Split · Thread-local Pool · Write Lock · Auto-migration              │
└──────────────────────────────────────────────────────────────────────────────────┘
                                          │
                                          ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│  INFRASTRUCTURE                                                                   │
│                                                                                  │
│  ┌────────────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐ │
│  │   Config               │  │   Observability   │  │   Deployment             │ │
│  │                        │  │                   │  │                          │ │
│  │  main.yaml (defaults)  │  │  Prometheus       │  │  Docker Compose          │ │
│  │        ↓               │  │  Loki + Promtail  │  │  backend:7008            │ │
│  │  .env (secrets)        │  │  Structured JSON  │  │  frontend:8307           │ │
│  │        ↓               │  │  Logging          │  │                          │ │
│  │  env vars (override)   │  │  OpenTelemetry    │  │  Helm Chart (K8s)        │ │
│  │                        │  │  Tracing          │  │  HPA · PDB · PVC         │ │
│  │  Settings singleton    │  │                   │  │                          │ │
│  │  Pydantic-settings     │  │  Metrics /metrics │  │  GitHub Actions CI       │ │
│  └────────────────────────┘  └──────────────────┘  │  Lint · Test · Security  │ │
│                                                      └──────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────────┐
│  MCP SERVER (src/mcp.py) — External AI Integration (6 tools)                     │
│                                                                                  │
│  ┌────────────────┐  ┌─────────────────┐  ┌─────────────┐  ┌────────────────┐   │
│  │  list_meetings  │  │  search_meetings │  │  ask_about  │  │ manage_memory  │   │
│  │                │  │                 │  │  _meetings  │  │                │   │
│  └────────────────┘  └─────────────────┘  └─────────────┘  └────────────────┘   │
│                                                                                  │
│  ┌────────────────┐  ┌─────────────────┐                                        │
│  │  list_skills    │  │  invoke_skill   │                                        │
│  │                │  │                 │                                        │
│  └────────────────┘  └─────────────────┘                                        │
│                                                                                  │
│                    Delegates to same Service Layer (chain, rag, memory)           │
│                    Runs as standalone process: python -m src.mcp                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

## Key Data Flows

### Ingestion Flow
```
Upload → meetings/upload → processor/pipeline →
  ├── Video/Audio → ASR (AssemblyAI) → transcript
  └── Document/Image → Parser (cascade + quality check) → markdown/text
       → Legacy format conversion (converters.py) if needed
       → rag/indexer → chunk → embed → ChromaDB + BM25
       → Per-file summary generation + embedding
       → SQLite (meeting metadata + file records)
```

### Query Flow
```
Chat Request → chain/ask() or ask_stream() →
  ├── Intent Routing (casual vs retrieval)
  ├── Query Resolve/Rewrite (_resolver.py / _steps_session.py)
  ├── Session Context (history)
  ├── Memory Context (long-term facts)
  ├── Entity Context (knowledge graph)
  ├── Retrieval:
  │   Broad Recall: Summary Router + Funnel Wide Fetch → File Scoping
  │     → Fair Per-File Retrieve → Speaker/Temporal Filters → Rerank
  │   Scoped: Direct retrieve → Filters → Rerank
  ├── Optional Web Search
  ├── LLM Generation (LCEL chain, Anthropic prompt caching)
  ├── Combined Extraction (fact + entity, _extraction.py)
  └── StreamBus → SSE Events → Frontend
```

### Streaming Architecture
```
chain/ask_stream()
  └── asyncio.create_task() ──▶ producer
       └── StreamBus.emit(step/token/sources/trace/error/done)
                                        │
                               async for event in bus
                                        │
                                  SSE data: {...}
                                        │
                                  Frontend sendChatStream()
```
