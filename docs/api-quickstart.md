# API quickstart

The FastAPI application exposes versioned routes under `/api/v1`. The live
contract is always available at `/docs` and `/openapi.json`; this page focuses
on integration patterns that are easy to get wrong.

## Base URL and authentication

```bash
export API_BASE=http://localhost:7008/api/v1
export API_KEY_HEADER='X-API-Key: your-key'
```

When `API_KEY` is empty, the service is in development mode and accepts
requests without this header. Staging/production requires the header. Do not
put the API key in a WebSocket URL; obtain a short-lived WebSocket token
instead.

Every response can include a `request_id`. Preserve it in client logs when
reporting an error. HTTP errors use an envelope shaped like:

```json
{
  "code": "HTTP_400",
  "message": "human-readable explanation",
  "request_id": "request-id",
  "details": null,
  "detail": "backward-compatible copy of message"
}
```

## Core REST flow

### Create, upload, and poll

```bash
curl -sS -X POST "$API_BASE/meetings" \
  -H "$API_KEY_HEADER" -H 'Content-Type: application/json' \
  -d '{"title":"Quarterly planning","description":"Reference material"}'

curl -sS -X POST "$API_BASE/meetings/upload" \
  -H "$API_KEY_HEADER" \
  -H 'Idempotency-Key: upload-quarterly-planning-v1' \
  -F 'meeting_id=<meeting_id>' \
  -F 'file=@slides.pptx'

curl -sS "$API_BASE/meetings/<meeting_id>" -H "$API_KEY_HEADER"
```

Alternatively omit `meeting_id` and provide `title` to create a meeting as
part of the upload. Upload processing is asynchronous. Poll until the meeting
and file are `ready`; `failed` responses include an error message suitable for
operator triage.

### Ask with scope and continuity

```bash
curl -sS -X POST "$API_BASE/chat" \
  -H "$API_KEY_HEADER" -H 'Content-Type: application/json' \
  -d '{
    "question":"Which risks were assigned to the platform team?",
    "meeting_ids":[<meeting_id>],
    "session_id":"<optional-existing-session>",
    "top_k":8,
    "rag_mode":"auto"
  }'
```

The response returns `answer`, a single numbered `sources` list, and the
`session_id` to reuse for the next turn. A source can identify a page, slide,
timestamp, speaker, table, image, file summary, or meeting summary. Treat the
answer as grounded only to the extent that its returned sources support it.

Useful scope fields are:

- `meeting_ids`: restrict retrieval to one or more meetings.
- `file_ids`: restrict retrieval to files within those meetings.
- `file_types`, `date_from`, and `date_to`: apply additional filters.
- `use_web_search`: add web results when a configured search provider is enabled.
- `rag_mode`: choose `vector`, `hybrid`, `multimodal`, `hybrid_multimodal`, or `auto`; legacy `native` is accepted as a deprecated alias for `vector`.

### Search without generation

Use retrieval-only search to debug scope and recall before spending an LLM
call:

```bash
curl -sS -X POST "$API_BASE/chat/search" \
  -H "$API_KEY_HEADER" -H 'Content-Type: application/json' \
  -d '{"question":"platform risks","meeting_ids":[<meeting_id>]}'
```

## Streaming and notifications

### Chat SSE

```bash
curl -N -sS -X POST "$API_BASE/chat/stream" \
  -H "$API_KEY_HEADER" -H 'Content-Type: application/json' \
  -H 'Accept: text/event-stream' \
  -d '{"question":"Summarize the decision","meeting_ids":[<meeting_id>]}'
```

The stream emits JSON events. Clients should handle at least:

| Event | Meaning |
|---|---|
| `step` | Pipeline stage started or completed |
| `token` | Incremental answer text |
| `sources` | Final citation metadata |
| `trace` | Timing and pipeline diagnostics |
| `web_results` | Optional external search results |
| `error` | Structured failure; inspect `code` and `detail` |
| `done` | Stream completed and provides `session_id` |
| `heartbeat` | Keep-alive while work continues |

Do not assume the last token event is the final protocol event; wait for
`done`, and handle `error` as a terminal event.

### WebSocket progress

Request a five-minute token in production:

```bash
curl -sS -X POST "$API_BASE/ws/token" -H "$API_KEY_HEADER"
```

Connect with a unique client ID and the returned token:

```text
wss://host/api/v1/ws?client_id=my-client&token=<short-lived-token>
```

The server sends `progress`, `complete`, `error`, and `catch_up` messages. It
also sends an idle `ping`; clients must answer with `pong`. A client may send
`ping` and receives `pong`. Connections are bounded by an idle timeout and a
one-hour maximum lifetime.

## Summaries, transcripts, and export

```bash
curl -sS -X POST "$API_BASE/meetings/<meeting_id>/summary" \
  -H "$API_KEY_HEADER"

curl -sS "$API_BASE/meetings/<meeting_id>/transcript?format=markdown" \
  -H "$API_KEY_HEADER"

curl -sS "$API_BASE/meetings/<meeting_id>/export?format=markdown" \
  -H "$API_KEY_HEADER" -o meeting.md
```

Summaries require a ready meeting with transcript content. Export formats and
file asset URL rules are documented in [`backend/docs/api-reference.md`](../backend/docs/api-reference.md).

## Idempotency and retries

For endpoints that accept `Idempotency-Key` (the upload endpoint is the main
documented example), send a stable key when the client may retry after a
network timeout. The key is bound to the method, path, authenticated principal,
and request body. Do not reuse a key for a different operation or payload.
Uploads additionally deduplicate identical content within the same meeting by
SHA-256 hash.

Retry guidance:

| Response | Client behavior |
|---|---|
| `400`/`401`/`403`/`404` | Fix request, credentials, or ownership; do not blind retry |
| `408`/`429`/provider timeout | Retry with backoff; preserve the idempotency key for mutating calls |
| `409` | Inspect the resource/task state before retrying |
| `500` | Retry cautiously, log `request_id`, and check health/logs |

## MCP

Start the stdio server from `backend/`:

```bash
uv run python -m src.mcp
```

For optional HTTP transport:

```bash
MCP_TRANSPORT=streamable-http MCP_HTTP_PORT=9000 \
  MCP_API_KEY=replace-me uv run python -m src.mcp
```

The six tools are `list_meetings`, `search_meetings`,
`ask_about_meetings`, `manage_memory`, `list_skills`, and `invoke_skill`.
Use stdio for a local trusted client. HTTP binds only to loopback; use an
authenticated same-host reverse proxy for remote access. See
[`backend/docs/mcp-server.md`](../backend/docs/mcp-server.md)
for tool schemas and extension rules.

## Complete endpoint reference

The static endpoint inventory and schemas are in
[`backend/docs/api-reference.md`](../backend/docs/api-reference.md). For a
running instance, prefer the generated OpenAPI schema because it reflects the
code currently deployed:

```bash
curl -sS "http://localhost:7008/openapi.json" | jq '.paths | keys'
```
