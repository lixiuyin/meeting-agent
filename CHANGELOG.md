# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Demo videos**: README now embeds four YouTube walkthroughs (Full Demo,
  Invoke Skills, Step by Step, Memory & Knowledge Graph) as a clickable
  thumbnail grid linking to <https://www.youtube.com/@lixiuyin>.

### Fixed
- **Citation alignment across all source kinds**: chunks, file summaries,
  and meeting summaries now share a single ``[N]`` index. The markdown
  summary blocks surface ``[N]`` in their headings (matching the index in
  ``[Meeting Content]``), and the system prompt is rewritten as seven
  mechanical sub-rules so every substantive bullet ends with ``[N]`` —
  fixes intermittent missing citations and "everything cites [1]"
  behavior in multi-meeting answers.
- **Frontend citation jumps**: meeting / file summary citations seed the
  SummaryModal with ``source.content`` so the modal always shows the
  text the LLM cited, even when the cached summary endpoint is empty
  or missing the per-file entry. Image-derived chunks without an image
  URL now fall back to the file viewer instead of silently no-op'ing.
- **Async / sync embedder boundary**: ``_QueryCachedEmbeddings`` refuses
  sync calls inside a coroutine; wrapped the remaining offenders in
  ``asyncio.to_thread`` (KG entity store, alias resolution, batch memory
  import, contradiction-resolution memory upsert, lifespan startup vector
  syncs).
- **Knowledge graph startup**: ``sync_missing_entity_vectors`` and
  ``MemoryService.sync_missing_vectors`` used ``row.get(...)`` on
  ``sqlite3.Row`` (which only supports subscript access), aborting the
  whole startup vector backfill on the first row. Switched to subscript
  access with explicit NULL handling.
- **Schemathesis contract suite**: brought 19 unique failures + 4 errors
  down — datetime fields now serialize with timezone via a project-wide
  ``UTCDatetime = Annotated[datetime, AfterValidator(_ensure_utc)]``;
  every router declares the common 400/401/403/404/409/429/500
  responses; rate-limit 429s wrap slowapi's payload in the
  ``ErrorResponse`` envelope; ``POST /ws/token`` typed ``request`` as
  ``Request`` so slowapi can extract the IP; ``POST /meetings/upload``
  catches ``RuntimeError: Stream consumed`` from malformed multipart
  bodies and returns 400; schemathesis ``--request-timeout`` raised to
  60s for the chat endpoint.
- **CI pipeline**: Backup/Restore Integrity persists ``DATA_DIR`` /
  ``DB_PATH`` via ``$GITHUB_ENV`` so later steps see them. Frontend
  Dockerfile uses ``npm ci --legacy-peer-deps`` to tolerate
  ``eslint@10`` vs ``eslint-plugin-jsx-a11y@6.10.2``.
  ``scripts/generate-types.sh`` runs prettier on the generated types
  so the contract-openapi-types diff check stays clean.
  ``frontend/package-lock.json`` patched via ``npm audit fix``
  (axios / fast-uri / postcss high-severity advisories).
  Bandit configured with ``[tool.bandit] skips=["B608"]`` for the
  parameterized-SQL false positives; MD5 / SHA1 fingerprints marked
  ``usedforsecurity=False``; remaining 0.0.0.0 sentinels annotated
  with ``# nosec B104``.
- **Backend tests**: resolver L1 cache wraps ``cachetools.TTLCache`` in a
  ``threading.RLock`` subclass so concurrent writes don't race during
  eviction. ``tests/ingestion/test_ingest_trace.py`` stubs
  ``_convert_pptx_to_pdf`` so the multimodal-indexing branch can run
  without LibreOffice. ``tests/benchmark/conftest.py`` detects the live
  Chroma collection's embedding dimension instead of hard-coding 384.
- **Test history pruned**: removed the >100 MB ``materials/Agent/agent.mp4``
  and ``materials/Jobs/jobs.mp4`` from this branch's history; the
  directory is now ignored.

### Changed
- **Documentation**: added [`docs/README.md`](docs/README.md) as the repo-level doc index; RAG and memory overview diagrams live under [`docs/diagrams/`](docs/diagrams/) (cross-linked from [`backend/docs/README.md`](backend/docs/README.md) and [`backend/docs/architecture.md`](backend/docs/architecture.md)); root [`README.md`](README.md) now lists both `docs/adr/` and `backend/docs/adr/` for ADRs; stub pages [`docs/rag-pipeline.md`](docs/rag-pipeline.md) and [`docs/memory-and-kg.md`](docs/memory-and-kg.md) redirect to the diagram folder for old links
- **Architecture refactor**: split oversized modules into cohesive packages
  - `src/services/chain.py` → `src/services/chain/` package (`_api.py`, `_context.py`, `_routing.py`, `_steps_retrieve.py`, `_steps_context.py`, `_steps_generate.py`, `_steps_session.py`, `_formatting.py`)
  - `src/services/memory.py` → `src/services/memory/` package (`_service/`, `_parsers.py`, `_decay.py`, `_vectorstore.py`)
  - `src/api/routers/meetings.py` → `src/api/routers/meetings/` package (per-endpoint modules: `_upload.py`, `_create.py`, `_list.py`, `_detail.py`, `_update.py`, `_delete.py`, `_files.py`, `_summary.py`, `_reprocess.py`, `_transcript.py`, `_timestamps.py`, `_export.py`, `_search.py`, `_speakers.py`)
  - `src/core/database.py` → `src/core/database/` package (`meetings.py`, `chat.py`, `memories.py`, `bm25.py`, `knowledge_graph.py`, `_connection.py`, `_migrations.py`, `idempotency.py`, `index_state.py`)
- All packages maintain backward-compatible imports via `__init__.py` re-exports
- **ASR provider**: removed local Whisper and VibeVoice; AssemblyAI is now the sole ASR provider (keeps Docker image lean — no PyTorch/CUDA)
- **Meeting status values**: `completed` → `ready`, `error` → `failed` across all APIs and database

### Added
- **Knowledge Graph**: entity/relation extraction, CRUD API (`GET/DELETE /memory/entities/{name}`, `POST /memory/entities/merge`), and semantic integration with memory service
- **Session summaries**: episodic cross-session memory with `POST /sessions/{id}/summarize`, `GET /sessions/summaries`, `GET /sessions/{id}/summary`, and `POST /sessions/search`
- **Memory batch operations**: `POST /memory/batch` (bulk import) and `GET /memory/export`
- **Speakers API**: speaker identification, rename, and audio clip extraction per file (`GET/PUT /meetings/{id}/files/{fid}/speakers`, `GET /meetings/{id}/files/{fid}/speakers/{code}/audio`)
- **Skills system**: runtime skill definitions with intent matching (`POST /skills`, `POST /skills/match`, `POST /skills/invoke`)
- **Extended health probes**: `GET /health/live`, `GET /health/ready`, `GET /health/traffic`, `GET /health/index-consistency`
- **File download tokens**: HMAC-SHA256 short-lived tokens for secure file access (`POST /meetings/{id}/files/{fid}/signed-url`)
- **Streaming summary**: `POST /meetings/{id}/summary/stream` for SSE-based summary generation
- **Per-file reprocessing**: `POST /meetings/{id}/files/{fid}/reprocess`
- **File timeline**: `GET /meetings/{id}/files/{fid}/timeline` (keyframes/pages)
- **Session citation**: `GET /sessions/{id}/cite`
- **Settings endpoints**: `POST /settings/rebuild-multimodal`, `POST /settings/reload-config`
- **RAGAnything multimodal retrieval**: optional dual-index/retrieval pipeline with `RAG_RETRIEVER_PROVIDER=native|raganything|hybrid_multimodal`
- **Content-aware parser routing**: `parser/_profile.py` + `parser/_router.py` replace simple L1/L2/L3 cascade with document-profile-based provider selection
- **Vision captioner**: image description via configurable vision model
- **TTS configuration**: text-to-speech settings (`TTS_BINDING`, `TTS_MODEL`, `TTS_API_KEY`)
- **Prometheus metrics**: `GET /metrics` endpoint with meeting/session/memory counters
- **Security headers**: configurable CSP, HSTS, X-Frame-Options via `SECURITY_*` settings
- **Idempotency**: AES-GCM encrypted response storage for safe retries
- **Tests expansion**: ~785 tests across 104 files, adding dedicated test modules for meetings, knowledge graph, RAG decomposition, memory clustering, and parser cascade

### Fixed
- Vector store directory standardized to `data/vectordb/` (was `data/chroma/`)
- `PARSE_TIMEOUT_SECONDS` default raised to 900s (was 120s) for large documents
- Security: `.env.example` no longer contains real API keys

## [0.1.0] - 2026-04-02

### Added
- Meeting file upload (video/PDF/PPT) with background processing
- AssemblyAI-based audio transcription (was Whisper at launch, migrated in Unreleased)
- PDF and PPT text extraction
- RAG pipeline with LangChain LCEL (chunking, embedding, retrieval)
- ChromaDB vector store for semantic search
- Multi-turn conversation with SQLite-backed session history
- Long-term user memory with LLM-based auto-extraction
- MCP server with 4 tools for external integration
- React frontend with Ant Design
- Docker Compose deployment
- YAML + .env + env vars three-tier configuration
- Comprehensive test suite (~167 tests) with pytest
- Document parsing cascade (Marker → MinerU → PaddleOCR)
- Web search augmentation (DuckDuckGo, SerpAPI, Tavily, Bing, Exa)
- Image upload support (PNG, JPG, WebP, TIFF, BMP)
- Rate limiting and structured JSON logging
- Advanced RAG: query rewriting, document reranking (Cohere/BGE), hybrid search (vector + BM25), parent-child chunking
- Multi-file meeting support: upload multiple files per meeting, individual file management
- Streaming chat via Server-Sent Events (`POST /chat/stream`)
- Meeting operations: LLM-powered summary, reprocessing, transcript with timestamps, export (JSON/Markdown/TXT)
- Full-text search within meeting transcripts (`GET /meetings/search/content`)
- Memory enhancements: semantic search over memories, importance decay with configurable TTL
- Vector index rebuild endpoint (`POST /settings/rebuild-vectors`)
- Audit logging for data mutations (`src/core/audit.py`)
- Database schema versioning with auto-migration
- Session cache JSON persistence with legacy pickle migration
- Frontend GenerationPage for AI-powered content generation (summaries, action items, emails)
- Virtual scrolling for large meeting lists
- CI pipeline with uv, ruff, pyright, vitest, pip-audit, npm audit
