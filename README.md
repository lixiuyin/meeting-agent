# Meeting Agent

[English](README.md) | [简体中文](README.zh-CN.md)

[![CI](https://github.com/lixiuyin/meeting-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/lixiuyin/meeting-agent/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![Node 22–24](https://img.shields.io/badge/node-22--24-green.svg)](https://nodejs.org/)

A full-stack RAG application that ingests meeting recordings, documents,
text/data files, and images; transcribes or parses them; indexes the normalized
content into semantic and lexical stores; and exposes cited Q&A with
conversation memory, knowledge-graph entity tracking, and optional web-search
augmentation. The exact accepted-extension and processor matrix is maintained
in the [ingest guide](backend/docs/ingest-pipeline.md#45-support-formats-and-old-office).

> Documentation status: the maintained guides and architecture references were
> reconciled with the source tree, OpenAPI contract, Compose topology, and
> Alembic chain on 2026-09-09. Dated audit reports remain historical evidence;
> start at [`docs/README.md`](docs/README.md) for the current documentation map.

## Demo Videos

Walk-throughs and feature demos are on the project's YouTube channel:

[![Watch on YouTube](https://img.shields.io/badge/Watch%20on-YouTube-FF0000?style=for-the-badge&logo=youtube&logoColor=white)](https://www.youtube.com/@lixiuyin)

> Click a thumbnail to play on YouTube — GitHub-flavored Markdown does not embed live players inline.

| Full Demo | Invoke Skills |
|:---:|:---:|
| [![Full Demo](https://img.youtube.com/vi/IuMp47AY_Do/maxresdefault.jpg)](https://youtu.be/IuMp47AY_Do) | [![Invoke Skills](https://img.youtube.com/vi/YDGAmJN0t0M/maxresdefault.jpg)](https://youtu.be/YDGAmJN0t0M) |
| End-to-end overview of upload → ingest → chat with citations | Custom skills registered via the API, fired either by direct invocation or by intent matching from chat |
| **Step by Step** | **Memory & Knowledge Graph** |
| [![Step by Step](https://img.youtube.com/vi/76IJ_jyXTMU/maxresdefault.jpg)](https://youtu.be/76IJ_jyXTMU) | [![Memory & Knowledge Graph](https://img.youtube.com/vi/027BUwJe1lE/maxresdefault.jpg)](https://youtu.be/027BUwJe1lE) |
| Chat at three scope levels — unscoped (all meetings), meeting-scoped, and file-scoped — showing how retrieval narrows with each pick | Long-term memory, knowledge-graph entities, and cross-session recall |

### Demo scope versus the current system

> **Version notice:** these videos were recorded with an earlier version and
> demonstrate only part of the current product. They remain useful for the core
> upload, retrieval, citation, skill, and memory concepts, but some navigation,
> screens, and workflows now differ. The running application and the maintained
> [documentation index](docs/README.md) are the source of truth.

| Area | What the earlier videos demonstrate | Capabilities added or substantially expanded afterwards | Current UI |
|---|---|---|---|
| Chat lifecycle | Basic questions, streaming answers, and cited sources | Stop an active generation, withdraw the active turn, and edit or regenerate an earlier message by creating an immutable conversation branch instead of rewriting the original history | **Chat**, **History** |
| Session continuation | Ordinary follow-up questions in one conversation | Preview source changes and continue from the latest state, the previously saved scope, or a saved evidence snapshot | **Chat parameters**, **History** |
| Memory workspace | Long-term memories, entities, and cross-session recall | A seven-view workspace: **Projects**, **Memories**, **Decisions & tasks**, **State changes**, **Meeting review**, **Entities**, and **Past Sessions**, with typed facts, lifecycle state, evidence, revisions, comparison, and project filtering | **Memory** |
| Projects and meeting preparation | Not covered as a unified workflow | Project directory and material assignment, unfinished decisions/tasks, recent state changes, and meeting-review preparation grouped by project | **Memory → Projects / Meeting review** |
| Materials and evidence governance | Uploading and reading meeting files | Reviewable material role/domain/approval metadata, immutable semantic history, rejection of inadmissible evidence, evidence-index synchronization state, and speaker identification with re-indexing | **Materials** |
| Source navigation | Opening cited material | Direct navigation to PDF pages, slides, transcript timestamps, and highlighted evidence excerpts, including synchronized original/parsed PDF views | **Chat citations**, **Materials** |
| Retrieval controls | All-meeting, meeting-level, and file-level scope | Fast/balanced/thorough retrieval profiles, coherent Memory modes, optional web fallback after local-confidence checks, and explicitly labelled source-backed degradation when generation cannot finish safely | **Chat parameters** |
| Reliability and maintenance | Not covered in operational detail | SQLite-backed durable processing for ingestion, summaries, speaker updates, and fact extraction; visible vector-rebuild progress and failure/cancellation state | **Settings → System** |

Because the interface and feature set have changed materially, a new recording
is recommended for an accurate end-to-end product tour. The existing videos can
remain online as earlier-version concept demonstrations if this notice stays
visible beside them.

## Highlights

- **Multi-modal ingestion** — registered video/audio, PDF/Office, text/data,
  and image families, with streaming size limits, filename hardening, binary
  signature checks, content hashing, and durable processing jobs.
- **Cloud-native parsing cascade** — local content profiling selects an ordered
  Marker, MinerU, and Paddle route; provider results pass quality gates, while
  plain text is read locally and only PDFs have a terminal PyMuPDF text
  fallback. Unsupported or exhausted routes end in an explicit failure state.
- **Speaker-diarized ASR** — AssemblyAI with editable speaker → real-name mapping that re-indexes the affected file's vectors and per-file summary.
- **Hybrid retrieval** — semantic (Chroma) + BM25 lexical with Reciprocal Rank Fusion, fair per-file allocation, anchor-based eviction for session continuity, and Cohere / BGE rerank.
- **Unified citations** — chunks, file summaries, and meeting summaries share one `[N]` numbering, all clickable from the chat UI to jump back to source page / slide / timestamp.
- **Governed long-term memory** — evidence-bound and revisioned facts, lifecycle review, bitemporal queries, projects/tasks, recall decay, knowledge-graph entities, and episodic summaries in one bounded Memory workspace.
- **Streaming and durable work** — chat and summary generation stream SSE events; file processing, summaries, speaker re-indexing, and fact extraction use a SQLite-backed lease/retry/dead-letter queue. Vector-generation rebuilds remain separately guarded maintenance tasks.
- **Multi-provider LLM / embedding** — chat supports OpenAI, Azure OpenAI,
  Anthropic, DeepSeek, OpenRouter, Groq, Together, Mistral, Ollama, LM Studio,
  vLLM, and llama.cpp. Embeddings support OpenAI, Azure OpenAI, Ollama, LM
  Studio, Hugging Face, Jina, Cohere, Google Vertex AI, OpenRouter, DeepSeek,
  Together, Groq, Mistral, and vLLM.
- **Hardened API** — versioned `/api/v1`, per-endpoint rate limiting, idempotency keys with AES-GCM-encrypted response storage, HMAC-signed file-download tokens, and structured JSON logs.
- **MCP server** — six tools exposed over stdio (and optional HTTP), so Claude / other agents can drive the system as a backend.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                       Frontend (React 19)                        │
│   chat · materials · memory · history · generate · settings      │
└──────────────────────────────┬──────────────────────────────────┘
                                │ /api/v1 (proxied via Vite/nginx)
┌──────────────────────────────▼──────────────────────────────────┐
│                   FastAPI · LangChain LCEL                       │
│                                                                  │
│  Upload  →  parse / transcribe  →  chunk  →  embed  →  Chroma   │
│                                              ↓                   │
│                                          BM25 + FTS5             │
│                                                                  │
│  Query   →  scope routing  →  retrieve  →  rerank  →  context   │
│                                                       ↓          │
│                                                      LLM         │
│                                                       ↓          │
│                                            answer + citations    │
│                                                                  │
│  Durable jobs: ingest · summaries · fact extraction · speakers   │
│  Memory: typed revisions · evidence · lifecycle · KG · decay     │
└──────────────────────────────────────────────────────────────────┘
```

**Backend** — FastAPI 0.135+ · LangChain LCEL · ChromaDB · SQLite (WAL, Alembic-managed) · slowapi · Pydantic v2.
**Frontend** — React 19 · TypeScript · Vite 6 · Ant Design 6 · react-router v7 · framer-motion.
**Infra** — Docker Compose · Helm chart · Prometheus + Grafana + Loki + Promtail (optional observability stack).

## Quick Start

### Docker (recommended)

```bash
cp backend/.env.example backend/.env
# Edit backend/.env: set LLM_API_KEY (required) and ASSEMBLYAI_API_KEY (for video).
make start
```

`make start` runs detached containers with `restart: unless-stopped`. Use
`make status` to inspect them and `make stop` for a graceful shutdown.

Port mapping declared by Compose (host → container): backend `7008 → 8000`,
frontend `8307 → 8080`.

For an authenticated Compose deployment, copy `.env.compose.example` to
`.env.compose` and set the shared `API_KEY`, `PRINCIPAL_PEPPER`,
`CORS_ORIGINS`, and a non-dev `ENVIRONMENT`. Provider keys remain in
`backend/.env`; `.env.compose` is ignored by git.

- Frontend: <http://localhost:8307>
- Backend API: <http://localhost:7008>
- API docs: <http://localhost:7008/docs>
- WebSocket: `ws://localhost:7008/api/v1/ws`

### Manual setup

```bash
# Backend (Python 3.12+)
cd backend
uv sync --dev                          # recommended; uses uv.lock for reproducible installs

# Optional extras (only install if you actually need the provider):
#   uv sync --dev --no-group production --extra multimodal   # Development evaluation only; see SECURITY.md
#   uv sync --dev --extra google       # Vertex AI embeddings
#   uv sync --dev --extra huggingface  # local HF embeddings + BGE reranker
#   uv sync --dev --extra local        # huggingface + llama-cpp-python (fully offline)
#   uv sync --dev --extra observability # Sentry + OpenTelemetry

cp .env.example .env                   # set LLM_API_KEY and ASSEMBLYAI_API_KEY
uv run python -m uvicorn src.main:app --reload --port 7008  # http://localhost:7008

# Frontend (Node 22–24; separate terminal; `.node-version` selects Node 24)
cd frontend
npm ci
npm run dev                            # http://localhost:8307, proxies /api → :7008
```

### Project-level shortcuts

```bash
make dev          # backend + frontend concurrently
make dev-be       # backend only
make dev-fe       # frontend only
make cli          # interactive terminal frontend (scripts.cli_agent)
make lint         # lint everything
make test         # run all tests
make e2e-auth     # isolated production-mode API-key browser checks
make e2e-full-stack # isolated live browser acceptance suite
make qa           # full QA: lint + tests + build + isolated Playwright E2E
make clean        # remove generated files
```

### Pre-commit hooks

```bash
pip install pre-commit && pre-commit install
```

Hooks in `.pre-commit-config.yaml`: ruff, eslint, prettier, bandit, gitleaks, detect-secrets.

## Configuration

Three-tier override (highest priority last):

1. `backend/config/main.yaml` — non-secret defaults (model names, RAG knobs, upload limits).
2. `backend/.env` — secrets and per-environment overrides.
3. **Environment variables** — for Docker / CI.

Settings are merged via pydantic-settings. `backend/.env.example` intentionally
contains only credentials and common overrides; use `backend/config/main.yaml`
or `backend/docs/configuration.md` for advanced tuning.

### Key settings

| Setting | Default | Description |
|---|---|---|
| `LLM_BINDING` | `openai` | `openai`, `azure_openai`, `anthropic`, `deepseek`, `openrouter`, `groq`, `together`, `mistral`, `ollama`, `lm_studio`, `vllm`, `llama_cpp` |
| `LLM_MODEL` | `gpt-4o-mini` | Any chat model the binding supports |
| `LLM_API_KEY` | *(required)* | API key for the chosen LLM provider |
| `LLM_BASE_URL` / `LLM_HOST` | *(empty)* | Custom endpoint for OpenAI-compatible / local providers |
| `EMBEDDING_BINDING` | `openai` | `openai`, `azure_openai`, `ollama`, `lm_studio`, `huggingface`, `jina`, `cohere`, `google`, `openrouter`, `deepseek`, `together`, `groq`, `mistral`, `vllm` |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model |
| `EMBEDDING_DIMENSION` | `1536` | Must match the model's vector size |
| `ASR_PROVIDER` | `assemblyai` | Only `assemblyai` is supported |
| `ASSEMBLYAI_API_KEY` | *(required for AV)* | env-only, never in YAML |
| `OCR_PROVIDER` | `marker` | Routing hint: `marker`, `mineru`, `paddle` |
| `RAG_RETRIEVER_PROVIDER` | `hybrid` | `vector`, `hybrid`, `multimodal`, `hybrid_multimodal` (`native` is a deprecated alias for `vector`) |
| `RAGANYTHING_ENABLED` | `false` | Development-only multimodal branch; production is blocked pending upstream dependency fixes |
| `SEARCH_BINDING` | `exa` | Web search: `duckduckgo`, `serpapi`, `tavily`, `bing`, `exa`; empty disables search |
| `MEMORY_AUTO_EXTRACT` | `true` | Auto-extract facts from each Q&A turn |
| `KNOWLEDGE_GRAPH_ENABLED` | `false` | Opt-in research feature: index entities + relations into the KG |
| `ENVIRONMENT` | `dev` | `dev`, `staging`, `production` (`prod` is an alias; non-dev requires `API_KEY`) |
| `API_KEY` | *(empty)* | Empty = dev mode (auth bypassed); required for staging/production |
| `PRINCIPAL_PEPPER` | *(empty)* | Required secret for stable irreversible principal derivation outside development |
| `PRINCIPAL_ID` | *(unset)* | Optional verified existing owner ID for preserving ownership across API-key rotation; not multi-user auth |
| `LOG_FORMAT` | `text` | Set to `json` for structured logs |

### Helm deployment notes (SQLite)

- Run backend as a single replica (`backend.replicaCount=1`) — SQLite cannot share writers.
- Provide secrets via Kubernetes Secret and set `backend.secretName`.
- HPA / PDB templates are intentionally absent for SQLite safety.

### Using Dashscope (Qwen)

```env
LLM_MODEL=qwen-plus
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_API_KEY=sk-your-dashscope-key
```

## MCP Server

```bash
uv run python -m src.mcp                  # stdio transport
MCP_TRANSPORT=streamable-http MCP_HTTP_PORT=9000 \
  MCP_API_KEY=replace-me uv run python -m src.mcp  # loopback HTTP
```

Tools: `list_meetings`, `search_meetings`, `ask_about_meetings`, `manage_memory`, `list_skills`, `invoke_skill`.

## API Endpoints

All application routes are versioned at `/api/v1`. Protected routes use the `X-API-Key` header (empty `API_KEY` = dev mode); liveness/readiness and basic health probes are intentionally unauthenticated. Rate limits are per-endpoint (upload / chat 20 / min, settings 5 / min, reads 60 / min). Errors share a unified `ErrorResponse` envelope (`code`, `message`, `request_id`, `details`). See [`backend/docs/api-reference.md`](backend/docs/api-reference.md) for the complete contract.

### Meetings

| Method | Path | Description |
|---|---|---|
| `POST` | `/meetings/upload` | Upload a file (creates new meeting if no `meeting_id`) |
| `POST` | `/meetings` | Create a new empty meeting |
| `GET` | `/meetings` | List meetings (filterable by status) |
| `GET` | `/meetings/{id}` | Meeting detail with file list |
| `PUT` | `/meetings/{id}` | Update meeting metadata |
| `DELETE` | `/meetings/{id}` | Delete a meeting and all files |
| `GET` | `/meetings/{id}/files` | List files for a meeting |
| `GET` | `/meetings/{id}/files/{fid}` | Download a file (header `X-API-Key` or `?token=`) |
| `POST` | `/meetings/file-token` | Issue a short-lived global file token |
| `POST` | `/meetings/{id}/files/{fid}/signed-url` | Issue a file-scoped HMAC-signed URL |
| `GET` | `/meetings/assets` | Fetch a meeting asset by relative path |
| `GET` | `/meetings/{id}/files/{fid}/timeline` | File timeline (segments / pages / captions / text) |
| `PATCH` | `/meetings/{id}/files/{fid}/semantics` | Review material role/approval with revision-fenced re-indexing |
| `GET` | `/meetings/{id}/files/{fid}/semantics/history` | Material semantic-review history |
| `POST` | `/meetings/{id}/files/{fid}/evidence-location` | Resolve evidence to page/slide/timestamp |
| `GET` | `/meetings/{id}/files/{fid}/speakers` | List speaker mappings |
| `PUT` | `/meetings/{id}/files/{fid}/speakers` | Update speaker → real-name mappings |
| `GET` | `/meetings/{id}/files/{fid}/speakers/{code}/audio` | Sample audio clip for a speaker |
| `DELETE` | `/meetings/{id}/files/{fid}` | Delete a single file from a meeting |
| `POST` | `/meetings/{id}/summary` | Generate / fetch meeting summary |
| `POST` | `/meetings/{id}/summary/stream` | Stream meeting summary via SSE |
| `POST` | `/meetings/{id}/reprocess` | Re-index all files of a meeting |
| `POST` | `/meetings/{id}/files/{fid}/reprocess` | Re-index a single file |
| `GET` | `/meetings/{id}/transcript` | Full transcript text |
| `GET` | `/meetings/{id}/transcript/timestamps` | Transcript with timestamp segments |
| `GET` | `/meetings/{id}/export` | Export meeting (JSON / Markdown / TXT) |
| `GET` | `/meetings/search/content` | Full-text search inside transcripts |

### Chat

| Method | Path | Description |
|---|---|---|
| `POST` | `/chat` | RAG Q&A with memory; `rag_mode` per-query: `vector` / `hybrid` / `multimodal` / `hybrid_multimodal` / `auto` |
| `POST` | `/chat/stream` | Streaming answer via SSE (token / sources / status / trace / done events) |
| `GET` | `/chat/runs/{run_id}` / `events` | Inspect a persisted run and replay events |
| `POST` | `/chat/runs/{run_id}/cancel` / `withdraw` | Cancel generation or withdraw its turn |
| `POST` | `/chat/search` | Semantic + BM25 search only, no LLM |

### Sessions

| Method | Path | Description |
|---|---|---|
| `GET` | `/sessions` | List chat sessions |
| `GET` | `/sessions/{id}/messages` | Session message history with sources |
| `POST` | `/sessions/{id}/branches` | Branch from a persisted message boundary for edit/regenerate workflows |
| `GET` | `/sessions/{id}/continuation-preview` | Validate latest/saved-scope/saved-snapshot continuation |
| `DELETE` | `/sessions/{id}` | Delete a session and its messages |
| `POST` | `/sessions/batch-delete` | Delete up to 100 sessions |
| `POST` | `/sessions/{id}/summarize` | Generate session summary |
| `GET` | `/sessions/{id}/summary` | Fetch existing summary |
| `GET` | `/sessions/{id}/cite` | Citation context for a session |
| `GET` | `/sessions/summaries` | Cross-session summary list |
| `POST` | `/sessions/search` | Semantic search across past sessions |

### Memory & Knowledge Graph

The `/memory` workspace is a governed interface for reviewing, organizing, and
reusing long-term knowledge extracted from conversations and meeting materials.
Records retain lifecycle state, revisions, project scope, and source evidence
instead of being treated as unverified free-form model memory.

| View | User-facing capabilities |
|---|---|
| **Projects** | Create project directories, bind meeting materials, prepare for meetings, and review project-scoped tasks and changes |
| **Memories** | Browse personal/project and reference libraries; search, filter, create, edit, confirm, retract, import, export, score, decay, delete, and repair vector indexing |
| **Decisions & tasks** | Query decisions, action items, and project facts by project, owner, status, deadline, source, and historical time |
| **State changes** | Compare recorded fact states between two business-time boundaries |
| **Meeting review** | Review automatically extracted candidates and conflicts, inspect source evidence, edit facts, and confirm or retract revisions |
| **Entities** | Browse extracted entities and relations, merge aliases, and delete incorrect entities |
| **Past Sessions** | Browse episodic summaries, topics, and decisions, then continue the original conversation |

Key governance behavior:

- confirmed personal or project memories can participate in later semantic recall;
- reference facts remain inspectable without automatically becoming personal memory;
- retraction preserves revision history, while deletion removes the record;
- evidence links can return to the originating session or exact source location;
- bitemporal queries distinguish when a fact was valid from when the system knew it.

The desktop tab bar and narrow-screen selector expose the same seven views. The
library selector, filters, and bulk actions stay inside the page card; only the
virtualized record region scrolls on desktop, while narrow screens use a bounded
responsive list. See the [frontend Memory workspace guide](frontend/docs/architecture.md#memory-workspace)
for interaction and layout details, and [Memory and Knowledge Graph](backend/docs/memory-and-kg.md)
for extraction, persistence, recall, lifecycle, and KG behavior.

| Method | Path | Description |
|---|---|---|
| `GET` / `POST` / `PUT` / `DELETE` | `/memory` | CRUD on long-term memories |
| `GET` / `PUT` | `/memory/projects` | Project directory and revision-checked updates |
| `POST` | `/memory/facts/query` | Deterministic typed/bitemporal facts and tasks |
| `POST` | `/memory/facts/changes` | Compare authoritative fact states |
| `POST` | `/memory/review/query` | Stable Meeting Review candidate paging |
| `GET` | `/memory/versions` | Immutable fact revision history |
| `POST` | `/memory/resolve-conflict` | Atomically resolve competing fact revisions |
| `POST` | `/memory/batch` | Batch import |
| `POST` | `/memory/batch-delete` | Delete up to 100 memories atomically |
| `GET` | `/memory/export` | Cursor-paginated JSON export |
| `POST` | `/memory/search` | Semantic search |
| `POST` | `/memory/decay` | Trigger freshness decay |
| `POST` | `/memory/feedback` | Record explicit memory usefulness feedback |
| `GET` | `/memory/entities` | List KG entities |
| `POST` | `/memory/entities/batch-delete` | Delete up to 100 KG entities |
| `GET` | `/memory/entities/{name}` | Entity detail with relations |
| `DELETE` | `/memory/entities/{name}` | Delete an entity |
| `POST` | `/memory/entities/merge` | Merge duplicate entities |

### Skills

| Method | Path | Description |
|---|---|---|
| `POST` | `/skills` | Register a custom skill |
| `GET` | `/skills` | List skills |
| `POST` | `/skills/match` | Test intent matching (debug) |
| `POST` | `/skills/invoke` | Invoke a skill directly |

### Settings & system

| Method | Path | Description |
|---|---|---|
| `GET` / `PUT` | `/settings` | Read / update runtime settings (in-memory) |
| `GET` | `/settings/bindings` | List available provider bindings |
| `GET` | `/settings/rebuild-status` | Inspect vector/multimodal rebuild state |
| `POST` | `/settings/rebuild-vectors` | Atomically rebuild compatible native indexes; fails closed when source artefacts require durable reprocessing |
| `POST` | `/settings/rebuild-multimodal` | Backfill multimodal (RAGAnything) index |
| `POST` | `/settings/reload-config` | Reload `main.yaml` from disk |
| `DELETE` | `/settings/account` | Wipe all data for the calling user |
| `GET` | `/health` | Full dependency health check |
| `GET` | `/health/live` | Liveness probe |
| `GET` | `/health/ready` | Readiness probe |
| `GET` | `/health/traffic` | Traffic controller status |
| `GET` | `/health/index-consistency` | Vector / FTS index consistency |
| `GET` | `/health/jobs` / `/health/capabilities` | Durable work and provider-capability status |
| `WS` | `/ws` | Real-time progress / completion notifications |

## Development

### Backend

```bash
cd backend

uv sync --dev
uv run python -m uvicorn src.main:app --reload         # dev server
uv run python -m src.mcp                               # MCP server (stdio)
uv run python -m scripts.cli_agent                     # interactive CLI

uv run ruff check src/ tests/                          # lint
uv run ruff format --check src/ tests/                 # format check
uv run pyright                                         # types

uv run python -m pytest                                # complete backend suite
uv run python -m pytest tests/chain/                   # one directory
uv run python -m pytest tests/meetings/test_api.py::TestMeetingsEndpoint::test_upload_unsupported_format
```

Markers (defined in `pyproject.toml`): `unit`, `integration`, `benchmark`, `property`, `chaos`.

Test isolation: `conftest.py` monkey-patches `constants.DATA_DIR` before any app import, so tests use a temporary database — never the production `data/meetings.db`. Don't import app modules at module level in `conftest.py`.

CLI usage reference: `backend/docs/cli.md`.

### Frontend

```bash
cd frontend

npm install
npm run dev                    # port 8307, proxies /api → :7008
npm run build                  # production build
npm run lint                   # eslint
npm run lint:fix
npm run format                 # prettier --write
npm run format:check
npm run type-check             # tsc --noEmit
npm run test                   # vitest watch
npm run test:run               # vitest single run
npm run e2e                    # Playwright (all browsers)
npm run e2e:headed             # headed Chromium
npm run mutation               # Stryker mutation testing
```

Run a single frontend test: `npm run test:run -- src/test/App.test.tsx` or `-- -t "renders welcome"`.

### Benchmarking

```bash
cd backend

uv run python -m scripts.benchmark chat --iterations 5     # chat pipeline latency
uv run python -m scripts.benchmark ingest --iterations 3   # ingest pipeline
uv run python -m scripts.benchmark micro                   # component micro-benchmarks
uv run python -m scripts.benchmark rag-all                 # retrieval + answer + snapshots
uv run python -m scripts.benchmark reranker-quality        # controlled reranker comparison
uv run python -m scripts.benchmark all --iterations 5 \
  --process-report benchmark-results/e2e-smoke.json        # every required suite
```

Benchmarks run against temporary databases and synthetic fixtures in `tests/fixtures/benchmark/`; results land in `benchmark-results/`. See `backend/docs/benchmarking.md` for the full guide.

#### Latest benchmark results

The table below is the only current score summary. It comes from the latest
completed verification captured on **2026-09-09 at 03:39 UTC**. The
machine-readable results, report hashes, model roles, and limitations are in
[`docs/validation/latest-benchmark.json`](docs/validation/latest-benchmark.json).

| Suite | Latest evaluated scope | Latest result | Status / boundary |
|---|---:|---|---|
| Protocol audit | 9 families | `valid=true`; `execution_ready=true` | Protocol readiness only |
| Evidence governance | 8 cases | Authority, label visibility, revision fence, and temporal-scope accuracy: **1.000** each | Passed synthetic policy check |
| RAG answer | 10 cases × 3 judge repeats | Faithfulness **0.997**; relevance **1.000**; context precision **0.983**; context recall **1.000**; correctness **0.997**; citation quality **0.932** | Passed synthetic diagnostic; fewer than 30 cases |
| Multi-turn | 6 cases × 1 judge repeat | Faithfulness **0.992**; appropriateness **1.000**; naturalness **0.988**; completeness/evidence recall/session continuity **1.000** | Diagnostic only; one judge repeat |
| Memory extraction | 22 events | **22/22** correct; write recall/latest-value/evidence rate **1.000** | Passed synthetic production-path diagnostic |
| Reranker quality | 8 × 12-candidate cases | MRR **1.000**; nDCG@10 **0.990** | Latest reranked scores; 8 evaluated, 0 skipped |
| RAG retrieval | 10 cases | Hybrid recall@10 **0.700**; file recall@8 **1.000** | Reranker: 0 evaluated, 10 skipped; one vector timeout fell back to BM25 |
| Vision entry point | 1 real UI screenshot | Caption + 2,248 OCR characters + semantics in **5.44 s** | Smoke only; not a representative vision corpus |
| Upload → ready → cited chat | 1 isolated fixture | Ready **0.94 s**; TTFT **1.87 s**; total **2.72 s**; citation/fact/source checks passed | Functional smoke passed |
| Main-model Chat | 20 requests | 20/20 completed; degraded **45%**; TTFT P95 **4.17 s**; total P95 **6.15 s** | **SLO gate failed** |

The verification-time roles were `z-ai/glm-5.3-flash` for main generation,
`qwen/qwen3.8-flash` for independent judging, and
`deepseek/deepseek-v4-flash-vision-exp` for Memory extraction and Vision. These
are project-specific synthetic diagnostics, not repository defaults or a general
model ranking. The OpenRouter provider endpoint was not pinned. The system
remains **not release-ready** because the main Chat SLO failed, the public
quality sets are small and synthetic, Multi-turn used one judge repeat, Vision
used one screenshot, and the embedding path showed an intermittent timeout.

## Observability

One-command monitoring stack:

```bash
export GRAFANA_ADMIN_PASSWORD="$(openssl rand -hex 24)"
docker compose -f docker-compose.yaml -f docker-compose.observability.yaml up
```

- **Prometheus** (port 9090) scrapes the backend `/metrics` endpoint.
- **Grafana** (port 3001) — pre-wired dashboards for Prometheus + Loki.
- **Loki + Promtail** (port 3100) ship files from `data/logs/` without exposing the Docker socket.
- **Alert rules** (`monitoring/prometheus/alerts.yaml`) cover request latency, error rate, circuit breaker trips, and disk usage.

## Release

Releases are tag-driven:

```bash
git tag v0.2.0
git push origin v0.2.0
```

`.github/workflows/release.yml` builds and pushes multi-arch backend / frontend images to GHCR, smoke-tests them, runs the full-stack E2E suite, validates the Helm chart, generates a changelog with git-cliff, and creates the GitHub Release. The manual recovery action only promotes previously published container images. It requires an explicit binary-only confirmation and a same-repository GitHub issue carrying the `data-recovery-approved` label; it does not downgrade or restore SQLite, uploads, or Chroma.

Image publication also fails closed unless
`docs/validation/release-readiness.json` exists, comes from a clean run,
matches the current dataset/harness/implementation fingerprints, and explicitly
records successful production quality, human business review, performance-SLO,
and security gates.
Each gate must reference a regular in-repository JSON artifact by path and
SHA-256. The artifact must be unexpired, name a reviewer, bind itself to the
same implementation fingerprints, and contain the gate-specific completion
fields shown by
[`docs/validation/release-evidence-artifact.template.json`](docs/validation/release-evidence-artifact.template.json).
The workflow then validates the exact candidate-image
digests before promotion. Start from
`docs/validation/release-readiness.template.json`; historical validation files
do not satisfy this gate.

The candidate smoke test starts the backend with production validation enabled
and uses the same protected provider configuration as the quality workflow.
Configure these repository Actions secrets before cutting a release:
`QUALITY_LLM_API_KEY`, `QUALITY_LLM_BASE_URL`, `QUALITY_LLM_MODEL`,
`QUALITY_EMBEDDING_API_KEY`, `QUALITY_EMBEDDING_BASE_URL`, and
`QUALITY_EMBEDDING_MODEL`. Missing values stop the candidate job before any
image is promoted.

## Operations & Governance

- SLOs: `backend/docs/operations/slo.md`
- SLA: `backend/docs/operations/sla.md`
- Backup / restore: `backend/docs/operations/backup.md`, `backend/docs/operations/restore.md`
- Retention: `backend/docs/operations/retention.md`
- Migrations: `backend/docs/operations/alembic.md`
- Incident runbooks: `backend/docs/operations/runbooks/`
- Architecture decisions (ADRs): product ADRs in [`docs/adr/`](docs/adr/); stack ADRs in [`backend/docs/adr/`](backend/docs/adr/)
- Diagrams (RAG flow, memory layers, architecture): [`docs/diagrams/`](docs/diagrams/)
- Documentation hub: [`docs/README.md`](docs/README.md)
- Getting started: [`docs/getting-started.md`](docs/getting-started.md); API integration: [`docs/api-quickstart.md`](docs/api-quickstart.md)
- Data lifecycle and operations: [`docs/data-lifecycle.md`](docs/data-lifecycle.md), [`docs/operations-guide.md`](docs/operations-guide.md)
- Development and benchmarking: [`docs/development-guide.md`](docs/development-guide.md)
- Frontend architecture and testing/CI: [`frontend/docs/architecture.md`](frontend/docs/architecture.md), [`frontend/docs/testing.md`](frontend/docs/testing.md)
- Backend subsystem docs: [`backend/docs/README.md`](backend/docs/README.md)

## License

[MIT](LICENSE)
