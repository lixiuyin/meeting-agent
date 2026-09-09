# REST API reference

> Protected endpoints use the `X-API-Key` header. Liveness/readiness probes are
> intentionally public, file/media routes may accept a short-lived signed token,
> and the WebSocket handshake may use its own short-lived token. An empty
> `API_KEY` is permitted only by the development configuration boundary.
>
> Code location: `backend/src/api/routers/`, response schema: `backend/src/models/schemas/`.

**Contract reconciliation:** 2026-09-09. The current-source OpenAPI document
exposed 83 paths and 96 operations. Generate `/openapi.json` for exact schemas;
the tables below are a navigational reference, not a generated-contract
replacement.

## 1. Agreement

- **Base URL**: `http://<host>:<port>/api/v1`
- **Authentication**: `X-API-Key: <your-key>` (see [`configuration.md`](./configuration.md))
- **Rate limiting**: `slowapi` defaults to **60/min** (`Limiter` in `middleware.py`); explicit overrides include `POST /meetings/upload` at 20/min, `POST /chat` and `/chat/stream` at 20/min, asset-token creation at 60/min, settings mutations at 5/min, `DELETE /meetings/{id}` at 10/min, speaker updates at 3/min, and memory/session mutations at their route-specific limits. `/health/*` is exempt. With `API_KEY` enabled, buckets use an API-key hash; otherwise they use the client IP (the first `X-Forwarded-For` address under the trusted proxy configuration). `DISABLE_RATE_LIMIT=1` is available only in development and test environments.
- **Response format**: JSON business responses use declared Pydantic schemas;
  SSE, WebSocket, file, and Prometheus responses use their protocol/media
  contracts. Errors use the unified `ErrorResponse` envelope below.
- **Request ID**: Response headers `X-Request-ID`, `X-Response-Time`

## 2. Meetings

Routing prefix: `/meetings`, code: `api/routers/meetings/`.

| Method   | Path                                               | Behavior                                                                                           |
| -------- | -------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| `POST`   | `/meetings/upload`                                 | Upload files (create a new meeting or append to an existing meeting)                               |
| `POST`   | `/meetings`                                        | Create an empty meeting                                                                            |
| `GET`    | `/meetings`                                        | List (can be filtered by `?status=processing`, etc.)                                               |
| `GET`    | `/meetings/{id}`                                   | Details + File List                                                                                |
| `PUT`    | `/meetings/{id}`                                   | Update metadata (title/description/date)                                                           |
| `DELETE` | `/meetings/{id}`                                   | Delete meeting and all files/vectors                                                               |
| `GET`    | `/meetings/{id}/files`                             | File list                                                                                          |
| `GET`    | `/meetings/{id}/files/{fid}`                       | Download file (`X-API-Key` or `?token=`; routed in `file_download.py`, registered before meetings) |
| `DELETE` | `/meetings/{id}/files/{fid}`                       | Delete a single file (including its vector)                                                        |
| `GET`    | `/meetings/{id}/files/{fid}/timeline`              | Timeline (keyframe/page)                                                                           |
| `PATCH`  | `/meetings/{id}/files/{fid}/semantics`             | Review/update material semantics with revision-fenced re-indexing                                  |
| `GET`    | `/meetings/{id}/files/{fid}/semantics/history`     | Read immutable material-semantic review history                                                    |
| `POST`   | `/meetings/{id}/files/{fid}/evidence-location`     | Resolve an evidence excerpt to a page/slide/timestamp location                                     |
| `GET`    | `/meetings/{id}/files/{fid}/speakers`              | Speaker list                                                                                       |
| `PUT`    | `/meetings/{id}/files/{fid}/speakers`              | Update speaker mapping                                                                             |
| `GET`    | `/meetings/{id}/files/{fid}/speakers/{code}/audio` | Speaker audio clip                                                                                 |
| `GET`    | `/meetings/{id}/summary`                           | Read a pre-generated summary and its lifecycle status                                              |
| `POST`   | `/meetings/{id}/summary`                           | LLM generates summary (long translation using map-reduce)                                          |
| `POST`   | `/meetings/{id}/summary/stream`                    | SSE streaming summary                                                                              |
| `POST`   | `/meetings/{id}/reprocess`                         | Force extraction and native re-index for every file                                                |
| `POST`   | `/meetings/{id}/files/{fid}/reprocess`             | Reprocess only a single file                                                                       |
| `POST`   | `/meetings/file-token`                             | Issue global short-term download token (`file_download`)                                           |
| `POST`   | `/meetings/{id}/files/{fid}/signed-url`            | Sign the signed URL/token of the binding file                                                      |
| `GET`    | `/meetings/assets`                                 | Meeting resource static file (`path` + optional `token`)                                           |
| `GET`    | `/meetings/{id}/transcript`                        | Full transcribed text                                                                              |
| `GET`    | `/meetings/{id}/transcript/timestamps`             | Structured transcription with timestamps                                                           |
| `GET`    | `/meetings/{id}/export`                            | Export; `format=json`, `markdown`, or `txt`                                                        |
| `GET`    | `/meetings/search/content`                         | FTS5 full-text search; query parameter `q`                                                         |
|          |                                                    |                                                                                                    |

### 2.1 Upload

```http
POST /api/v1/meetings/upload
Content-Type: multipart/form-data
X-API-Key: <key>

file=<binary>
meeting_id=42 # Optional: append to existing meeting
title=Q1 Review # Optional: the title of the new meeting
```

Response: `MeetingUploadResponse`

```json
{
  "meeting_id": 42,
  "file_id": 137,
  "file_name": "Q1-review.pdf",
  "status": "pending",
  "skipped": false // true means content_hash hits an existing file
}
```

Subsequently check the status via **WS** or polling `GET /meetings/{id}`.

### 2.2 Upload constraints

- Maximum `MAX_UPLOAD_SIZE_MB` (default 500 MB)
- Automatically sanitize file names (anti-path crossing)
- Supported extensions are generated from the canonical registry in
  `backend/src/services/files/_kinds.py`:
  - video: `mp4`, `mkv`, `avi`, `mov`, `webm`, `m4v`, `3gp`;
  - audio: `mp3`, `wav`, `aac`, `flac`, `m4a`, `ogg`, `wma`, `opus`;
  - documents/data: `pdf`, `ppt`, `pptx`, `doc`, `docx`, `xls`, `xlsx`, `csv`,
    `txt`, `md`, `markdown`, `html`, `htm`, `json`, `xml`, `rtf`;
  - images: `png`, `jpg`, `jpeg`, `bmp`, `tiff`, `tif`, `webp`, `gif`.
- Extension acceptance does not imply one universal processor: audio/video use
  AssemblyAI, plain text/data is read locally, and PDF/Office/image inputs use
  the parser cascade described in [`ingest-pipeline.md`](./ingest-pipeline.md).

### 2.3 Reprocess semantics

```http
POST /api/v1/meetings/42/reprocess
```

Traverse all files under meeting:

- enqueue one durable processing job per file;
- force extraction plus Chroma/BM25 replacement even when `content_hash` is unchanged;
- force the meeting-summary rebuild after the file jobs complete.

## 3. Chat

Route prefix: `/chat`, code: `api/routers/chat.py`.

| Method | Path | Behavior |
|---|---|---|
| `POST` | `/chat` | Synchronous Q&A (RAG + memory) |
| `POST` | `/chat/stream` | Start/attach to durable SSE chat execution |
| `GET` | `/chat/runs/{run_id}` | Read persisted run state and terminal metadata |
| `GET` | `/chat/run-lookup` | Resolve a run from its idempotent client/run key |
| `GET` | `/chat/runs/{run_id}/events` | Replay persisted run events after a sequence cursor |
| `POST` | `/chat/run-cancel` | Cancel by idempotent run key |
| `POST` | `/chat/runs/{run_id}/cancel` | Cancel by server run ID while retaining persisted history |
| `POST` | `/chat/runs/{run_id}/withdraw` | Cancel and withdraw the associated turn according to lifecycle rules |
| `POST` | `/chat/search` | Retrieve/rerank only; do not generate |

### 3.1 `POST /chat`

```json
{
  "question": "What did Alice say about the Q2 roadmap?",
  "session_id": "uuid-or-null",
  "meeting_ids": [42, 43], // Optional, only retrieve in these meetings
  "retrieval_profile": "balanced", // fast | balanced | thorough
  "memory_mode": "balanced", // off | focused | balanced | deep
  "use_web_search": false,
  "file_types": ["pdf"], // optional filtering
  "date_from": "2026-01-01T00:00:00Z",
  "date_to": null
}
```

`retrieval_profile` is request-scoped and never mutates runtime settings.
`fast` disables multi-query and reranking and caps the default Top K at 5;
`thorough` enables multi-query and raises the default Top K/rerank pool to at
least 16. `memory_mode` controls recall and extraction as one coherent mode:
`off` disables long-term recall/extraction, `focused` uses conservative
single-hop facts, and `deep` enables wider recall, multi-hop memory, and the
knowledge graph. `balanced` is the production default. Advanced clients may
still send `top_k`; when omitted, the retrieval profile owns the result budget.

Response: `ChatResponse`

```json
{
  "answer": "...",
  "sources": [
    {"meeting_id": 42, "file_id": 137, "file_name": "Q1-review.pdf", "chunk_index": 5, "score": 0.83, "snippet": "..."}
  ],
  "session_id": "uuid",
  "web_results": [],
  "trace": { "spans": [...] },
  "extraction_failed": false,
  "degraded": false,
  "degradation_reason": null,
  "skill_used": null
}
```

### 3.2 `POST /chat/stream`

Same schema, response is SSE stream. Slow generation emits a separate
`status` event; the status is never embedded in answer tokens:

```
data: {"type":"step","name":"retrieve","phase":"start"}

data: {"type":"sources","items":[...]}

data: {"type":"status","status":"degraded","reason":"fast_path_timeout"}

data: {"type":"token","content":"Alice "}
data: {"type":"token","content":"said"}
...

data: {"type":"done","elapsed_ms":1842}
```

For event types, see [`chain-pipeline.md`](./chain-pipeline.md#61-event-type-streambus--streamevent).

### 3.3 `POST /chat/search`

Only run retrieval and reranking, return the document list, and do not call the LLM. Suitable for recall debugging.

## 4. Sessions

Route prefix: `/sessions`, code: `api/routers/sessions.py`.

| Method   | Path                       | Behavior                                       |
| -------- | -------------------------- | ---------------------------------------------- |
| `GET`    | `/sessions`                | List user sessions                             |
| `GET`    | `/sessions/{id}/messages`  | All session messages                           |
| `GET`    | `/sessions/{id}/continuation-preview` | Validate and preview latest/saved-scope/saved-snapshot continuation |
| `POST`   | `/sessions/{id}/branches`  | Create a branch at a persisted message boundary |
| `DELETE` | `/sessions/{id}`           | Delete sessions (and messages, summaries)      |
| `POST`   | `/sessions/batch-delete`    | Delete up to 100 sessions in one transaction   |
| `POST`   | `/sessions/{id}/summarize` | Generate/regenerate summary                    |
| `GET`    | `/sessions/{id}/summary`   | Read summary                                   |
| `GET`    | `/sessions/{id}/cite`      | citation/summary context                       |
| `GET`    | `/sessions/summaries`      | List of all summaries (paginated)              |
| `POST`   | `/sessions/search`         | Cross-session search (FTS5 + digest semantics) |

## 5. Memory

Routing prefix: `/memory`, code: `api/routers/memory.py`. See [`memory-and-kg.md`](./memory-and-kg.md) for details.

| Method | Path | Behavior |
|---|---|---|
| `GET` | `/memory/projects` | List the project directory |
| `PUT` | `/memory/projects` | Create/update a revision-checked project definition |
| `GET` | `/memory/projects/materials` | List materials available for project assignment |
| `POST` | `/memory/review/query` | Page through Meeting Review candidates using a stable snapshot |
| `POST` | `/memory/facts/query` | Deterministic typed/bitemporal decision, task, and project-fact query |
| `POST` | `/memory/facts/changes` | Compare fact snapshots across business/system time |
| `GET` | `/memory` | List memories with library/type/lifecycle/project/paging filters |
| `POST` | `/memory` | Create a memory |
| `PUT` | `/memory` | Revision-checked edit or lifecycle transition |
| `DELETE` | `/memory` | Hard-delete one memory and queue derived-vector cleanup |
| `POST` | `/memory/resolve-conflict` | Select a revisioned winner and atomically supersede alternatives |
| `POST` | `/memory/batch` | Batch import |
| `POST` | `/memory/batch-delete` | Delete up to 100 memories atomically |
| `GET` | `/memory/export` | Cursor-paginated JSON export |
| `GET` | `/memory/versions` | Read immutable revisions for a logical memory key |
| `POST` | `/memory/search` | Semantic search |
| `POST` | `/memory/retry-index` | Retry indexing for a pending/failed memory revision |
| `POST` | `/memory/decay` | Trigger decay + merge |
| `POST` | `/memory/feedback` | Record explicit usefulness feedback for one fact |
| `GET` | `/memory/entities` | List entities |
| `POST` | `/memory/entities/batch-delete` | Delete up to 100 entities |
| `GET` | `/memory/entities/{name}` | Entity details + relationships |
| `DELETE` | `/memory/entities/{name}` | Delete an entity |
| `POST` | `/memory/entities/merge` | Merge entities |

## 6. Settings

Route prefix: `/settings`, code: `api/routers/settings/` (`__init__.py` + `_rebuild.py`).

| Method   | Path                           | Behavior                                       |
| -------- | ------------------------------ | ---------------------------------------------- |
| `GET`    | `/settings`                    | Read current settings, masked secrets, and `activation_policy` field groups |
| `PUT`    | `/settings`                    | Update settings (memory only)                  |
| `GET`    | `/settings/bindings`           | List all optional providers                    |
| `GET`    | `/settings/rebuild-status`      | Inspect the guarded vector/multimodal rebuild task state |
| `POST`   | `/settings/rebuild-vectors`    | Guarded shadow rebuild with generation verification and rollback |
| `POST`   | `/settings/rebuild-multimodal` | Multimodal index backfill (RAGAnything)        |
| `POST`   | `/settings/reload-config`      | Reload YAML configuration from disk            |
| `DELETE` | `/settings/account`            | Start GDPR erasure; returns `202` + deletion batch status |
| `GET` | `/settings/account/deletions/{batch_id}` | Inspect pending/completed/failed external cleanup |
| `POST` | `/settings/account/deletions/{batch_id}/retry` | Requeue and retry dead-letter cleanup jobs |

### 6.1 PUT semantics

- Updates are in-memory only and do not write back to `.env`.
- Each request builds and validates an unpublished candidate, atomically publishes it, resets affected services, and bumps the settings epoch.
- Running requests and durable jobs use immutable settings snapshots.
- Index-shaping and restart-required changes return `409` without applying any field. The unified error envelope uses `SETTINGS_REINDEX_REQUIRED` or `SETTINGS_RESTART_REQUIRED`, and `details` contains the complete hot/resettable/reindex/restart field classification. Apply blocked changes through controlled deployment configuration instead.
- Accepted live changes are lost when the process restarts unless they are also applied to deployment configuration.

### 6.2 `rebuild-vectors`

- **Concurrency guard**: a process lock plus SQLite advisory lock allows only one rebuild.
- Build a shadow Chroma collection while the live generation remains queryable. Fast-copy is allowed only when every source chunk has the active index-config fingerprint.
- Any file-level failure aborts before the swap. After swapping, BM25 is rebuilt strictly and per-file generation manifests are verified; failure restores the retired Chroma generation and matching BM25 index.
- The API returns immediately while the guarded task runs. This endpoint cannot reconstruct a ready file that lacks a persisted text representation; use durable file reprocessing for those files.

## 7. Skills

Route prefix: `/skills`, code: `api/routers/skills.py`.

| Method | Path | Behavior |
|---|---|---|
| `POST` | `/skills` | Register custom skill (201) |
| `GET` | `/skills` | List registered skills (name, description, examples) |
| `POST` | `/skills/invoke` | Manually call the skill (without relying on intent matching) |
| `POST` | `/skills/match` | Test intent matching (debug) |

### 7.1 Invoke request

```json
{
  "skill_name": "meeting-summary",
  "query": "Summarize last week's retro",
  "user_id": "default",
  "meeting_ids": [42]
}
```

Response:

```json
{
  "skill_name": "meeting-summary",
  "content": "# Summary\n- ...",
  "format": "markdown",
  "sources": [...],
  "execution_time_ms": 0
}
```

## 8. Health & System

| Method | Path | Behavior |
|---|---|---|
| `GET` | `/health` | DB connectivity + lightweight checks |
| `GET` | `/health/live` | Liveness probe |
| `GET` | `/health/ready` | Five local readiness checks: startup, DB, FTS5, durable queue, storage |
| `GET` | `/health/jobs` | Durable queue counts, dead-letter status, and idempotency lifecycle/recovery counts |
| `GET` | `/health/capabilities` | Report configured provider-capability availability/degradation |
| `GET` | `/health/traffic` | Traffic controller status |
| `GET` | `/health/index-consistency` | Index consistency check |
| `POST` | `/health/reset-memory-cb` | Reset memory vector circuit breaker (operation and maintenance) |
| `POST` | `/ws/token` | Issue a short-lived WebSocket authentication token |
| `GET` | `/metrics` | Prometheus metrics (**not** under `/api/v1`; requires API Key) |
| `WS` | `/ws` | Real-time events (progress, complete) |

### 8.1 WebSocket events

`WebSocketManager` current payload shape (no `file_id` / `stage` / `ratio` fields):

```json
{"type":"progress","meeting_id":42,"status":"processing","progress":0.6,"message":"..."}
{"type":"complete","meeting_id":42,"status":"ready","title":"Q1 Review"}
```

On failed completion the `status` is `failed`. Processor pipelines may extend fields; see `services/websocket.py`.

How to subscribe:

```javascript
// First obtain POST /api/v1/ws/token with X-API-Key.
const ws = new WebSocket(`ws://host:7008/api/v1/ws?token=${encodeURIComponent(token)}`);
ws.onmessage = (ev) => {
  const msg = JSON.parse(ev.data);
  // ...
};
```

Before opening the socket, request `POST /api/v1/ws/token` with the API key and
pass the returned short-lived token as `?token=...`. See
[`security-and-tenancy.md`](./security-and-tenancy.md#42-websocket-tokens) for
the principal-binding caveat that must be tested during deployment acceptance.

## 9. Error response

All error responses uniformly follow the `ErrorResponse` envelope (defined in `src/models/schemas/_common.py`) and are declared on the OpenAPI schema in each router through the `responses=` parameter of `register_routers`:

```json
{
  "code": "HTTP_404",
  "message": "Meeting not found",
  "request_id": "8a1bdfec82444a21",
  "details": null,
  "detail": "Meeting not found"
}
```

Field semantics:

- `code`: machine-readable error code, format `HTTP_<status>` or `INTERNAL_ERROR`.
- `message`: human-readable error message (production output is redacted as needed).
- `request_id`: Request ID, consistent with the `X-Request-ID` response header/server log, used for tracking.
- `details`: Additional context, optional.
- `detail`: compatible with old fields of old clients, the same value as `message`.

Common status codes (each router declares the following codes in OpenAPI, which can be verified by schemathesis contract testing):

| Status | Semantics |
|---|---|
| 400 | Request parameter error (including multipart stream damage, etc.) |
| 401 | Missing/wrong `X-API-Key` |
| 403 | Authentication passed but insufficient permissions |
| 404 | Resource does not exist |
| 409 | Concurrency violation (for example, a vector rebuild already running) |
| 413 | File limit exceeded |
| 422 | Pydantic verification failed |
| 429 | Rate limit exceeded (slowapi; use the same `ErrorResponse` envelope and return `Retry-After`) |
| 500 | Server internal error (the message is redacted in production) |

> All 5xx responses **do not leak** internal stack information - detailed errors are only in the server log (including `request_id`).
> The `datetime` field in all responses uses ISO 8601 with time zone (`UTCDatetime` type, the naive value is automatically filled with UTC during serialization, and the output is in the form of `"2026-05-09T14:28:17Z"`).

## 10. Cross-document schema

To avoid duplicated maintenance, all response models are centralized under `src/models/schemas/` and split by domain:

- `_common.py` — `MessageResponse`, `PaginatedResponse[T]`, shared enum
- `meetings.py`
- `chat.py`
- `memory.py`
- `sessions.py`
- `settings.py`

When adding a new field, be sure to synchronously update the type definition corresponding to domain client** under the front-end `src/api/` (or regenerate `frontend/src/api/generated.d.ts` from OpenAPI through `scripts/generate-types.sh`).

## 11. Client example

### cURL

```bash
# Upload
curl -X POST http://localhost:7008/api/v1/meetings/upload \
  -H "X-API-Key: $API_KEY" \
  -F "file=@Q1-review.pdf" \
  -F "title=Q1 Review"

# Q&A
curl -X POST http://localhost:7008/api/v1/chat \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"question":"What is the Q1 highlight?","meeting_ids":[42]}'

# streaming
curl -N -X POST http://localhost:7008/api/v1/chat/stream \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"question":"..."}'
```

### Python (`httpx`)

```python
import httpx

async with httpx.AsyncClient(base_url="http://localhost:7008/api/v1",
                             headers={"X-API-Key": "..."}) as client:
    r = await client.post("/chat", json={"question": "..."})
    r.raise_for_status()
    print(r.json()["answer"])
```

### TypeScript (axios)

```typescript
import { sendChat } from "@/api/client";

const { answer, sources } = await sendChat({
  question: "What is the action item?",
  meetingIds: [42],
});
```

## 12. Version and backward compatibility

- Currently, there is only `/api/v1/`**, and no parallel version is available.
- Newly added fields are considered backward compatible
- Field removal or semantic changes require the introduction of `/api/v2/`
- Backward-incompatible front-end changes require simultaneous upgrade of `frontend/src/api/client.ts`
