# Chunk, Retrieval and Rerank technical documentation

This document is based on the current source code and explains how Meeting Agent generates chunks from file content, writes vectors/BM25 indexes, and executes
The complete process of Broad/Scoped Retrieval, filtering, deduplication, Rerank and handing over to LLM.

Relevant implementations are mainly located at:

- Chunk/index: `backend/src/services/rag/_indexer.py`, `_indexer_extract.py`, `_indexer_store.py`
- Retrieval: `backend/src/services/rag/_retriever.py`, `_filters.py`, `_vector.py`, `_bm25.py`, `_rrf.py`
- Broad recall: `backend/src/services/chain/_steps_retrieve.py`, `_retrieve_broad.py`, `_retrieve_routing.py`
- Post-processing: `backend/src/services/chain/_retrieve_post.py`, `_retrieve_filters.py`, `_retrieve_utils.py`
- Configuration: `backend/src/core/config.py`, `backend/config/main.yaml`, `backend/.env.example`

For complete RAG design background, please also refer to [`backend/docs/rag.md`](../backend/docs/rag.md); this document pays more attention to reality
Calling relationships, data structures and boundary conditions.

## 1. Overall data flow

```text
original file
  │
  ├─ magic-byte/extension verification, SHA-256, disk placement
  ├─ Processor: ASR/Parser/OCR/Vision
  ▼
FileArtefact
  │ text / segments / parsed_doc / aux_segments / structured_json
  ├─ index_meeting() → flat or parent-child chunks
  ├─ index_meeting_segments() → speaker/time-aware chunks
  └─ index_meeting_pages() → page/table/image-aware chunks
       │
       ├─ Chroma meeting collection
       └─ SQLite bm25_index + FTS5 (when hybrid is enabled)

question
  ├─ history-aware query rewrite + query analysis
  ├─ meeting/file summary routing (Broad Recall)
  ├─ vector / BM25 / RAGAnything retrieval
  ├─ RRF, scope, fair allocation, anchor
  ├─ speaker/time/content-type filter
  ├─ sibling co-retrieval
  ├─ pre-rerank dedup
  ├─ Cohere/BGE rerank
  └─ near-duplicate + low-information suppression
       ▼
final ctx.docs → context budget/truncation → LLM prompt
```

There are two confusing "top-k"s in the system:

1. `effective_k` is the basic number that is expected to be retained in this round of retrieval and final answers, determined by the user's `top_k`, `TOP_K`, and question complexity.
   And Broad/meeting scope floor decision.
2. The internal retrieval pool will over-fetch for rerank, filtering, file coverage or multi-query; it is not equal to the final send
   Number of LLMs.

## 2. Chunk generation

### 2.1 Trigger link uploaded to Chunk

`backend/src/api/routers/meetings/_upload.py` completes file verification, temporary disk placement, SHA-256, meeting/file
Logging and idempotent processing, then asynchronously dispatches `backend/src/services/processor/_pipeline.py:process_meeting_file()`.
Successful upload only means that it has been processed, but does not mean that the file is retrievable; the file needs to reach the `ready` status.

Processor first generates `FileArtefact`, and then `_pipeline.py` sorts it by fields and
`NON_TEXT_CHUNKING_STRATEGY` distribution:

| Conditions                                                    | Entry                      | Applicable Content                                                          |
| ------------------------------------------------------------- | -------------------------- | --------------------------------------------------------------------------- |
| `NON_TEXT_CHUNKING_STRATEGY="text"` and has structured fields | `index_meeting()`          | Flatten non-text content into uniform text for benchmark/Pure-Text variants |
| `artefact.segments` exists                                    | `index_meeting_segments()` | ASR segment, image caption/OCR combined segment                             |
| `artefact.parsed_doc` exists                                  | `index_meeting_pages()`    | PDF/PPT/DOC/XLS/CSV and other paged content                                 |
| `artefact.text` only                                          | `index_meeting()`          | TXT, compatible with old entries and plain text                             |

The priority is text override → segments → parsed_doc → text. `aux_segments` is auxiliary information such as video key frames
Product: After the main segments path is completed, the current implementation will treat them as additional segments for `source_kind="image"`
Chunks are written to the index separately; it does not replace the main entry.

### 2.2 Processor and FileArtefact

| File Types | Processor | Main Products |
|---|---|---|
| `video`, `audio` | `AVFileProcessor` | ASR transcript, `start/end/text/speaker` segments, optional keyframes |
| `pdf`, `ppt`, `doc`, `xls`, `csv` | `DocumentFileProcessor` | `ParsedDocument`, page text, table and image assets |
| `image` | `ImageFileProcessor` | caption/OCR, structured page/picture information |
| `txt` and other plain text | `TextFileProcessor` | `text` |

`FileArtefact` key fields:

- `text`: normalized full-text text;
- `segments`: segments with timestamps/speakers;
- `parsed_doc`: structured content such as pages, tables, image assets, etc.;
- `aux_segments`: video keyframes or other auxiliary segments;
- `structured_json` / `structured_kind`: Persistent structured results.

### 2.3 Flat Chunking

`index_meeting()` selects `_index_flat()` based on `PARENT_CHILD_ENABLED` or
`_index_parent_child()`. Flat paths use `RecursiveCharacterTextSplitter`.

The default delimiters are tried in the following order: paragraph/hyphen, line break, Chinese period/exclamation mark/question mark/semicolon, English sentence end, space,
Empty string. In this way, Chinese text will not completely rely on spaces to be segmented.

When `SEMANTIC_CHUNKING_ENABLED=false`, use directly:

```text
chunk_size = CHUNK_SIZE
chunk_overlap = CHUNK_OVERLAP
```

When semantic structure chunking is enabled, `_chunkers.py:_split_by_structure()` is first used based on the Markdown title,
Roughly classify topic boundaries such as speaker tags, numbered lists, horizontal separators, etc., and then only use them for very long paragraphs
`RecursiveCharacterTextSplitter`. Structural chunking is not embedding clustering; it is regular text structure preprocessing.

The core metadata of each flat chunk includes:

```json
{
  "meeting_id": 42,
  "file_id": 137,
  "file_type": "pdf",
  "chunk_index": 3,
  "chunk_id": "meeting_42_file_137_chunk_3"
}
```

### 2.4 Parent-Child Chunking

When `PARENT_CHILD_ENABLED=true`:

1. Use `CHUNK_SIZE_TOKENS/CHUNK_OVERLAP_TOKENS` to generate parents;
2. Each parent uses `CHILD_CHUNK_SIZE_TOKENS/CHILD_CHUNK_OVERLAP_TOKENS` to generate children;
3. Both parent and child are written into Chroma; child has `parent_id`, `chunk_type="child"`;
4. The child also carries `parent_start_offset` / `parent_end_offset`, which is used for the offset fallback after the ID search fails;
5. BM25 uses the same child granularity; retrieval resolves the
   associated parent after a child hit. IDs are generated from location:

```text
meeting_{meeting_id}_file_{file_id}_parent_{parent_index}
meeting_{meeting_id}_file_{file_id}_child_{parent_index}_{child_index}
```

Therefore, changing the chunk size may change the position ID; the offset fallback of the parent check is precisely to reduce this change.
The resulting loss of context. Don't treat location IDs like content IDs that never change across configuration versions.

### 2.5 Page-Aware Chunking

`index_meeting_pages()` iterates over `ParsedDocument.pages`. Normal text for each page is retained on a page-by-page basis
`page_number`; Page text exceeding `CHUNK_SIZE` will be recursively split.

When the configuration is enabled, the page will also generate independent derived chunks:

| `content_type`   | source                                | condition                                              |
| ---------------- | ------------------------------------- | ------------------------------------------------------ |
| `text`           | Page text                             | Text is not empty                                      |
| `table`          | `tables` / `table_assets` in Markdown | `RAG_INDEX_TABLES=true`                                |
| `image_caption`  | Image caption                         | caption is not empty                                   |
| `image_ocr`      | Image OCR                             | OCR length is not less than `RAG_IMAGE_OCR_MIN_LENGTH` |
| `image_combined` | caption + OCR                         | Both work                                              |

Image and page metadata may include `image_storage_path`, thumbnail, page image,
`heading_path` and other positioning information. Tables and pictures are independent recall units and will not be automatically combined into one chunk with the page text.

### 2.6 Segment-Aware Chunking

`index_meeting_segments()` process:

1. Clear empty segments and remove literal `[Speaker]` noise that may be generated by ASR;
2. Determine whether to write `Speaker: text` into chunk text based on `AUDIO_SPEAKER_IN_CONTENT`;
3. Embedding each segment with batch size 64;
4. If `AUDIO_SEMANTIC_BOUNDARY_ENABLED=true`, compare the cosine similarity of adjacent segment embeddings,
   Mark the boundary when it is lower than `AUDIO_SEMANTIC_BOUNDARY_THRESHOLD` and meets the min/max segment constraints;
5. Group by semantic boundaries, upper character limit and `AUDIO_SPLIT_ON_SPEAKER_CHANGE`;
6. Use the dimension-wise average of segment embeddings in the group as chunk embedding to avoid calling the embedding provider again;
7. Write timestamp, speaker and multi-speaker metadata.

A group is split when any of the following conditions are met:

- The current index is semantic boundary;
- Adding the current segment will cause the cumulative length of characters to exceed `CHUNK_SIZE`;
- Turn on speaker-change split and the current speaker is different from the group speaker.

When a long segment exceeds the chunk size, subsequent chunks inherit the speaker of the previous chunk; there are multiple speakers in one chunk.
speaker, even if `AUDIO_SPEAKER_IN_CONTENT=false`, the speaker prefix will be forced to avoid information loss.
The main speaker of the chunk is selected based on the number of characters of each speaker in the group, rather than simply taking the first paragraph.

segment chunk metadata example:

```json
{
  "chunk_id": "meeting_42_file_137_chunk_4",
  "timestamp_start": 92.4,
  "timestamp_end": 138.1,
  "speaker": "Alice",
  "speakers_in_chunk": "Alice\u001fBob",
  "multi_speaker": true,
  "time_position_ratio": 0.31,
  "meeting_duration": 445.2
}
```

`time_position_ratio` is the chunk midpoint divided by the total duration of the session, used for time position related retrieval; it is not the filter itself.

### 2.7 Chunk writing, idempotence and deletion

`_indexer_store.py` is responsible for writing:

- vector upsert: call embedding provider (segment path reusable precomputed embedding), through
  `vectorstore_write_lock()` writes to Chroma;
- unchanged dedup: Calculate the first 12 digits of the normalized text SHA-256 for documents with the same ID. If the text is unchanged, skip it.
 re-embedding;
- BM25: always mirror the same chunk granularity into SQLite FTS5 `bm25_index`. This keeps keyword fallback immediately available and makes `HYBRID_SEARCH_ENABLED` a retrieval-only switch;
- Delete: `delete_meeting_chunks()` delete Chroma, BM25, summary vectors and `index_state` by meeting/file
  Related records.

The location suffix of the chunk ID is used for positioning and upsert; the content hash is only used to determine whether the existing text with the same ID has changed and will not be replaced.
chunk ID. File-level reindex uses per-file `RLock` to avoid delete+upsert interleaved generation of half-new and half-old indexes.

Vector rebuild uses shadow collection: build a new collection first, then switch after success; when the embedding configuration remains unchanged
Existing embeddings can be bulk copied from old collections. On failure, the shadow should be deleted and the live collection retained.

## 3. Retrieval unified entrance

### 3.1 `retrieve()` Contract

`backend/src/services/rag/_retriever.py:retrieve()` receives:

```python
retrieve(
    query,
    meeting_ids=None,
    file_ids=None,
    top_k=None,
    fetch_multiplier=1,
    file_types=None,
    date_from=None,
    date_to=None,
    rag_mode=None,
    known_speakers=None,
    _apply_diversity=True,
    user_id=None,
)
```

Return:

```python
(
    [
        {
            "content": "...",
            "metadata": {...},
            "score": 0.83,
        }
    ],
    QueryAnalysis(...),
)
```

`retrieve()` is responsible for:

1. `analyze_query()` extracts speaker name and temporal hint;
2. Use `top_k * fetch_multiplier` to get the initial fetch number;
3. Construct meeting/file/file_type/date/speaker/user filter;
4. Select strategy based on `rag_mode` or `RAG_RETRIEVER_PROVIDER`;
5. Execute vector, BM25, RAGAnything or their fusion;
6. Normalize every public result to an explicit `score_kind=relevance`, higher-is-better contract;
7. Perform optional meeting/file diversity cap on unscoped queries;
8. Perform optional per-file cap on multi-file scoped queries.

### 3.2 Score direction

The current vector metric is one of `l2`, `cosine`, and `ip`:

- `l2` / `cosine`: The smaller the original distance, the better; threshold is the upper limit of the maximum distance;
- `ip`: The bigger the original score, the better;
- vector adapters label raw values as `score_kind=distance` or `score_kind=relevance`; the public `retrieve()` boundary converts distances with `1/(1+raw)` and always returns higher-is-better relevance scores;
- RRF, BM25, funnel, RAGAnything and reranker outputs explicitly carry `score_kind=relevance`, so downstream merging and web-fallback thresholds never infer direction from global feature flags.

Scoped vector retrieval disables the distance threshold so explicitly selected meeting/file chunks are not accidentally removed; unscoped paths still use `SCORE_THRESHOLD`. Hybrid retrieval does not pre-filter vector results before fusion, allowing both vector and BM25-only hits to participate fairly.

### 3.3 Provider analysis

Valid values for `rag_mode`: `vector`, `hybrid`, `multimodal`, `hybrid_multimodal`, `auto`; legacy `native` is accepted as an alias for `vector`.

| Pattern             | Actual Behavior                                                                                                 |
| ------------------- | --------------------------------------------------------------------------------------------------------------- |
| `vector`            | Vector retrieval; BM25 is used only as a failure fallback |
| `hybrid`            | Force vector + BM25 parallel retrieval, then RRF                                                                |
| `multimodal`        | Call RAGAnything when enabled and applicable; otherwise use the vector fallback                                 |
| `hybrid_multimodal` | Vector and RAGAnything RRF; fall back to hybrid/vector when unavailable                                          |
| `auto`              | Select `hybrid_multimodal` when RAGAnything is enabled, otherwise select `hybrid`                               |

`RAG_RETRIEVER_PROVIDER` is the default provider when the request does not specify `rag_mode`. Unknown values log a warning and fall back to `vector`.

## 4. Four search strategies

### 4.1 Vector

The `vector` strategy always queries Chroma directly and uses user-scoped BM25 only as an availability fallback. `HYBRID_SEARCH_ENABLED` and `HYBRID_ALPHA` do not silently change this strategy.

### 4.2 Hybrid

`_run_hybrid_strategy()` applies `HYBRID_ALPHA` as the vector weight:

- `HYBRID_ALPHA <= 0`: pure BM25;
- `HYBRID_ALPHA >= 1`: pure vector, with BM25 only as an availability fallback;
- `0 < HYBRID_ALPHA < 1`: vector and BM25 are fused with weighted RRF.

For the mixed case:

1. vector and BM25 are executed in parallel in the thread pool;
2. vector does not advance threshold;
3. `_rrf_merge()` merges according to ranking;
4. BM25 is normalized to a positive score (FTS5 rank is a negative BM25, the code is inverted first);
5. If BM25 is being rebuilt, it will temporarily fall back to pure vector to avoid ranking distortion caused by mixing old and new indexes.

The basic formula of RRF is:

```text
score(doc) = w_vector / (k + rank_vector + 1)
           + w_bm25 / (k + rank_bm25 + 1)
```

Each path is normalized against its theoretical maximum. `k` adapts to fetch size: smaller fetches use a smaller value and larger fetches use the configured `RRF_K_PARAM`. Deduplication prefers the stable `logical_chunk_id`, then the physical `chunk_id`; if both are absent it uses a SHA-256 content fingerprint.

### 4.3 Multimodal

`retrieve_with_raganything()` is called when RAGAnything is enabled and the query does not have a strict meeting/file scope. It falls back to vector retrieval when:

- RAGAnything is not enabled;
- RAGAnything returns empty and `RAGANYTHING_FALLBACK_TO_NATIVE=true`;
- RAGAnything throws an error and allows fallback.

When a request has meeting/file scope, the multimodal branch bypasses RAGAnything and uses vector retrieval. RAGAnything still validates every returned document against authoritative database ownership, so unscoped multimodal retrieval remains principal-isolated.

### 4.4 Hybrid Multimodal

When unscoped RAGAnything is available, vector and RAGAnything results are fetched separately and fused through `_rrf_merge_multi()` with `HYBRID_MULTIMODAL_ALPHA`. On a RAGAnything failure, the strategy falls back to native hybrid retrieval (vector + BM25); scoped requests use hybrid/vector retrieval directly.

## 5. Chain layer search order

`backend/src/services/chain/_api.py:_run_pipeline()` executes retrieval in parallel after session/query rewrite.
memory, session, entity, web and history branches. The retrieval branch order is fixed to:

```text
retrieve_documents(ctx)
  → pre_rerank_dedup(ctx)
  → rerank_documents(ctx)
  → suppress_near_duplicates(ctx)
```

`retrieve_documents()` internal order:

1. Warm up query embedding for branches such as summary/router;
2. Calculate `effective_k`;
3. Load known speakers;
4. Read conversational anchor;
5. Generate multi-query variants (if enabled and query is not simple);
6. Enter Broad Recall or Scoped Retrieval;
7. Apply speaker ** or ** temporal filter;
8. Perform sibling co-retrieval on page/table/image hits;
9. When the recall of unscoped rewritten query is empty, try again with the original question;
10. Write the hit file of this round to the session anchor.

Note: speaker and temporal filter are `if ... elif ...` in the current code, and they will not be executed at the same time in the same round.

### 5.1 Query rewrite, analysis and adaptive top-k

`rewrite_query()` skips short queries (up to 6 words and no pronoun reference) directly; long queries are rewritten using LLM and
There is a 10 minute TTL cache. The original question is used when overwriting fails. It is known that the speaker name will be character cleaned and then injected and rewritten.
Tip, avoid rewriting that destroys speaker identity.

`determine_adaptive_top_k()` Rules:

- User explicitly passes `top_k`: limited to 50, with the highest priority;
- Broad Recall Common Questions: At least 8;
- Broad Recall summary intent: at least `SUMMARY_INTENT_TOP_K` (default 16);
- Scoped short questions: default 3;
- Other cases: use `TOP_K` (default 8);
- User pinned meeting but did not select file: additional floor is `TOP_K_MEETING_SCOPED_FLOOR` (default 16);
- When file is selected: The current implementation uses at least 12 as the retrieval floor.

### 5.2 Multi-Query

When `MULTI_QUERY_ENABLED=true` and query is not simple, LLM generates up to `MULTI_QUERY_COUNT` (default 3)
alternative formulation and keep the original problem in variants.

- Broad Recall also requires `RAG_BROAD_RECALL_MULTI_QUERY_ENABLED=true`;
- Each variant will independently participate in file scope selection and fair retrieval;
- file scope uses `RAG_FUNNEL_RRF_K` to do file-level RRF by default between variants;
- `RAG_BROAD_RECALL_MQ_MERGE="zigzag"` can switch back to zigzag;
- The chunk results of variants use the first 16 bits of SHA-256 content hash to remove duplication and retain a better score;
- Scoped multi-query sets the budget of each variant to at least 3, then merges and truncates to `effective_k * fetch_multiplier`.

## 6. Broad Recall: File-level routing and fair retrieval

The judgment of Broad Recall is `not ctx.file_ids`, so "meeting is specified but file is not specified" still belongs to Broad Recall.

### 6.1 Meeting-level router

If no user specifies meeting and `RAG_MEETING_SUMMARY_ROUTER_ENABLED=true`, first
Retrieve meeting summaries from the `meeting_summaries` collection and turn the results into candidate meeting IDs. failure, empty collection, or
When it is lower than the hit requirement, fail-open and continue to use all visible meetings.

### 6.2 File scoping strategy

`RAG_FILE_SCOPING_MODE` has four values:

| Pattern             | Behavior                                                                                |
| ------------------- | --------------------------------------------------------------------------------------- |
| `router_and_funnel` | File summary router concurrently with chunk wide fetch, followed by funnel narrow merge |
| `funnel_only`       | Skip the file summary router and only use chunk evidence to select files                |
| `router_pre_filter` | First, the router narrows the meeting range, and then funnels it within the range       |
| `router_only`       | Only select files by file summary router, without funnel narrow                         |

File summary router supports RRF of vector + summary BM25; the relevant configuration is
`RAG_SUMMARY_ROUTER_*`. summary router returns `None` to indicate unavailability/failure, and the caller can fall back to the old path;
Whether to fall back when healthy but without hits is determined by `RAG_SUMMARY_ROUTER_FALLBACK_TO_CHUNK`.### 6.3 Funnel wide fetch and narrow
Whether to fall back when healthy but without hits is determined by
`RAG_SUMMARY_ROUTER_FALLBACK_TO_CHUNK`.

### 6.3 Funnel wide fetch and narrow

The basic quantity of Wide fetch is:

```text
base = TOP_K * RAG_FUNNEL_FETCH_MULTIPLIER
```

When setting `RAG_FUNNEL_WIDE_K_MIN/MAX`, the pool size will be estimated based on the total number of BM25 chunks, and log factor will be used
After expansion, clamp to upper and lower limits and internal hard cap; use base when not set.

Funnel narrow steps:

1. wide chunk is aggregated by file;
2. The aggregation method is determined by `RAG_FUNNEL_AGGREGATION` and `RAG_FUNNEL_AGG_TOP_K`, and the default is top-k mean;
3. The file title prior can be added, and the file prior is controlled by `RAG_FUNNEL_FILE_PRIOR_*`;
4. Use `RAG_FUNNEL_EVIDENCE_MODE` to filter the evidence floor: `absolute`, `ratio` or `percentile`;
5. The files in the top `max(1, target_files // 4)` of the router are protected and will not be eliminated by the evidence floor;
6. The file lists of router and funnel are merged using `RAG_FUNNEL_MERGE_STRATEGY`, the default is `rrf`, and `zigzag` is available;
7. The RRF file score uses `1/(RAG_FUNNEL_RRF_K + rank + 1)`;
8. Anchor file is injected through cap/evict rules;
9. Return `ScopeSelection(scope_file_ids, file_scores, docs_by_file)`.

The summary intent will lower the evidence floor to a dedicated value of `0.08` in the code and use absolute instead in ratio mode
Interpretation to avoid sparse but relevant documents being eliminated by overview queries.

`docs_by_file` is a wide fetch cache. If a file in the cache already has enough chunks, Fair Retrieval can reuse it.
Avoid issuing Chroma queries for the same file again; the cache is not an independent final result and will still undergo subsequent filtering and post-processing.

### 6.4 Adaptive file chunk budget

Broad Recall uses `_retrieve_routing.py:compute_chunk_budget()`:

```text
target_total = max(effective_k * 2, 16)
For summary intent: at least SUMMARY_INTENT_TOP_K * 2
When meeting pinned: at least file_count * RAG_MIN_CHUNKS_PER_FILE * 3
```

If adaptive chunks are enabled, the file weight is:

```text
score_norm = file_score / max_file_score
weight = max(score_norm, 0.15) * size_factor
```

`size_factor` defaults to page_count or duration_seconds and is limited to 0.5–3.0. Budget limit per file
Between `min_per_file` and `max_per_file=16`. When using multiple queries, the per-file budget is divided by the number of variants, but the floor
is 2. When a user pinned a single meeting, it is forced to be evenly distributed to prevent a certain file from monopolizing the budget.

### 6.5 Fair Retrieval

`fair_retrieve_per_file()` calls `retrieve(file_ids=[fid])` concurrently for each selected file, and is subject to
`RAG_FAIR_CONCURRENCY` (default 8) semaphore limit. The actual number of fetch per file is:

```text
per_file_fetch = max(budget * 2, budget + 2)
```

The current priority of the result deduplication key:

1. `metadata.chunk_id`;
2. `meeting_id:file_id:chunk_index`;
3. Content SHA-1 first 32 bits (only if chunk_index is also missing).

Don't use the old documentation of "always use meeting/file/chunk_index first" as current behavior.

### 6.6 Anchor

When `RAG_ANCHOR_ENABLED=true`, the session anchor saves the meeting/file IDs used by recent answers. Anchor has:

- `RAG_ANCHOR_TTL_MINUTES` expiration time;
- `fixed` or `sliding` TTL mode;
- `RAG_ANCHOR_MAX_IDS` upper limit;
- Whether `RAG_ANCHOR_BOOST_IN_BROAD_RECALL` is injected in Broad scope;
- `RAG_ANCHOR_QUOTA_RATIO` controls injection quota;
- Fallback score ratio for anchor-only files.

Do not read/inject the session anchor when explicitly selecting file to avoid historical context overwriting the user's explicit scope. Rewrite query
When the retry is successful, the retry result will not be written to the anchor to avoid locking the error rewrite into a long-term range.

## 7. Scoped Retrieval

As long as `file_ids` is not empty, `retrieve_documents()` will skip Broad file routing and call it directly
`_retrieve_scoped()` → `retrieve()`.

- Single query: use `effective_k * fetch_multiplier`;
- Multi-query: Each variant uses `max(effective_k // n_variants, 3) * fetch_multiplier`;
- Multiple query results are deduplicated using the first 16 bits of content SHA-256 and sorted by score;
- When multiple files are selected and `SCOPED_MAX_PER_FILE > 0`, the underlying `retrieve()` will do per-file cap; single file
  scope does not do this cap;
- scoped retrieval does not read anchor;
- vector scoped queries turn off distance threshold, but subsequent speaker/temporal filter may still reduce results.

When there is only `meeting_ids` but no `file_ids`, Broad Recall will still be used, but the number of target files will at least cover the meeting
ready files under.

## 8. Filtering, Sibling and failure fallback

### 8.1 Query analysis filters

`QueryAnalysis` can extract known speakers and temporal hints:

- speaker filter checks hits based on source metadata and speaker mapping;
- Temporal filter filters based on `timestamp_start/end` or temporal context;
- When `RAG_SPEAKER_FILTER_PUSHDOWN=true`, the speaker condition will also be pushed down to the Chroma `$or` filter, but after
  The speaker filter is still retained and is compatible with old chunks.

The current pipeline only selects a branch between speaker filter and temporal filter; if the analysis is not successful,
Neither is executed.

### 8.2 Sibling co-retrieval

When `RAG_SIBLING_CORETRIEVE_ENABLED=true`, search for the same file and page for the hit page/table/image chunk
Related sibling, up to:

- `RAG_SIBLING_CORETRIEVE_PER_ANCHOR` per anchor (default 1);
- Total `RAG_SIBLING_CORETRIEVE_MAX_TOTAL` (default 4).

Sibling is recall expansion, which occurs before reranking and may increase the number of candidates beyond the original top-k.

### 8.3 Raw-query retry

If an unscoped request uses rewritten query to get 0 documents, and rewrite is different from the original question, the pipeline will use
Original question Try again. The retry result will not write anchor; scoped requests will not go through this fallback.

### 8.4 Provider fallback

Main fallback relationships:

```text
vector failure → BM25
hybrid while BM25 rebuild → vector
multimodal disabled/empty/error → vector (if allowed)
hybrid_multimodal disabled → hybrid
hybrid_multimodal RAGAnything error → vector + BM25 hybrid
```

These fallbacks will record traces/logs; returning empty results does not mean that the system crashes. When troubleshooting, you should also check the provider,
BM25 rebuild state, vector dimension, scope and `index_state`.

## 9. Pre-rerank Dedup, Rerank and final truncation

### 9.1 Pre-rerank dedup

`pre_rerank_dedup()` is controlled by
`RAG_PRE_RERANK_DEDUP_ENABLED` and is enabled by default. It uses adaptive
character n-grams:

- Length `<100`: 3-gram;
- Length `100–1000`: 4-gram;
- Length `>1000`: 5-gram.

Keep the first candidate when the overlap ratio reaches `RAG_PRE_RERANK_DEDUP_THRESHOLD` (default 0.92) to reduce Cohere/BGE
Call cost. It is not RRF dedup, nor is it final near-duplicate suppression.

### 9.2 `rerank_documents()`

Current trigger conditions:

- No `RERANKER_BINDING` or no candidate: skip;
- The number of candidates is less than `max(final_top_k * 2, 12)`: skip;
- **Single file scope is no longer a skip condition**, and can still be reranked when there are a large number of candidates in a single file.

When executing:

1. Use Cohere or local BGE reranker;
2. `RERANKER_TOP_N` is the basic rerank pool; Broad Recall will be expanded to at least `len(ctx.docs)//3` and distinct
   file number to maintain file coverage;
3. Broad uses `RERANKER_UNSCOPED_MIN_SCORE` (default 0.05), Scoped uses
   `RERANKER_SCOPED_MIN_SCORE` (default 0.10); if all are below the corresponding threshold, the reranker retains the top-n fallback;
4. When `RAG_CONTENT_TYPE_RERANK_ENABLED=true`, content-type bias is applied based on query hints such as table/figure/image;
5. `min_per_file=1` for rerank under Broad Recall;
6. Broad Recall is eventually truncated to `max(final_top_k, covered_file_count)`, so it may be more than `final_top_k`;
7. Scoped is finally strictly truncated to `final_top_k`.

### 9.3 Near-duplicate and low information filtering

`suppress_near_duplicates()` is executed after rerank, using fixed 4-gram and `_CONTENT_SIMILARITY_THRESHOLD=0.85`.
It retains higher-ranked chunks, removes high-overlapping content, and then calls `_filter_low_information_chunks()`.

Low information heuristics include: empty text, pure page numbers such as `Page 1/10`, short text with extremely low letter ratio, copyright/confidentiality/footer, etc.
Weak markup. The filter will try to retain at least two candidates and will not simply clear out all weakly marked documents.

## 10. Specific process examples

Assuming that the user does not select file and the question is "What backend API changes were discussed last week?", the system configuration is:

```text
RAG_FILE_SCOPING_MODE=router_and_funnel
RAG_MEETING_SUMMARY_ROUTER_ENABLED=true
HYBRID_SEARCH_ENABLED=true
HYBRID_ALPHA=0.5
RERANKER_BINDING=cohere
RAG_PRE_RERANK_DEDUP_ENABLED=true
```

The process is as follows:

1. session/history-aware rewrite generates retrieval query;
2. Adaptive top-k is judged as Broad Recall;
3. meeting summary router tries to narrow down meeting candidates;
4. Concurrency between file summary router and chunk wide fetch;
5. Funnel aggregates file scores by chunk evidence, applying evidence mode, title prior, RRF and anchor;
6. Allocate chunk budget according to file score and page/duration size factor;
7. Fair Retrieval concurrently fetches by file, reuses `docs_by_file`, and merges according to the current chunk dedup rules;
8. speaker/temporal filter, sibling co-retrieval;
9. pre-rerank adaptive n-gram dedup;
10. Cohere rerank, Broad Recall maintain at least one/file;
11. post-rerank 4-gram suppression and low-information filtering;
12. The context builder will also truncate according to the token budget and finally send to LLM.

When any of the external providers fails, you should continue along the fallback; only when the critical link cannot be restored, let the pipeline report an error.

## 11. Configuration quick check

Actual defaults are based on `backend/src/core/config.py` and YAML; the following table lists the fields that directly affect Chunk/Retrieval.

| Configuration | Default/Scope | Role |
| -------------------------------------------------------------------------- | ------------------- | ---------------------------------------------------------------- |
| `CHUNK_SIZE_TOKENS` / `CHUNK_OVERLAP_TOKENS` | 384 / 64 | Active token budget for ordinary text, page, vector, and BM25 chunks |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | 1024 / 128 | Legacy compatibility values; `CHUNK_SIZE` also remains the character budget for native audio-segment grouping and structural pre-blocking |
| `PARENT_CHILD_ENABLED` | false | enable parent-child |
| `CHILD_CHUNK_SIZE_TOKENS` / `CHILD_CHUNK_OVERLAP_TOKENS` | 160 / 24 | Active child token budget when parent-child indexing is enabled |
| `CHILD_CHUNK_SIZE` / `CHILD_CHUNK_OVERLAP` | 256 / 32 | Legacy compatibility fallback |
| `SEMANTIC_CHUNKING_ENABLED` | false | Regular structure-aware pre-blocking |
| `NON_TEXT_CHUNKING_STRATEGY` | `native`/`text` | Non-text entry override strategy |
| `AUDIO_SEMANTIC_BOUNDARY_ENABLED` | false | ASR segment semantic boundary |
| `AUDIO_SEMANTIC_BOUNDARY_THRESHOLD` | 0.5 | Adjacent segment similarity threshold |
| `AUDIO_SEMANTIC_MIN_SEGMENTS` / `AUDIO_SEMANTIC_MAX_SEGMENTS` | 2 / 20 | segment grouping constraints |
| `AUDIO_SPEAKER_IN_CONTENT` | true | Whether the speaker writes chunk content |
| `AUDIO_SPLIT_ON_SPEAKER_CHANGE` | true | speaker switches whether to chunk |
| `HYBRID_SEARCH_ENABLED` | true | Whether the explicit hybrid strategy is available; hot-changeable with a compatible provider and does not require reindexing |
| `HYBRID_ALPHA` | 0.5 | Vector/BM25 RRF weight |
| `HYBRID_MULTIMODAL_ALPHA` | 0.5 | Vector/RAGAnything weight |
| `TOP_K` / `SCORE_THRESHOLD` | 8 / 1.5 | Default top-k and unscoped vector threshold |
| `RRF_K_PARAM` | 60 | vector/BM25 RRF base k |
| `UNSCOPED_DIVERSITY_ENABLED` | true | Enable meeting diversity cap without explicit scope |
| `UNSCOPED_MAX_PER_MEETING` / `UNSCOPED_FETCH_MULTIPLIER` | 5 / 4 | Single meeting cap with unscoped over-fetch |
| `RAG_RERANK_FETCH_MULTIPLIER` | 6 | scoped rerank over-fetch |
| `RERANKER_BINDING` | empty (disabled) | `cohere`/generic `http`/local `bge`/empty; enabling it also requires a viable model and credential/endpoint |
| `RERANKER_TOP_N` | 20 | rerank the intermediate document pool; Broad will also amplify according to the candidate size |
| `RERANKER_UNSCOPED_MIN_SCORE` / `RERANKER_SCOPED_MIN_SCORE` | 0.05 / 0.10 | Broad / Scoped lowest score |
| `RAG_FUNNEL_FETCH_MULTIPLIER` | 10 | wide fetch base multiple |
| `RAG_FUNNEL_TOP_MEETINGS` / `RAG_FUNNEL_TOP_FILES` | 12 / 12 | funnel candidate limit |
| `RAG_FUNNEL_AGGREGATION` / `RAG_FUNNEL_AGG_TOP_K` | `top_k_mean` / 3 | file score aggregation |
| `RAG_FUNNEL_EVIDENCE_MODE` | `ratio` | evidence floor interpretation method |
| `RAG_FUNNEL_RRF_K` | 60 | router/funnel file-level RRF |
| `RAG_FILE_SCOPING_MODE` | `router_and_funnel` | Broad file selection strategy |
| `RAG_MEETING_SUMMARY_ROUTER_ENABLED` | true | meeting summary pre-routing |
| `RAG_MEETING_SUMMARY_ROUTER_EXPLORATION_RATIO` | 0.2 | Reserve a share of unscoped file selection for global exploration outside meeting-summary priors |
| `RAG_SUMMARY_ROUTER_ENABLED` | true | file summary pre-routing |
| `RAG_BROAD_RECALL_SCOPE_CAP` | 10 | Final Broad file scope upper limit |
| `RAG_MIN_CHUNKS_PER_FILE` | 3 | Fair Retrieval Minimum file budget |
| `RAG_FAIR_ADAPTIVE_CHUNKS` | true | Whether to allocate by file score/size |
| `RAG_FAIR_CONCURRENCY` | 8 | Fair Retrieval concurrency number |
| `RAG_ANCHOR_ENABLED` / `RAG_ANCHOR_TTL_MINUTES` | true / 30 | session anchor |
| `MULTI_QUERY_ENABLED` / `MULTI_QUERY_COUNT` | false / 3 | query variants |
| `RAG_BROAD_RECALL_MULTI_QUERY_ENABLED` | false | Broad whether to allow multi-query |
| `RAG_PRE_RERANK_DEDUP_ENABLED` / `RAG_PRE_RERANK_DEDUP_THRESHOLD` | true / 0.92 | Adaptive n-gram deduplication before rerank |
| `RAG_SIBLING_CORETRIEVE_ENABLED` | true | Extend retrieval with page/table/image siblings |
| `RAG_SIBLING_CORETRIEVE_PER_ANCHOR` / `RAG_SIBLING_CORETRIEVE_MAX_TOTAL` | 1 / 4 | Maximum siblings per anchor and in total |
| `RAG_CONTENT_TYPE_RERANK_ENABLED` | true | table/figure/image bias |
| `RAG_INDEX_TABLES` / `RAG_INDEX_IMAGE_CAPTIONS` | true / true | Page derived chunk |
| `RAGANYTHING_ENABLED` | false | Multimodal external search branch |
| `RAGANYTHING_FALLBACK_TO_NATIVE` | true | Multimodal failure fallback |

After modifying chunk size, overlap, embedding model/dimension or vector distance metric, it is usually necessary to
`POST /api/v1/settings/rebuild-vectors`; modifying parser/ASR/OCR products requires file/meeting reprocess.

## 12. Code index

### Chunk/index

| Documentation | Responsibility |
|---|---|
| `backend/src/services/processor/_pipeline.py` | Processor parsing, FileArtefact to index entry distribution |
| `backend/src/services/processor/_processors/_types.py` | FileArtefact type |
| `backend/src/services/rag/_indexer.py` | flat, parent-child, page, segment main entrance |
| `backend/src/services/rag/_chunkers.py` | Structure-aware text chunking |
| `backend/src/services/rag/_indexer_extract.py` | Page, table, image metadata |
| `backend/src/services/rag/_indexer_store.py` | chunk ID, unchanged dedup, Chroma/BM25 writing and deletion |
| `backend/src/services/rag/_vector.py` | parent backcheck and score direction |
| `backend/src/services/rag/_vectorstore.py` | Chroma singleton, dimension, write lock |
| `backend/src/services/rag/_bm25.py` | FTS5/BM25 retrieval |
| `backend/src/services/rag/_bm25_maintenance.py` | BM25 drift/rebuild |

### Retrieval/scoping

| Documentation | Responsibility |
|---|---|
| `backend/src/services/rag/_retriever.py` | retrieve, provider strategy, vector/BM25/multimodal |
| `backend/src/services/rag/_filters.py` | provider resolution, Chroma filter, scope filter |
| `backend/src/services/rag/_strategies.py` | Four strategy protocol/selector |
| `backend/src/services/rag/_rrf.py` | vector/BM25, multimodal and summary RRF |
| `backend/src/services/rag/_summary_router.py` | file summary vector/BM25 router |
| `backend/src/services/rag/_meeting_summary_vectorstore.py` | meeting summary vector store |
| `backend/src/services/rag/_funnel.py` | chunk→file aggregation, score normalize, title prior |
| `backend/src/services/rag/_funnel_narrow.py` | wide fetch, evidence floor, router/funnel merge |
| `backend/src/services/rag/_scoping_strategies.py` | Four file scoping strategies |
| `backend/src/services/rag/_fair_retriever.py` | per-file retrieval, concurrency and chunk dedup |
| `backend/src/services/rag/_query.py` | rewrite, simple/summary intent, adaptive top-k |
| `backend/src/services/rag/_query_analysis.py` | speaker/temporal query analysis |
| `backend/src/services/rag/_reranker.py` | Cohere/BGE provider layer |
| `backend/src/services/rag/_raganything.py` | RAGAnything bridge |

### Chain/post-processing

| Documentation | Responsibility |
|---|---|
| `backend/src/services/chain/_steps_retrieve.py` | retrieve_documents general arrangement, filter, sibling, anchor |
| `backend/src/services/chain/_retrieve_broad.py` | Broad/Scoped retrieval, multi-query merge |
| `backend/src/services/chain/_retrieve_routing.py` | known speakers, adaptive per-file budget |
| `backend/src/services/chain/_retrieve_post.py` | pre-dedup, rerank, near-duplicate suppression |
| `backend/src/services/chain/_retrieve_filters.py` | speaker, temporal, content-type bias |
| `backend/src/services/chain/_retrieve_utils.py` | n-gram, low-info, multi-query dedup |
| `backend/src/services/chain/_api.py` | Synchronize pipeline and retrieval branch sequence |
| `backend/src/services/chain/_api_stream.py` | Streaming pipeline retrieval branch |

## 13. Change and Verification Checklist

When modifying a Chunk or Retrieval check at least:

- Whether the new field is written to Chroma metadata, BM25 metadata, API `SourceResponse` and front-end viewer at the same time;
- Whether each retrieval adapter emits explicit score provenance and the public boundary remains higher-is-better;
- Whether scoped and unscoped, single meeting and multiple meetings, and single/multi-query are tested separately;
- Whether the page text, tables, picture caption/OCR, audio speaker/timestamp all have fixtures;
- Whether reranker is not configured, provider timeout, BM25 rebuild, RAGAnything disabled/error can be rolled back;
- Whether parent ID changes, file re-indexing, and reconstruction after deletion will not leave old chunks;
- Whether vector rebuild, multimodal rebuild or full reprocess is required after modifying the configuration.

Recommended verification command:

```bash
cd backend
uv run pytest -q tests -k 'rag or chunk or retrieve or rerank'
uv run ruff check src tests
uv run python -m scripts.benchmark rag-all --top-k 10 --judge-repeats 1
```

Performance/quality comparison must record the model, embedding, RAG configuration, data set version, whether warm cache, iteration,
latency, recall/citation quality, and error rate; the example numbers in the document cannot be regarded as the current benchmark results.
