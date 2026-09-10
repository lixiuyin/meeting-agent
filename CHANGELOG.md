# Changelog

## 2026-09-06 — meeting memory and RAG correctness

- Fixed bitemporal memory authority selection so lifecycle and fact-type changes
  cannot resurrect older matching revisions.
- Added checksum-bound, message-bound full-context continuation snapshots that
  isolate frozen turns from current document, memory, KG, session, and web state.
- Preserved definite future action commitments as open action items while keeping
  forecasts and ordinary future claims pending review.
- Added editable material roles and approval states with automatic native-index
  rebuild, plus timezone-safe memory editing and Unicode-safe evidence offsets.
- Made saved-snapshot continuation fail closed when its evidence is unavailable,
  and made explicit speaker/time retrieval constraints refuse scope widening.
- Added monotonic meeting-file source revisions, immutable semantic review history,
  atomic reindex queueing, rejected-evidence filtering, and dependent auto-memory
  retraction when no accepted evidence remains.
- Added evidence sync state and rejection-reason/history controls to the Materials UI.
- Added a fingerprinted, provider-free meeting-evidence governance benchmark for
  authority filtering, strict temporal scope, source revision fences, and prompt labels.
- Tightened supersession evaluation to require the retired record and replacement
  to share a logical identity.

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Conservative query routing and evidence validation**: requests are
  classified as `atomic_fact`, `bounded_synthesis`, or
  `analytical_synthesis`; only an atomic probe with concentrated,
  answer-shaped, source-identifiable evidence may use the low-latency path.
  Weak or cross-source evidence is promoted to normal hierarchical retrieval
  and reranking, with the decision recorded in the pipeline trace.
- **README interface tour**: the bilingual README now shows a 31-frame GIF that
  includes every supplied Chat screen and long-answer continuation, plus a
  static chat fallback and restored clickable video thumbnails.
- **Demo videos**: README now embeds four YouTube walkthroughs (Full Demo,
  Invoke Skills, Step by Step, Memory & Knowledge Graph) as a clickable
  thumbnail grid linking to <https://www.youtube.com/@lixiuyin>.
- **Current architecture documentation**: reconciled the documentation map,
  full-stack and deployment diagrams, Memory/RAG flows, API surface, runtime
  lifecycle, configuration, migration head, and frontend Memory workspace with
  current source/OpenAPI; dated reports and benchmark plans now state their
  historical or design-only scope.
- **Model-role benchmark publication guidance**: documented the dated main,
  judge, Memory-extraction, and Vision verification roles; published failed SLO
  evidence without converting it into a general model ranking; and added
  requirements for route/configuration disclosure, sanitized aggregates, and
  explicit release-readiness boundaries. Superseded public score snapshots and
  the historical README table were removed; `latest-benchmark.json` is now the
  single current score source.

### Fixed

- **Generation timeout semantics**: removed the erroneous 2.5-second hard
  generation cutoff. The 2.5-second value remains an observability SLO;
  validated atomic streams use independent 10-second first-token, 15-second
  stall, and 30-second total safety budgets, while synthesis uses the ordinary
  configured generation timeout.
- **Full-stack performance evidence capture**: the browser acceptance flow now
  writes its structured report before SLO assertions, so functional evidence
  and measured latency survive a deliberate performance-gate failure.
- **Documentation parity and rendering**: restored clickable README badges,
  documentation links, and YouTube thumbnails; synchronized compact endpoint
  indexes; and made the docs checker reject Markdown links wrapped in code
  ticks.
- **Implementation-grounded ingestion documentation**: corrected the
  transcriber module path, replaced the obsolete four-provider/local routing
  description with the actual three-provider quality-gated cascade, documented
  the PDF-only PyMuPDF fallback, and synchronized the complete upload-extension
  matrix with the canonical file-kind registry.
- **Configuration and identity documentation**: documented every current
  `Settings` field, including the generation deadline, deprecated history
  alias, meeting-summary exploration share, and `PRINCIPAL_ID` ownership
  continuity contract for API-key rotation.
- **Memory workspace viewport containment**: the library selector and memory list
  now share one bounded flex column. On desktop, filters and actions remain
  visible while the virtualized records scroll inside the card instead of
  pushing the final record and evidence text below the rounded container.
- **History and responsive UI**: persisted `ai` messages now render with the
  same Markdown and citation controls as live `agent` messages; chat context
  selectors wrap at mobile widths; and Memory tabs unload hidden entity trees
  while entity groups render only when expanded.
- **BM25 legacy metadata diagnostics**: valid JSON objects are no longer
  misreported as empty metadata. Runtime and one-shot repair paths now recover
  both `file_id` and `chunk_id`, honor the configured database path, and support
  explicit `--db` selection.
- **Browser and summary reliability**: form validation no longer creates an
  unhandled browser rejection, deprecated Ant Design List/Descriptions APIs
  were removed, network-flap coverage is deterministic, and malformed session
  summary output receives one bounded corrective retry.
- **Summary lifecycle and materials status**: startup now requeues incomplete
  file summaries only when automatic summaries are enabled; otherwise parsed
  files return to `ready` with a durable `pending` summary state. The materials
  UI now reserves “Summarizing” for actively generating summaries.
- **History search and batch actions**: semantic session-summary results retain
  their authoritative conversation title; deleting a search result updates the
  visible search state; selection is limited to visible sessions; and the batch
  toolbar remains usable after clearing a selection.
- **Browser acceptance coverage**: the isolated Chromium suite now exercises
  navigation, upload/ingest/RAG, generation skills, materials, history and
  continuation, memory CRUD/search/export/decay, settings/rebuild/reload,
  deletion, streaming aborts, network recovery, and WebSocket behavior. A
  separate production-mode run verifies API-key rejection and access.
- **RAG ownership and score contracts**: all vector, BM25, summary-routing,
  broad-recall, speaker, and optional multimodal paths now retain the request
  principal; retrieval adapters expose one explicit higher-is-better relevance
  contract so distance scores cannot reverse multi-query ranking or web fallback
  decisions.
- **Atomic native-index lifecycle**: per-file replacements use generation-tagged
  shadow writes, verified Chroma/BM25 manifests, rollback, and durable repair
  jobs. Full vector rebuilds swap only after complete transcript-backed rebuild
  and restore the matching BM25 generation on failure.
- **Durable jobs and live settings**: expired final-attempt leases dead-letter
  instead of remaining permanently `running`; admitted requests/jobs use
  immutable settings snapshots; index-shaping updates are rejected until a
  controlled rebuild, while retrieval-only hybrid gating is hot-changeable.
- **Reproducible release promotion**: Docker base images are digest-pinned and
  release workflows test, promote, and sign the exact candidate image digest
  without rebuilding it. Helm now enforces the documented single-backend
  architecture instead of advertising unsupported HPA/PDB behavior.
- **Citation alignment across all source kinds**: chunks, file summaries,
  and meeting summaries now share a single `[N]` index. The markdown
  summary blocks surface `[N]` in their headings (matching the index in
  `[Meeting Content]`), and the system prompt is rewritten as seven
  mechanical sub-rules so every substantive bullet ends with `[N]` —
  fixes intermittent missing citations and "everything cites [1]"
  behavior in multi-meeting answers.
- **Frontend citation jumps**: meeting / file summary citations seed the
  SummaryModal with `source.content` so the modal always shows the
  text the LLM cited, even when the cached summary endpoint is empty
  or missing the per-file entry. Image-derived chunks without an image
  URL now fall back to the file viewer instead of silently no-op'ing.
- **Async / sync embedder boundary**: `_QueryCachedEmbeddings` refuses
  sync calls inside a coroutine; wrapped the remaining offenders in
  `asyncio.to_thread` (KG entity store, alias resolution, batch memory
  import, contradiction-resolution memory upsert, lifespan startup vector
  syncs).
- **Knowledge graph startup**: `sync_missing_entity_vectors` and
  `MemoryService.sync_missing_vectors` used `row.get(...)` on
  `sqlite3.Row` (which only supports subscript access), aborting the
  whole startup vector backfill on the first row. Switched to subscript
  access with explicit NULL handling.
- **Schemathesis contract suite**: brought 19 unique failures + 4 errors
  down — datetime fields now serialize with timezone via a project-wide
  `UTCDatetime = Annotated[datetime, AfterValidator(_ensure_utc)]`;
  every router declares the common 400/401/403/404/409/429/500
  responses; rate-limit 429s wrap slowapi's payload in the
  `ErrorResponse` envelope; `POST /ws/token` typed `request` as
  `Request` so slowapi can extract the IP; `POST /meetings/upload`
  catches `RuntimeError: Stream consumed` from malformed multipart
  bodies and returns 400; schemathesis `--request-timeout` raised to
  60s for the chat endpoint.
- **CI pipeline**: Backup/Restore Integrity persists `DATA_DIR` /
  `DB_PATH` via `$GITHUB_ENV` so later steps see them. Frontend
  Dockerfile uses `npm ci --legacy-peer-deps` to tolerate
  `eslint@10` vs `eslint-plugin-jsx-a11y@6.10.2`.
  `scripts/generate-types.sh` runs prettier on the generated types
  so the contract-openapi-types diff check stays clean.
  `frontend/package-lock.json` patched via `npm audit fix`
  (axios / fast-uri / postcss high-severity advisories).
  Bandit configured with `[tool.bandit] skips=["B608"]` for the
  parameterized-SQL false positives; MD5 / SHA1 fingerprints marked
  `usedforsecurity=False`; remaining 0.0.0.0 sentinels annotated
  with `# nosec B104`.
- **Backend tests**: resolver L1 cache wraps `cachetools.TTLCache` in a
  `threading.RLock` subclass so concurrent writes don't race during
  eviction. `tests/ingestion/test_ingest_trace.py` stubs
  `_convert_pptx_to_pdf` so the multimodal-indexing branch can run
  without LibreOffice. `tests/benchmark/conftest.py` detects the live
  Chroma collection's embedding dimension instead of hard-coding 384.
- **Test history pruned**: removed the >100 MB `materials/Agent/agent.mp4`
  and `materials/Jobs/jobs.mp4` from this branch's history; the
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
- **Meeting status values**: `completed` → `ready`; parent meeting aggregation
  uses `failed`, while file-processing rows and compatibility paths retain the
  explicit `error` state

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
- **RAGAnything multimodal retrieval**: optional dual-index/retrieval pipeline
  with `RAG_RETRIEVER_PROVIDER=multimodal|hybrid_multimodal`; the ordinary
  production strategies are `vector|hybrid` (`native` remains a deprecated
  alias for `vector`, while `raganything` is rejected)
- **Content-aware parser routing**: `parser/_profile.py` + `parser/_router.py` replace simple L1/L2/L3 cascade with document-profile-based provider selection
- **Vision captioner**: image description via configurable vision model
- **TTS configuration**: text-to-speech settings (`TTS_BINDING`, `TTS_MODEL`, `TTS_API_KEY`)
- **Prometheus metrics**: `GET /metrics` endpoint with meeting/session/memory counters
- **Security headers**: configurable CSP, HSTS, X-Frame-Options via `SECURITY_*` settings
- **Idempotency**: AES-GCM encrypted response storage for safe retries
- **Tests expansion**: added dedicated test modules for meetings, knowledge
  graph, RAG decomposition, memory clustering, and parser cascade

### Fixed

- Vector store directory standardized to `data/vectordb/` (was `data/chroma/`)
- Parser deadlines now start at `PARSE_TIMEOUT_SECONDS=300`, add
  `PARSE_TIMEOUT_PER_MB_SECONDS=2` per MiB, and cap at
  `PARSE_TIMEOUT_MAX_SECONDS=900` for large documents
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
