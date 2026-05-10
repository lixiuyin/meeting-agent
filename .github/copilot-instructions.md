# Copilot Instructions for `meeting-agent`

## Build, test, and lint commands

### Backend (`backend/`)
```bash
# Install dependencies (recommended, uses uv.lock)
uv sync --dev

# Run API server
uv run python -m uvicorn src.main:app --reload

# Lint + format check
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/

# Type check
uv run pyright

# Run full backend tests
uv run python -m pytest tests/ -v

# Run a single test file
uv run python -m pytest tests/test_asr_assemblyai.py -v

# Run a single test
uv run python -m pytest tests/test_api.py::TestMeetingsEndpoint::test_upload_unsupported_format -v
```

### Frontend (`frontend/`)
```bash
# Install dependencies
npm ci

# Run dev server
npm run dev

# Build
npm run build

# Lint + type check
npm run lint
npm run type-check

# Run all frontend tests once
npm run test:run

# Run one test file
npm run test:run -- src/test/App.test.tsx

# Run tests matching a name
npm run test:run -- -t "renders"
```

### Repo root shortcuts
```bash
# Run backend + frontend dev servers
make dev

# Lint all
make lint

# Run all tests
make test

# Full QA pipeline (lint + tests + selected Playwright full-stack E2E)
make qa
```

## MCP servers for this repo

```bash
# Backend MCP server (tools: list_meetings/search_meetings/ask_about_meetings/manage_memory)
cd backend
uv run python -m src.mcp
```

For Playwright-based MCP/browser automation sessions, run the app first and target the frontend URL:
- local dev: `http://localhost:5173` (`make dev` or `cd frontend && npm run dev`)
- docker: `http://localhost:8307` (`docker compose up --build`)

## High-level architecture

- This is a full-stack meeting intelligence app: ingest files (video/audio/docs/images) -> extract text/transcript -> chunk/index in Chroma -> answer via RAG + memory.
- Backend is FastAPI under `backend/src`, with all API routes versioned under `/api/v1` and registered in `src/api/__init__.py`.
- Ingestion path is orchestrated in `src/services/processor/_pipeline.py`:
  - audio/video -> `src/services/transcriber.py` (AssemblyAI-only dispatcher, ffmpeg extraction for video)
  - docs/images -> `src/services/parser/cascade.py` (content-aware parser routing with cloud providers + local fallback)
  - extracted content -> RAG indexing (`src/services/rag/*`)
- Query path is orchestrated in `src/services/chain/_api.py`:
  - session ensure + query rewrite
  - retrieval/rerank + memory/session/entity/web/history context loading
  - generation + persistence + async fact extraction
  - streaming uses SSE events via the stream bus and frontend stream parser
- Persistence is SQLite with a thread-local pool and WAL mode in `src/core/database/_connection.py`; reads use `get_connection()`, writes use serialized `get_write_connection()`.
- Frontend is React + Vite in `frontend/`:
  - route shell in `src/App.tsx`
  - typed API/SSE client in `src/api/client.ts` (base URL `/api/v1`, optional `X-API-Key` from `VITE_API_KEY`)

## Key repository conventions

- **Backend package name is `src`** (not `app`); run/import targets use `src.main:app`, `src.mcp`, etc.
- **Meetings router structure is split files + shared `_common.py` only**; sub-routers are expected to import stdlib/FastAPI/db/schema deps directly with three-level relative imports.
- **Router registration order matters**: `file_download` is intentionally registered before `meetings` to ensure token-based file download route precedence for overlapping paths.
- **Heavy/shared services use thread-safe singleton initialization** (double-checked locking) across LLM, embeddings, vector stores, and related providers.
- **Potentially blocking work is offloaded from the event loop** (commonly via `asyncio.to_thread(...)` in pipeline/service layers).
- **Database access pattern is strict**: use `get_connection()` for reads and `get_write_connection()` for all mutations to avoid lock contention.
- **Auth behavior is environment-sensitive**: API uses `X-API-Key`; empty `API_KEY` means dev-mode auth bypass, but non-dev startup refuses to run without `API_KEY`.
- **Tests rely on early constant patching in `backend/tests/conftest.py`** so tests always use temp `DATA_DIR`/`DB_PATH`, not local production data.
