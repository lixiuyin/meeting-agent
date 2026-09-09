# Data ingest pipeline (Ingest Pipeline)

> A complete description of the link from when the user clicks "Upload" to when the content is available for retrieval by RAG.
>
> Code location:
> `backend/src/api/routers/meetings/_upload.py` · `backend/src/services/processor/` (including `_pipeline.py`, `_pipeline_meeting.py`, `_pipeline_summary.py`, `_pipeline_common.py`, `_recovery.py`) ·
> `backend/src/services/parser/cascade.py` (`profile` / `_router` / `_quality` / `providers/*`) ·
> `backend/src/services/transcriber.py` · `backend/src/services/rag/_indexer.py`

## 1. A bird’s eye view of the pipeline

```
POST /api/v1/meetings/upload
      │
      ▼
 Upload router
  ├─ sanitize filename (prevent path crossing)
  ├─ Streaming to disk, check MAX_UPLOAD_BYTES (from MAX_UPLOAD_SIZE_MB)
  ├─ Calculate content_hash (sha256)
  ├─ Deduplication: If the same hash already exists → reuse directly
  ├─ Create or associate meeting + meeting_files records
  └─ persist durable `file_processing` job
      │
      ▼
 worker lease → process_meeting_file(file_id)
  ├─ fetch_metadata ← trace span
  ├─ WS progress ≈ **0.2** (start processing files)
  │
  ├─ _resolve_processor(file_type)
  │ ├─ video/audio → AVFileProcessor → transcribe() (AssemblyAI)
  │ ├─ image → ImageFileProcessor (`parse_structured` cascade + optional vision caption)
  │ ├─ document → DocumentFileProcessor → parse_structured() (cascade)
  │ └─ text → TextFileProcessor
  │
  ├─ WS progress ≈ **0.6** (text extraction completed)
  ├─ index_meeting/index_meeting_file (chunking + embed)
  │ ├─ chunk: flat / parent-child / semantic
  │ ├─ embed (traffic-controlled)
  │ └─ Chroma physical ID includes a replacement generation; stable
  │    `logical_chunk_id` remains generation-independent (see `_indexer`)
  ├─ index_meeting_pages / index_meeting_segments (structured index)
  ├─ index_file_with_raganything (conditional execution)
  │
  ├─ WS progress ≈ **0.8** / Single file completion can reach **1.0**
  ├─ Write back meeting_files status and transcript / structured artefacts
  ├─ enqueue content-addressed, meeting/file-scoped memory extraction windows
  ├─ per-file summary (MEETING_AUTO_SUMMARIZE_FILES)
  ├─ Aggregate parent meeting: _update_meeting_status_from_files()
  └─ WS complete (meeting dimension status: such as ready / failed)

> Single meeting old path `process_meeting(meeting_id)` uses a progress ratio of **0.0 → 0.2 → 0.6 → 0.8** (`processor/_pipeline.py`).
```

## 2. Upload endpoint: `POST /api/v1/meetings/upload`

### 2.1 Streaming disk writing + capacity protection

```python
# pseudocode
async with aiofiles.open(target, "wb") as fh:
    total=0
    async for chunk in file.stream():
        total += len(chunk)
        if total > settings.MAX_UPLOAD_BYTES:
            raise HTTPException(413, "File too large")
        await fh.write(chunk)
        sha.update(chunk)
```

**Do not pre-read the entire file** to prevent large files from overwhelming the memory.

### 2.2 File name cleaning

- Remove `..` / absolute path / illegal characters
- Keep the original extension (for dispatch)
- Append `_1`, `_2` … in case of conflict
- Magic byte verification (`_MAGIC_BYTES` in `_common.py`)

### 2.3 Content hashing is idempotent

- `meeting_files.content_hash` + unique index per meeting (see database migration v21)
- Duplicate uploads in the same meeting are rejected before processing
- Ordinary redelivery may skip an already-ready unchanged file; explicit reprocess and manifest-repair jobs always force native re-indexing

### 2.4 Meeting vs File relationship

- You can pass `meeting_id` to append files; otherwise, create a new meeting
- Delete a single file: `delete_meeting_chunks(meeting_id, file_id=...)` only clears the file vector

## 3. Background processing: `process_meeting_file(file_id)`

Code: `services/processor/_pipeline.py`.

### 3.1 State Machine (File vs Meeting)

**`meeting_files.status`** (most common in pipeline and recovery logic):

```
processing ──► ready
           └──► error
```

New file lines are usually inserted with **`processing`**, with **`processing_started_at`** (for the grace judgment of `recover_stale_meetings`).

**`meetings.status`** (parent aggregation, see `MeetingLifecycleStatus`): includes `uploading` / `processing` / `ready` / `failed` / `error`, etc.; when coexisting with a single file `error`, you need to understand the rules of the aggregate function `_update_meeting_status_from_files()` (subject to the source code).

### 3.2 Isolation of blocking calls

```python
await asyncio.wait_for(
    asyncio.to_thread(parser.parse, path, trace=trace),
    timeout=settings.PARSE_TIMEOUT_SECONDS,
)
```

Timeout or exception → update file to `error` and write `error_message`; session may be aggregated to `failed`.

### 3.3 WebSocket progress event

The handler calls `websocket_manager.notify_*` on nodes such as **0.2 / 0.6 / 0.8 / (single file 1.0)**. Payload example:

```json
{"type":"progress","meeting_id":42,"status":"processing","progress":0.6,"message":"Text extracted from deck.pdf"}
{"type":"complete","meeting_id":42,"status":"ready","title":"Q1 Review"}
```

Multi-file scenarios are broadcast with **`meeting_id`; `file_id` is generally not included in the payload. Connection: `WS /api/v1/ws?client_id=...` (production requires `api_key` query or dev mode).

### 3.4 Trace spans

Phase span includes: `fetch_metadata`, `parse` or `transcribe`, `index_meeting`, `db_persist`, etc.
After successful parsing, the metadata contains **`parser_used`**. Cloud results
use **`marker` / `mineru` / `paddle`** (the values in `ParserName`); the terminal
PDF-only local fallback uses **`local_pymupdf_fallback`**. Plain-text files are
handled before the provider cascade and record their own text-extraction trace.

After ingestion, the pipeline logs
**`logger.info("ingest_trace %s", json.dumps(trace.to_dict()))`**. It does not
write a separate trace table by default; benchmark capture and API propagation
have their own contracts.

## 4. Parsing pipeline: `services/parser/cascade.py`

### 4.1 Overview

The current implementation is **format dispatch + local profiling + an ordered
cloud-provider cascade + quality gates**:

1. **Format dispatch**: `.txt`, `.md`, `.markdown`, `.html`, `.htm`, `.json`,
   `.xml`, `.rtf`, and `.csv` are read locally and never enter the cloud-parser
   router.
2. **Office conversion**: `.ppt` and `.pptx` first attempt conversion to a
   temporary PDF. `.doc` and `.xls` attempt conversion to `.docx` and `.xlsx`.
   The uploaded original and its database identity remain authoritative;
   `original_format` is preserved in parser metadata for slide citations.
3. **Local profiling**: `profile_document()` uses local libraries such as
   PyMuPDF and python-pptx to estimate pages, text density, image ratio, and
   scan likelihood. Profiling chooses a route; it is not the returned parse.
4. **Routing**: `select_parsers(profile, user_hint=...)` returns only an ordered
   tuple of `marker`, `mineru`, and `paddle` providers.
5. **Cascading and quality**: providers run sequentially inside one asyncio
   event loop. Retryable provider errors are retried within the shared timeout;
   a response that fails `assess_quality()` advances to the next provider.
6. **Terminal behavior**: after every cloud provider fails, an original PDF may
   fall back to local PyMuPDF text extraction. Other routed formats raise
   `AllParsersFailedError`; there is no general local-success guarantee.

### 4.2 Provider Overview

| Name | Implementation module | Description |
|---|---|---|
| `marker` | `providers/marker_api.py` | Marker Cloud API (layout-aware parsing) |
| `mineru` | `providers/mineru_api.py` | MinerU Cloud API (OCR and complex-PDF parsing) |
| `paddle` | `providers/paddle_api.py` | Paddle layout analysis cloud API |
| `local_pymupdf_fallback` | Direct branch in `cascade.py` | PDF-only text fallback after the configured cloud candidates fail; not a `ParserName` router candidate |

> Unlike the historical native Magic-PDF/PaddleOCR path, routed documents use
> **HTTP cloud parsing**. Local profiling does not remove the need for a viable
> parser credential. Plain-text files are the exception because they are read
> locally before routing.

### 4.3 `OCR_PROVIDER` (configuration prompt)

**`OCR_PROVIDER`** in environment variables/configuration passes the `user_hint` of `_router.select_parsers`: **soft preference** — if the hint appears in the current routing sequence, it is **promoted to the head of the queue**; **does not** bypass the format rules of `DocumentProfile` (for example, independent images will not put MinerU first, because MinerU v4 extracts PDF as the main scene).

### 4.4 Route summary (`_router.py`)

- **Standalone image** (`png`, `jpg`, `jpeg`, `bmp`, `tiff`, `tif`, `webp`,
  `gif`): **`paddle` → `marker`**. MinerU is excluded because its v4 extract
  path expects PDF input.
- **PDF below `TEXT_DENSITY_LOW`**: **`mineru` → `marker` → `paddle`**.
- **Other PDF**: **`marker` → `mineru` → `paddle`**.
- **PPTX that remains PPTX after conversion attempts**: **`marker` → `paddle`
  → `mineru`**. A successfully converted presentation is profiled and routed
  as PDF instead.
- **DOCX/XLSX and other routed documents**: **`marker` → `mineru` →
  `paddle`**.

The only active router threshold is `TEXT_DENSITY_LOW = 50` characters per
page. `image_ratio` and `is_likely_scanned` remain useful profile/diagnostic
fields but do not add extra branches in the current `_router.py`.

### 4.5 Support formats and old Office

- **Upload registry**: `services/files/_kinds.py` is the source of truth for
  every accepted extension and its viewer/timeline/page capabilities.
- **Parser registry**: `parser/types.py` covers document, text, and image
  inputs. Audio/video bypass it and use `transcriber.py`.
- **`.ppt` / `.pptx`**: conversion to PDF is attempted first. `.doc` and `.xls`
  attempt conversion to `.docx` and `.xlsx`. Conversion uses the converter
  layer and may depend on LibreOffice; a failed conversion only means the
  original format is offered to the cloud cascade, not that it can be parsed
  locally.

| Family | Accepted extensions | Processing path |
|---|---|---|
| Video | `mp4`, `mkv`, `avi`, `mov`, `webm`, `m4v`, `3gp` | ffmpeg audio extraction → AssemblyAI |
| Audio | `mp3`, `wav`, `aac`, `flac`, `m4a`, `ogg`, `wma`, `opus` | AssemblyAI |
| PDF | `pdf` | profile → cloud cascade → PDF-only PyMuPDF fallback |
| Slides | `ppt`, `pptx` | PDF conversion attempt → cloud cascade |
| Documents | `doc`, `docx` | legacy conversion where needed → cloud cascade |
| Spreadsheets | `xls`, `xlsx` | legacy conversion where needed → cloud cascade |
| Text/data | `csv`, `txt`, `md`, `markdown`, `html`, `htm`, `json`, `xml`, `rtf` | local text extraction |
| Images | `png`, `jpg`, `jpeg`, `bmp`, `tiff`, `tif`, `webp`, `gif` | cloud cascade; optional vision enrichment in the image processor |

### 4.6 Page limit

`_estimate_page_count()` is estimated before cascading; exceeding **`MAX_PARSE_PAGES`** (default 1000) throws `ValueError` to avoid sky-high parsing.

### 4.7 Timeout and Threads

- Effective file budget: start with **`PARSE_TIMEOUT_SECONDS`** (300 s), add
  `PARSE_TIMEOUT_PER_MB_SECONDS` (2 s/MiB), and cap at
  `PARSE_TIMEOUT_MAX_SECONDS` (900 s). The cascade consumes that budget using
  a monotonic deadline.
- When called from **existing event loop**, cascade runs the complete asynchronous cascade in the child thread through **thread pool + `asyncio.run`** to avoid blocking FastAPI (see `cascade._dispatch_cascade`).

## 5. Transcription: `services/transcriber.py`

### 5.1 Provider

| `ASR_PROVIDER` | Description |
|---|---|
| `assemblyai` | **only** supported cloud ASR; speaker/timestamp/multilingual (`services/transcriber.py`) |

Local Whisper etc. have been removed to keep the image lean. The video is first extracted to WAV using **ffmpeg** and then uploaded.

### 5.2 Output and API

`TranscriptResult`: `text`, `segments`, `language`, `duration_seconds`; Timeline API consumes `segments`.

## 6. Index: `services/rag/_indexer.py`

### 6.1 Chunking

Flat / Parent-Child / Semantic — See [`rag.md`](./rag.md#3-indexing-process-chunking) for details.

### 6.2 Deterministic chunk ID

Physical IDs include a unique replacement generation, while metadata carries a generation-independent `logical_chunk_id`. This lets a new Chroma/BM25 generation be written and verified before old physical IDs are pruned, without breaking cross-retriever deduplication.

### 6.3 Chroma metadata (example)

`meeting_id`, `file_id`, `chunk_index`, `file_type`, `file_name`, `page`, timestamp field, `date`, etc., for retrieval `where` filter.

### 6.4 Index reconciliation

Startup/periodic reconciliation reads the actual Chroma and BM25 metadata for every ready file. A file is ready only when both stores expose one matching generation under the active index-config fingerprint; the compact manifest is persisted in `index_state`. Missing, mixed, or stale generations set `repair_pending=1` and enqueue idempotent durable file reprocessing. RAGAnything state is tracked separately and every multimodal read is checked against authoritative database ownership.

### 6.5 Automatically trigger meeting summary `_pipeline_summary.py`

After the processing is completed, the meeting summary generation is automatically triggered. When `MEETING_AUTO_SUMMARIZE_FILES=True`, a summary is generated for each file before an overall summary at the meeting level is generated.

## 7. Rerun: `POST /meetings/{id}/reprocess`

The explicit endpoint forces extraction and native re-indexing even when `content_hash` is unchanged. Manifest-incompatible files use the same durable path with `force_native_reindex=true`, so reconciliation cannot be defeated by the ordinary unchanged-file optimization. Embedding/parser/chunk-shape deployment changes rely on this repair path after controlled restart.

## 8. Boundaries and fault tolerance

| Situation                                      | Behavior                                                                      |
| ---------------------------------------------- | ----------------------------------------------------------------------------- |
| Exceeds `MAX_UPLOAD_BYTES`                     | 413, the written part should be cleared (see upload routing implementation)   |
| path crossing / illegal name                   | 400 + sanitize                                                                |
| One cloud parser fails or fails quality        | Retry when eligible, then continue to the next routed provider                 |
| Every cloud parser fails for an original PDF   | Try text-only `local_pymupdf_fallback`; fail if it yields no text               |
| Every cloud parser fails for another routed format | Raise `AllParsersFailedError`; persist `error` + `error_message` and aggregate the meeting state |
| Process crashes midway                         | `recover_stale_meetings` after restart (5 minutes grace)                      |
| Embedding rate limiting                        | `traffic_control` queuing/circuit breaking                                    |

## 9. Performance and cost recommendations

- **Video**: Convert WAV first and then send it to AssemblyAI to save bandwidth.
- **Large PDF**: Lower `MAX_PARSE_PAGES` or split upload.
- **Cloud parsing**: configure at least one viable provider for routed
  documents; monitor `parser_used`, `fallback_from`, quality-gate failures, and
  provider quotas. Do not rely on the PDF-only fallback for layout fidelity.
- **Concurrency**: embedding is subject to `LLM_MAX_CONCURRENCY`; upload work
  uses `DURABLE_JOB_WORKERS` and survives API restarts.
