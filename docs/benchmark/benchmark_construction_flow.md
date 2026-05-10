# Benchmark Construction Flow

This document describes how the RAG chunk/retrieval benchmark is constructed and executed, starting from the AMI Meeting Corpus dataset all the way to final Phase 1 and Phase 2 reports.

---

## 1. Overview

The benchmark is a two-phase evaluation pipeline:

- **Phase 1** compares 8 chunk-strategy configurations (3 methods x 2-3 presets) using the same retrieval settings, and selects the top-2 configs by `combined_recall@10`.
- **Phase 2** runs a retrieval grid search on those top-2 configs, testing 4 retrieval strategy combinations (native/hybrid x no reranker/BGE reranker).

All benchmark runs are isolated via `bench_environment()`, which creates a temporary database and vector store per run.

---

## 2. Dataset: AMI Meeting Corpus

### 2.1 Source Data

The benchmark uses 4 AMI Corpus meetings placed under:

```
backend/tests/fixtures/Dataset/amicorpus/
├── ES2015a/
│   └── audio/ES2015a.Mix-Headset.wav
├── ES2015b/
│   └── audio/ES2015b.Mix-Headset.wav
├── ES2015c/
│   └── audio/ES2015c.Mix-Headset.wav
└── ES2015d/
    └── audio/ES2015d.Mix-Headset.wav
```

### 2.2 Pre-Transcription (Recommended)

To avoid calling the ASR API repeatedly during benchmark runs, pre-transcribe the audio once:

```bash
cd backend
uv run python -m scripts._bench_amicorpus
```

This saves segment JSON files to:

```
backend/tests/fixtures/benchmark/amicorpus_transcripts/
├── ES2015a_segments.json
├── ES2015b_segments.json
├── ES2015c_segments.json
└── ES2015d_segments.json
```

Each segment file contains a list of objects with `start`, `end`, `speaker`, and `text`.

Subsequent benchmark runs will load these pre-transcribed segments instead of calling the ASR API.

---

## 3. Golden Query Generation

Golden queries are LLM-generated question/answer pairs derived from the indexed chunks. They are split into two scopes:

- **Scoped** (default 10 queries): Questions answerable from a single meeting/file.
- **Unscoped** (default 5 queries): Cross-meeting questions requiring information from multiple meetings.

### 3.1 Generation Command

```bash
cd backend
uv run python -m scripts._bench_generate_golden \
  --num-scoped 10 \
  --num-unscoped 5 \
  --output-scoped tests/fixtures/benchmark/amicorpus_golden_scoped.json \
  --output-unscoped tests/fixtures/benchmark/amicorpus_golden_unscoped.json
```

### 3.2 Generation Process

1. **Ingest all 4 AMI meetings** using the default chunk strategy (A Native M: 1024/128, semantic boundaries enabled).
2. **Load all chunks** from the vectorstore.
3. **Form sliding windows** (3-5 adjacent chunks per window) across each meeting, sampling evenly across all meetings.
4. **Send windows to LLM** with a structured prompt that requests:
   - Realistic user questions
   - Mixed answer lengths (half short, half descriptive)
   - Explicit `expected_meeting_ids` and `expected_file_ids`
   - Query type classification (factual, speaker, temporal, summary, comparison)
5. **Validate and enrich** the LLM output:
   - Ensure IDs are lists of integers
   - Add default fields (`source`, `id`)
6. **Save** to `amicorpus_golden_scoped.json` and `amicorpus_golden_unscoped.json`.

### 3.3 Golden File Schema

```json
{
  "version": 1,
  "scope_type": "scoped|unscoped",
  "modality": "audio",
  "items": [
    {
      "id": "audio_scoped_001",
      "query": "Who is the project manager?",
      "expected_answer": "Heather is the project manager.",
      "query_type": "factual",
      "expected_meeting_ids": [1],
      "expected_file_ids": [1],
      "source": "Meeting Transcript"
    }
  ]
}
```

---

## 4. Dynamic Ground Truth Computation

Unlike static benchmarks, the expected chunks are computed **dynamically** for each chunk strategy because different chunk boundaries produce different chunk IDs and contents.

### 4.1 Algorithm (`compute_expected_chunks`)

For each query/answer pair:

1. **Keyword extraction**: Extract content-bearing keywords from the combined query + expected answer, excluding stopwords.
2. **Keyword coverage filter**: For each chunk, compute the fraction of keywords present. A chunk is a candidate if coverage >= 0.25 and at least `adaptive_min` keywords match (adaptive floor: at least 1, at most 3).
3. **Embedding similarity filter**: Compute cosine similarity between the query+answer embedding and each chunk's embedding. Include the top-5 most similar chunks with similarity > 0.
4. **LLM refinement (optional)**: Send the heuristic candidate pool to an LLM judge to filter out false positives. Cached on disk so re-runs are free.
5. **Return** the list of `chunk_id`s judged relevant.

### 4.2 Scoped vs Unscoped

- **Scoped**: `compute_expected_chunks` is called only on chunks from the meetings/files specified in `expected_meeting_ids`/`expected_file_ids`. This ensures the ground truth matches the retrieval scope.
- **Unscoped**: `compute_expected_chunks` is called on **all** chunks across all meetings.

---

## 5. Phase 1: Chunk Strategy Comparison

### 5.1 Configurations

Phase 1 tests 8 chunk configs across 3 methods:

| Method | Presets | Description |
|--------|---------|-------------|
| A Native (Segment-Aware) | S, M, L | Segments routed to `index_meeting_segments()`; semantic boundaries enabled for S/M |
| B Flat | S, M, L | Segments flattened to text via `NON_TEXT_CHUNKING_STRATEGY="text"`; routed to `index_meeting()` |
| C Parent-Child | S, L | Flattened text with parent-child hierarchy enabled |

### 5.2 Execution Flow (per config)

1. Create isolated temp DB and vector store via `bench_environment()`.
2. Lock benchmark settings (hybrid search, top_k, reranker, etc.).
3. Apply chunk config (chunk size, overlap, parent-child, semantic boundaries, non-text strategy).
4. Ingest all 4 AMI meetings.
5. Load all chunks from vectorstore.
6. **Pre-warm embedding cache** for all unique golden queries (avoids repeated API calls).
7. For each **scoped** query:
   - Filter chunks to the expected meeting/file scope.
   - Compute expected chunk IDs dynamically.
   - Call `retrieve(query, meeting_ids=..., file_ids=..., top_k=10, fetch_multiplier=1)`.
   - Compute recall@10, MRR, nDCG@10.
8. For each **unscoped** query:
   - Compute expected chunk IDs dynamically on all chunks.
   - Call `retrieve(query, meeting_ids=None, file_ids=None, top_k=10)`.
   - Compute recall@10, MRR, nDCG@10, and file coverage.
9. Aggregate metrics and compute `combined_recall = 0.5 * scoped_recall + 0.5 * unscoped_recall`.

### 5.3 Top-2 Selection

After all 8 configs complete:

1. Sort configs by `combined_recall` descending.
2. Select the top result.
3. For the second result, prefer a config from a **different method** to ensure diversity.
4. If all top results share the same method, fall back to the second-best overall.

---

## 6. Phase 2: Retrieval Grid Search

### 6.1 Grid

Phase 2 tests 4 retrieval combinations on each of the top-2 chunk configs:

| Provider | Reranker | Description |
|----------|----------|-------------|
| native | off | Vector-only retrieval |
| native | bge | Vector + BGE reranker |
| hybrid | off | Vector + BM25 hybrid |
| hybrid | bge | Hybrid + BGE reranker |

Total: 2 configs x 4 combinations = 8 runs.

### 6.2 Execution Flow (per combination)

Same as Phase 1, with two key differences:

1. **Reranker handling**: When `reranker` is enabled, `fetch_multiplier` is set to `RAG_RERANK_FETCH_MULTIPLIER` (default 6) so the reranker has extra candidates to reorder. The `rerank()` function is explicitly called on the retrieved results.
2. **Retrieval provider**: `RAG_RETRIEVER_PROVIDER` is set to `native` or `hybrid`, and `RERANKER_BINDING` is set accordingly.

### 6.3 Scoring

Each combination receives a weighted score:

```
weighted_score =
  0.4 * unscoped_recall@10
  + 0.3 * scoped_recall@10
  + 0.2 * unscoped_file_coverage
  + 0.1 * unscoped_ndcg@10
```

The combination with the highest `weighted_score` is recommended as the optimal configuration.

---

## 7. Results and Artifacts

After each run, two files are written to `backend/benchmark-results/`:

- `rag-chunk-phase1_{timestamp}.json` — Full per-query breakdown + aggregates
- `rag-chunk-phase1_{timestamp}.md` — Human-readable markdown table

For Phase 2:

- `rag-chunk-phase2_{timestamp}.json`
- `rag-chunk-phase2_{timestamp}.md`

For a full run (Phase 1 + Phase 2):

- `rag-chunk-full_{timestamp}.json` — Combined payload
- `rag-chunk-full_{timestamp}.md` — Combined report

---

## 8. Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Dynamic ground truth | Chunk boundaries change across configs; static chunk IDs would be invalid for flat/parent-child variants |
| Pre-transcribed segments | Avoids repeated ASR API calls ($$$) and ensures deterministic input across all configs |
| Embedding cache pre-warm | Prevents repeated embedding API calls for the same 15 golden queries across 8+ configs |
| Isolated temp DB per config | Guarantees no cross-run pollution; each config starts from a clean state |
| Method diversity in top-2 | Ensures Phase 2 compares fundamentally different chunking philosophies |
| `fetch_multiplier=1` in Phase 1 | Keeps Phase 1 focused on chunk quality, not reranker behavior |
| `fetch_multiplier=6` in Phase 2 when reranking | Gives the reranker enough candidates to demonstrate its value |

---

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `sqlite3.OperationalError: no such table` | Stale thread-local connection from previous run | Already fixed: `close_all_connections()` is called before each `init_db()` |
| Vector search timeout → BM25 fallback | Slow embedding provider or disabled hybrid search | Ensure `HYBRID_SEARCH_ENABLED=true` in `.env`; benchmark locks this to `True` |
| Empty `expected_chunks` for a query | `compute_expected_chunks` couldn't match keywords/embeddings | Lower `keyword_threshold` or check that golden answers are actually in the corpus |
| Phase 2 reranker shows no improvement | `fetch_multiplier=1` gives reranker nothing to reorder | Already fixed: Phase 2 now uses `fetch_multiplier=RAG_RERANK_FETCH_MULTIPLIER` when reranker is enabled |
| Golden queries lack `expected_meeting_ids` | Old golden file from before prompt fix | Regenerate golden queries with updated prompt |
