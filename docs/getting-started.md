# Getting started

This guide takes a new operator from an empty checkout to a cited answer.
For the complete configuration surface, see
[`backend/docs/configuration.md`](../backend/docs/configuration.md).

## Prerequisites

Choose one setup:

| Setup | Required | Best for |
|---|---|---|
| Docker Compose | Docker with Compose v2 | A reproducible local or single-node deployment |
| Manual | Python 3.12+, `uv`, Node 22–24, npm | Backend/frontend development |

At least one chat LLM provider is required. Audio/video ingestion additionally
needs `ASSEMBLYAI_API_KEY`. Parser, embedding, vision, reranker, and web-search
providers are optional and depend on the files and features you use.

## Docker setup

```bash
cp backend/.env.example backend/.env
# Edit backend/.env and set LLM_API_KEY.
# Set ASSEMBLYAI_API_KEY if you will upload audio or video.
docker compose up --build
```

The default host ports are:

| Service | URL |
|---|---|
| Frontend (intended host port) | <http://localhost:8307> |
| API | <http://localhost:7008> |
| Swagger UI | <http://localhost:7008/docs> |
| OpenAPI JSON | <http://localhost:7008/openapi.json> |

Compose maps host port `8307` to the Nginx container's port `8080`.

The supported frontend development/CI engines are Node 22–24. The current
`frontend/Dockerfile` build stage is still pinned to Node 20 while the final
runtime is Nginx; this is a known build-path mismatch, not an expansion of the
supported Node range. Keep the 22/24 CI jobs authoritative until the builder is
aligned.

Check readiness before using the API:

```bash
curl --fail http://localhost:7008/api/v1/health/ready
```

If the host ports are already occupied, change the published ports in
`docker-compose.yaml`; the container-side backend port remains `8000`.

## Manual setup

Start the backend and frontend in separate terminals:

```bash
cd backend
uv sync --dev
cp .env.example .env
# Edit .env, then:
uv run python -m uvicorn src.main:app --reload --port 7008
```

```bash
cd frontend
npm install
npm run dev
```

The manual frontend is at <http://localhost:8307> and proxies `/api`
to the backend at `http://localhost:7008`. Project shortcuts are available
from the repository root:

```bash
make dev       # backend and frontend
make dev-be    # backend only
make dev-fe    # frontend only
make lint
make test
```

## First workflow

The same flow works in the UI and through the API:

1. Upload a meeting recording or supported document.
2. Wait until the meeting/file status becomes `ready`.
3. Open the meeting to inspect the transcript, pages, slides, or timestamps.
4. Ask a question with an optional meeting/file scope.
5. Open each citation to verify the source location.
6. Optionally generate a meeting summary, rename diarized speakers, or export the meeting.
7. Open **Memory** to manage projects and libraries, query decisions/tasks,
   compare state changes, review evidence-backed candidates, inspect entities,
   and browse past-session summaries.

The upload request commits a `file_processing` durable job and returns before
parsing, transcription, vision, chunking, and indexing finish. A successful
HTTP response therefore means “accepted and queued”, not “ready for retrieval”.

The desktop Memory workspace keeps its library selector, filters, and actions
visible while the record list scrolls inside the card. Long values and evidence
remain contained in that list; if the browser itself gains an unexpected
vertical scrollbar or text crosses the rounded card boundary, treat it as a
layout regression and capture the viewport size with the report.

### API smoke test

Set a convenient base URL. In development, an empty `API_KEY` means the API
key header is not required. In staging/production, add
`-H "X-API-Key: $MEETING_AGENT_API_KEY"` to each request:

```bash
export API_BASE=http://localhost:7008/api/v1
```

Create a meeting and upload a file:

```bash
curl -sS -X POST "$API_BASE/meetings" \
  -H 'Content-Type: application/json' \
  -d '{"title":"Demo meeting","description":"Local smoke test"}'

curl -sS -X POST "$API_BASE/meetings/upload" \
  -F 'meeting_id=<meeting_id>' \
  -F 'file=@/path/to/meeting.pdf'
```

The upload response contains `meeting_id` and `file_id`. Poll the meeting:

```bash
curl -sS "$API_BASE/meetings/<meeting_id>"
```

When the response reports `status: "ready"`, ask a question:

```bash
curl -sS -X POST "$API_BASE/chat" \
  -H 'Content-Type: application/json' \
  -d '{"question":"What were the main decisions?","meeting_ids":[<meeting_id>]}'
```

For complete request fields and response schemas, use
[`api-quickstart.md`](api-quickstart.md) and the generated Swagger UI.

## Supported input and provider choices

The ingest pipeline checks the filename against the canonical registry and
validates known binary signatures before writing the complete upload. The
current accepted formats are:

| Family | Extensions |
|---|---|
| Video | `mp4`, `mkv`, `avi`, `mov`, `webm`, `m4v`, `3gp` |
| Audio | `mp3`, `wav`, `aac`, `flac`, `m4a`, `ogg`, `wma`, `opus` |
| Documents/data | `pdf`, `ppt`, `pptx`, `doc`, `docx`, `xls`, `xlsx`, `csv`, `txt`, `md`, `markdown`, `html`, `htm`, `json`, `xml`, `rtf` |
| Images | `png`, `jpg`, `jpeg`, `bmp`, `tiff`, `tif`, `webp`, `gif` |

A renamed or malformed binary file is rejected before indexing. Text-like
formats without a fixed magic signature still pass through extension, size,
filename, ownership, and downstream parser validation.

The default path is intentionally layered:

```text
file → magic-byte validation → content profile → parser/ASR/vision
     → normalized transcript/pages/segments → chunks → Chroma + FTS5/BM25
```

Cloud parsers are selected through content-aware routing and quality gates.
Only plain-text/data inputs are unconditionally local; the terminal local
parser fallback is PDF-only and text-only. Read
[`backend/docs/ingest-pipeline.md`](../backend/docs/ingest-pipeline.md) before
choosing parser credentials for production workloads.

## Common first-run failures

| Symptom | Likely cause | Check |
|---|---|---|
| `401` | `API_KEY` is configured but the header is missing or wrong | `X-API-Key` and `PRINCIPAL_PEPPER` in `.env` |
| Upload accepted but never ready | Parser/ASR provider timeout or durable `file_processing` job failure | `GET /api/v1/meetings/{id}`, durable-job/dead-letter logs, and [`ingest-pipeline.md`](../backend/docs/ingest-pipeline.md) |
| `429` during chat | Provider or application traffic limits | [`llm-and-traffic.md`](../backend/docs/llm-and-traffic.md) and the 429 runbook |
| Vector dimension error | Embedding model/dimension changed without a rebuild | `POST /api/v1/settings/rebuild-vectors` |
| UI is blank but API works | Frontend proxy or `VITE_API_BASE_URL` mismatch | Browser devtools and `frontend/.env.example` |
| No citations | No file is ready, scope excludes the file, or retrieval threshold is too strict | `/health/index-consistency`, `/chat/search`, and [`rag.md`](../backend/docs/rag.md) |

Do not repeatedly retry a failed upload without checking the stored status:
the upload path is idempotent by content hash within a meeting, and mutating
requests can also use `Idempotency-Key`.
