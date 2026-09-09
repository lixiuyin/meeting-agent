# Non-text file chunk-routing switch

> **Maintained implementation note (verified 2026-09-09).** This document
> explains the trade-offs behind `rag.non_text_chunking_strategy`. The active
> configuration contract is documented in
> [`backend/docs/configuration.md`](../../../../docs/configuration.md), and the
> end-to-end ingestion path in
> [`backend/docs/ingest-pipeline.md`](../../../../docs/ingest-pipeline.md).

---

## 1. What is the switch?

### Setting items

| Field | Value | Default | Meaning |
|---|---|---|---|
| `rag.non_text_chunking_strategy` | `"native"` \| `"text"` | `"native"` | Chunk path selection when ingesting non-text files |

### Value semantics

- `native` (default, equivalent to the behavior before the change): each mode follows its own dedicated chunk strategy
  - audio/video → `index_meeting_segments()` (segment-aware, including semantic boundaries + speaker alignment + embedding multiplexing)
  - pdf/ppt/doc/xls/csv → `index_meeting_pages()` (page-aware, one block per page + table/picture independent block)
  - image → `index_meeting_segments()` (caption+OCR single segment)
  - txt/md → `index_meeting()` (plain text flat / parent-child)
- `text`: `artefact.text` of all non-text files takes the plain text chunk entry (`index_meeting()`), and
  txt/md follows the same pipeline. txt/md itself is not affected.

### Trigger process (protection mechanism)

This switch is index-affecting, so `PUT /api/v1/settings` rejects a live change with
`SETTINGS_REINDEX_REQUIRED`. Edit `config/main.yaml`, restart the service, and run
the vector rebuild workflow. The old and new policies use different chunk IDs,
metadata, and granularity, so they must never coexist in one committed generation.

---

## 2. Implementation map

The implementation spans switch configuration, route selection, signal
preservation, guarded rebuilds, frontend controls, and regression tests.

### 2.1 Switch infrastructure

| Documentation | Changes |
|---|---|
| [backend/config/main.yaml](../../../../config/main.yaml) | Added `non_text_chunking_strategy: native` default value under `rag` section |
| [backend/src/core/config.py](../../../core/config.py) | Added `NON_TEXT_CHUNKING_STRATEGY: str` settings field (from YAML) |
| [backend/src/models/schemas/settings.py](../../../models/schemas/settings.py) | `RAGSettings` adds a new field with the same name; `@field_validator` verification value can only be `{native, text}`, automatically lowercase |
| [backend/src/api/routers/settings/](../../../api/routers/settings/) | The centralized activation policy classifies this key as `reindex_required`; live changes are rejected atomically and GET exposes the policy. |

### 2.2 Routing implementation (core)

| Documentation | Changes |
|---|---|
| [backend/src/services/processor/_pipeline.py](../_pipeline.py) | Added three auxiliary functions: `_should_route_artefact_to_text_chunking(artefact)`, `_format_timestamp_label(seconds)`, `_build_text_route_payload(artefact)`; the text-route branch is in the original `index_meeting_*` adds a short circuit in front of the three choices; when hitting, first `delete_meeting_chunks(meeting_id, file_id=...)` and then `index_meeting()`; metadata is injected into `chunk_strategy_route ∈ {native, text}` for observability |

`_pipeline.py` The actual distribution logic of the routing layer:

```
if NON_TEXT_CHUNKING_STRATEGY == "text" and (segments is not empty or parsed_doc is not empty):
    route_text = _build_text_route_payload(artefact)
    delete_meeting_chunks(meeting_id, file_id=...)
    index_meeting(text=route_text) #Text link (flat / parent-child)
elif segments are not empty:
    index_meeting_segments(...) # native link: audio and video / picture
elif parsed_doc is not empty:
    index_meeting_pages(...) # native link: documentation
else:
    index_meeting(text=artefact.text) #Plain text (txt/md), the original behavior remains unchanged
```

### 2.3 Signal enhancement (so that text routing does not lose key information)

| Documentation | Changes |
|---|---|
| [backend/src/services/parser/types.py](../../parser/types.py) | `ParsedDocument` adds `to_indexable_text()`: retain page text + table markdown + image caption/OCR (coexisting with the original `to_text()`, leaving the old page-aware mainline unchanged) |
| [backend/src/services/processor/_pipeline.py](../_pipeline.py) | `_build_text_route_payload()` injects `[hh:mm:ss] speaker:` prefix into audio/video; adjusts `to_indexable_text()` into document; directly uses `artefact.text` (caption+OCR) for a single image segment |

### 2.4 rebuild fix (bug not directly related to switch, but brought out together)

| Documentation | Changes |
|---|---|
| [backend/src/api/routers/settings/_rebuild.py](../../../api/routers/settings/_rebuild.py) | `_rebuild_vectors_task` now queries `meeting_files` at file granularity. A `JOIN meetings` supplies the title/date, and each file independently runs `delete_meeting_chunks(meeting_id, file_id)` followed by `index_meeting(...)`. This fixes the previous bug that merged all files in a meeting into one indexing operation. |

### 2.5 Front-end

| Documentation | Changes |
|---|---|
| [frontend/src/api/client-settings.ts](../../../../../frontend/src/api/client-settings.ts) | `RAGSettings` interface adds `non_text_chunking_strategy: "native" \| "text"` |
| [frontend/src/views/settings/RagTab.tsx](../../../../../frontend/src/views/settings/RagTab.tsx) | RAG settings page with drop-down selection + helper copywriting |
| [frontend/src/i18n/messages.ts](../../../../../frontend/src/i18n/messages.ts) | Chinese-English bilingual key: `settings.rag.nonTextChunkingStrategy*` |

### 2.6 Test

| Documentation | Changes |
|---|---|
| [backend/tests/ingestion/test_ingest_trace.py](../../../../tests/ingestion/test_ingest_trace.py) | Adds `test_process_meeting_file_routes_audio_artefact_text_through_text_chunking` and `test_process_meeting_file_routes_document_artefact_text_through_text_chunking`. With the switch enabled, audio/document content uses `index_meeting()` and `chunk_strategy_route="text"`; audio text retains the `[mm:ss]` timestamp prefix. |
| [backend/tests/config/test_settings_rebuild_check.py](../../../../tests/config/test_settings_rebuild_check.py) | Adds `test_non_text_chunking_strategy_change` to verify that changing this setting triggers the rebuild requirement. |

---

## 3. How to switch between different Chunk strategies

RAG has a total of **three independent** chunk dimensions, arranged from large to small according to "scope of influence":

### Dimension 1: Overall routing of non-text files (outermost layer)

Set `rag.non_text_chunking_strategy` in `backend/config/main.yaml` (or its
documented environment override), restart the backend, then run the appropriate
rebuild or reprocess workflow. `PUT /api/v1/settings` intentionally rejects this
index-shaping field with `SETTINGS_REINDEX_REQUIRED`; it is not a live toggle.

- `native` ↔ `text`, affects audio/video/pdf/ppt/doc/image.
- Cutting only takes effect on **newly uploaded** or **actively reprocessed** files.

### Dimension 2: Flat ↔ Parent-Child under plain text path

```http
PUT /api/v1/settings
{
  "rag": {
    "parent_child_enabled": true,
    "child_chunk_size": 500,
    "child_chunk_overlap": 50
  }
}
```

- Affects the internal behavior of `index_meeting()`.
- After dimension 1 is switched to `text`, all non-text files will also use this switch.
- These index-shaping fields also require the controlled restart/reindex workflow.

### Dimension 3: Structure-aware pre-cutting in Flat mode

```http
PUT /api/v1/settings
{
  "rag": { "semantic_chunking_enabled": true }
}
```

- Only works on flat paths (when `parent_child_enabled=false`).
- Use `_split_by_structure()` to roughly chop Markdown titles, `Speaker N` tags, numbered lists, etc., and then press `chunk_size` to finely chop.

### Apply new policies to old data

| Desired results | Recommended actions | Remarks |
|---|---|---|
| Make old files re-chun to new chunk_size / parent_child / `non_text_chunking_strategy=text` | `POST /api/v1/settings/rebuild-vectors` | Fixed to re-chun at file granularity from `meeting_files.transcript`. **No re-transcription/parsing**, so the audio timestamp prefix and document table markdown signals that can only be obtained in the ingest stage will not come back |
| Let the old files return to native (restore segment-aware / page-aware) | `POST /api/v1/meetings/{meeting_id}/reprocess` or single file `.../files/{file_id}/reprocess` | Completely rerun the ingest pipeline, ASR / parser will be re-adjusted, **there is an external API cost** |

---

## 4. Old route vs new strategy: signal preservation comparison

The following table compares `non_text_chunking_strategy=native` (old route, old behavior) for each file type.
**Signal difference** with `non_text_chunking_strategy=text` (new strategy, unified use of text chunks).

### 4.1 Audio/Video (AVFileProcessor)

| project | native (segment-aware) | text (unified text chunk) |
|---|---|---|
| Chunk entry | `index_meeting_segments()` | `index_meeting()` |
| Chunk segmentation logic | Semantic boundary detection based on adjacent segment embedding cosine similarity + character upper limit | Character-based recursive segmentation, can be superimposed `parent_child_enabled` |
| Speaker alignment | ✅ Each chunk has `speaker` / `speakers_in_chunk` metadata, inherited across segments | ⚠️ There is only the `Speaker:` line prefix in the text, and there is no structured speaker field in the metadata |
| Timestamp | ✅ Each chunk has `timestamp_start` / `timestamp_end` / `time_position_ratio` metadata | ⚠️ There is only `[mm:ss]` prefix in the text (newly added `_build_text_route_payload()` injection this time), there is no time field in the metadata |
| Embedding cost | ✅ chunk vector = segment vector average, reuse segment embedding, save one embedding API | ❌ Re-adjust embedding API for each chunk |
| Temporal Filter ("Start of meeting", "First 30 minutes", etc.) | ✅ Hits `time_position_ratio` hard filter | ❌ Invalid (no metadata time field); but `[mm:ss]` prefix in text can still be recalled by BM25/vector |
| Speaker Filter ("What Alice said") | ✅ Hit `speaker` hard filtering | ⚠️ Degenerate into content matching ("Alice:" substring) |
| Audio Chunking special settings (`AUDIO_SEMANTIC_BOUNDARY_*`, `AUDIO_SPLIT_ON_SPEAKER_CHANGE`) | ✅ All valid | ❌ All invalid (not using segment path) |
| Affected by `parent_child_enabled` | ❌ Not affected by | ✅ Affected by |
| Affected by `semantic_chunking_enabled` | ❌ Not affected (segment-aware has its own semantic boundaries) | ✅ Affected by |

**Signal Loss Assessment**: Moderate. Speaker/timestamp information is preserved in the text, but hard filtering of the metadata dimension
(temporal filter, filter by speaker) will be invalid, which is equivalent to relying on the text itself to be recalled.

### 4.2 PDF/PPT/DOC/XLS/CSV (DocumentFileProcessor)

| project | native (page-aware) | text (unified text chunk) |
|---|---|---|
| Chunk entry | `index_meeting_pages()` | `index_meeting()` |
| Segmentation granularity | Each page ≤ `CHUNK_SIZE` will be a whole page, otherwise it will be cut recursively | The full text will be cut recursively by characters, **Ignore page boundaries** |
| Page number | ✅ `page_number` metadata | ❌ None |
| Heading path | ✅ `heading_path` metadata | ❌ None |
| Table | ✅ Table markdown is separated into independent chunks, `content_type="table"` | ⚠️ Table markdown is integrated into the text (newly added `to_indexable_text()` this time), and may be cut apart, **there is no independent table chunk** |
| Image caption | ✅ Independent chunk, `content_type="image_caption"` or `image_combined` | ⚠️ spelled into the text (same as above) |
| Image OCR | ✅ Independent chunk, `content_type="image_ocr"` or `image_combined`, filtered by `RAG_IMAGE_OCR_MIN_LENGTH` | ⚠️ spelled into the text, **not filtered by the OCR length threshold** (short noise OCR will also enter) |
| Image asset path | ✅ `image_storage_path` / `image_thumbnail_path` metadata, the front end can directly render thumbnails | ❌ None (asset path is lost) |
| Reranker Content-Type Bias (query contains "table"/"image") | ✅ Bonus points for hitting `content_type` | ❌ Invalid |
| `RAG_INDEX_TABLES` / `RAG_INDEX_IMAGE_CAPTIONS` settings | ✅ Control whether to form independent blocks | ❌ Invalid (unify into the main text) |
| Affected by `parent_child_enabled` | ❌ Not affected (page-aware uses its own splitter) | ✅ Affected by |

**Signal Loss Assessment**: Large. Text content (including table markdown, caption, OCR) is added in the
Basically nothing is lost with the help of `to_indexable_text()`, but **structured metadata (page number, content_type, image path)
All are lost**, causing the front-end "jump to which page", "display image thumbnail", and reranker's table/image bias to be invalid.

### 4.3 Image (`ImageFileProcessor`)

| project | native (segment-aware, single segment) | text (unified text chunk) |
|---|---|---|
| Chunk entry | `index_meeting_segments()` (caption+OCR as a single segment) | `index_meeting()` |
| Split | A single segment is directly divided into blocks | When the content exceeds `CHUNK_SIZE`, it is cut by characters |
| metadata `speaker="image"` | ✅ Yes | ❌ None (`_build_text_route_payload` explicitly skips the `image` speaker prefix) |
| Content | caption and OCR are spliced using `\n\n` | Same as native (fallback to `artefact.text`) |

**Signal Loss Assessment**: Very small. The content is exactly the same, mainly the `speaker="image"` tag on the metadata is missing.

### 4.4 Txt/Md(TextFileProcessor)

Completely unaffected by `non_text_chunking_strategy`. Use `index_meeting()` in both modes.

---

## 5. Switch semantic triple questions

### 5.1 After selecting `text`, will all files use text chunk? How to choose a different text chunk strategy at this time?

**Yes, all files eventually go to `index_meeting()`. ** Detailed distribution (from
[_pipeline.py](../_pipeline.py)):

| File type | Actual path under `non_text_chunking_strategy=text` |
|---|---|
| audio / video | `_build_text_route_payload()` inject timestamp → `index_meeting()` |
| pdf / ppt / doc / xls / csv | `to_indexable_text()` spell the table + caption → `index_meeting()` |
| image | `artefact.text` (caption+OCR) → `index_meeting()` |
| txt / md | `artefact.text` → `index_meeting()` (originally this one) |

After entering `index_meeting()` ([_indexer.py:78-88](../../rag/_indexer.py#L78)) there are two independent switches
Decide on segmentation strategy:

```python
if PARENT_CHILD_ENABLED:
    _index_parent_child(...)  # Parent and child levels
else:
    _index_flat(...)  # Look inside SEMANTIC_CHUNKING_ENABLED
```

So the "text chunk sub-strategy" you can choose under text routing is actually **3 combinations**:

| Combinations | `parent_child_enabled` | `semantic_chunking_enabled` | Behavior |
|---|---|---|---|
| Flat (default) | false | false | Pure character recursive cutting, according to `chunk_size`/`chunk_overlap` |
| Flat + Semantic | false | true | First cut the Markdown title / `Speaker N` / numbered list roughly, then finely cut the characters |
| Parent-Child | true | (ignored) | Parent block `chunk_size`, child block `child_chunk_size`, retrieve parent after hitting child |

**Configuration example that changes the complete text route together**:

```yaml
rag:
  non_text_chunking_strategy: text
  parent_child_enabled: true
  chunk_size: 1500
  chunk_overlap: 200
  child_chunk_size: 500
  child_chunk_overlap: 50
```

Note: `parent_child_enabled`, `semantic_chunking_enabled`, and
`non_text_chunking_strategy` all change index shape. They are rejected as live
updates and must be activated together through a restart followed by rebuild.

### 5.2 Upload the file first or select the switch first?

Both orders work, but have different semantics:

| Order | Behavior | Applicable |
|---|---|---|
| **Select the switch first → then upload** (recommended) | When uploading, directly enter the database according to the new strategy, **without any re-cutting costs** | Clean baseline experiments, new data |
| **Upload first → then switch the switch** | Uploaded files** will not be automatically re-cut** and will still be stored in the vector library according to the uploading strategy | Historical data needs to be compared |

In order for the latter to apply the new policy to old files, "recut" must be explicitly triggered (see 5.3).

### 5.3 Can I switch to other strategies after uploading?

**It can be cut, but there are two paths with different coverage. **

#### Path A: `POST /api/v1/settings/rebuild-vectors` (lightweight, free, fast)

```http
POST /api/v1/settings/rebuild-vectors
X-API-Key: <key>
```

- Behavior (fixed to per-file granularity): file-by-file from `meeting_files.transcript` (already saved plain text)
  `delete_meeting_chunks(meeting_id, file_id)` + `index_meeting()`, **Do not re-adjust ASR/parser**.
- Coverage:
  - ✅ Cut `chunk_size` / `chunk_overlap` / `parent_child_enabled` /
    `semantic_chunking_enabled` - fully effective.
  - ✅ Switch from `native` to `text` - the chunk of the text path will be reconstructed.
  - ⚠️ **Ingest-only structured signals cannot be reconstructed**: The rebuild uses the `transcript` plain text in the database.
    There is no `segments` / `parsed_doc`, so the audio chunk will not be prefixed with `[mm:ss]`,
    The document chunk will also not have table markdown.
  - ❌ Switch back to `native` from `text` - **rebuild cannot do it** because native requires
    `segments` (ASR product) / `parsed_doc` (parser product), rebuild does not have these.

#### Path B: `POST /api/v1/meetings/{id}/reprocess` or `/files/{fid}/reprocess` (complete, expensive)

```http
POST /api/v1/meetings/<meeting_id>/reprocess
# or single file
POST /api/v1/meetings/<meeting_id>/files/<file_id>/reprocess
```

- Behavior: Completely rerun `process_meeting_file()`, including re-ASR, re-parser, and re-vision caption.
- Coverage:
  - ✅ Any direction switching is fully effective, **including `text` → `native`**.
  - ✅ Signal enhancements (audio timestamp prefix, document table markdown) will also be back.
  - ❌ There are external API costs: AssemblyAI charges based on audio duration, mineru.net / aistudio
    Charges are based on the number of document pages.

#### Decision table

| Your goal | Which one to use |
|---|---|
| Cut `non_text_chunking_strategy=text` to have old data applied | rebuild-vectors (accept weak signal) or reprocess (complete signal but spend money) |
| Switch back to `native` to allow old data to be applied | **Required** reprocess |
| Change chunk_size / parent_child / semantic_chunking to apply old data | rebuild-vectors |
| Only test **new files** after modification | No need to do anything, just upload new files directly |

### 5.4 Standard A/B practical cookbook

```bash
# Step 1: Default native, upload files
curl -X POST -F "file=@meeting.mp4" /api/v1/meetings/upload
# (record meeting_id and file_id)

# Step 2: Run a set of queries as baseline
curl -X POST /api/v1/chat -d '{"query": "...", "meeting_ids": [<id>]}' \
  | jq '.sources[].metadata.chunk_strategy_route' # should be "native"

# Step 3: Set non_text_chunking_strategy=text and parent_child_enabled=true
# in backend/config/main.yaml, then restart the backend and run Rebuild Vectors.

# Step 4: Let this file apply the new strategy (Signal full version)
curl -X POST /api/v1/meetings/<meeting_id>/files/<file_id>/reprocess

# Step 5: Run the same set of queries
curl -X POST /api/v1/chat -d '{"query": "...", "meeting_ids": [<id>]}' \
  | jq '.sources[].metadata.chunk_strategy_route' # should be "text"
```

#### Check which path a certain chunk takes

`sources[].metadata` in the `/api/v1/chat` response will contain
`chunk_strategy_route ∈ {native, text}`. You can also directly check the Chroma collection metadata.

---

## 6. Current limitations and future options

1. **`/settings/rebuild-vectors` reconstructs only from persisted text**: it does
   not rerun ASR/parser. After switching to
   `non_text_chunking_strategy=text`, rebuilt audio chunks cannot recover
   ingest-only `[mm:ss]` prefixes; full reprocessing is required. The reverse
   transition (`text` → `native`) also requires reprocessing.
2. **Granularity only global switch**: Currently `non_text_chunking_strategy` is all-modal across the board. If you want to do
   A detailed comparison such as "audio takes text, PDF keeps page-aware" needs to be split into
   `audio_chunking_route` / `document_chunking_route` are two independent enumerations.
3. **Reranker's content-type bias is invalid under text routing**: It will not work when the query contains "table"/"graph"
   Extra points. It is not solved now because independent table/image chunks are no longer generated under the text path.
