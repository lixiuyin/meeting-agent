# Benchmark Configurations and Invocation Guide

> Scope: specialized two-phase audio chunk/retrieval experiment. Verified
> against `backend/scripts/_bench_chunk_configs.py` and the `rag-chunk-phase1`
> / `rag-chunk-phase2` commands on 2026-09-09. For the full current evaluation
> surface and release-grade evidence rules, use
> [`backend/docs/benchmarking.md`](../../backend/docs/benchmarking.md).

This document catalogs all experimental configurations used in the chunk/retrieval benchmark, and provides copy-paste commands for every invocation mode.

---

## 1. Chunk Configurations (Phase 1)

Phase 1 evaluates 8 configurations defined in `backend/scripts/_bench_chunk_configs.py`.

### 1.1 Config List

| # | Name | Preset | Method | Chunk Size | Overlap | Parent-Child | Child Size | Child Overlap | Semantic Boundary | Threshold | Non-Text Strategy |
|---|------|--------|--------|------------|---------|--------------|------------|---------------|-------------------|-----------|-------------------|
| 0 | A Native (Segment-Aware) | S | native | 512 | 64 | No | — | — | Yes | 0.5 | native |
| 1 | A Native (Segment-Aware) | M | native | 1024 | 128 | No | — | — | Yes | 0.5 | native |
| 2 | A Native (Segment-Aware) | L | native | 2048 | 256 | No | — | — | No | — | native |
| 3 | B Flat | S | flat | 512 | 64 | No | — | — | — | — | text |
| 4 | B Flat | M | flat | 1024 | 128 | No | — | — | — | — | text |
| 5 | B Flat | L | flat | 2048 | 256 | No | — | — | — | — | text |
| 6 | C Parent-Child | S | parent_child | 1024 | 128 | Yes | 256 | 32 | — | — | text |
| 7 | C Parent-Child | L | parent_child | 2048 | 256 | Yes | 512 | 64 | — | — | text |

### 1.2 Method Descriptions

- **native**: Audio segments are routed to `index_meeting_segments()`. The indexer preserves speaker labels and timestamps, and optionally detects semantic boundaries to avoid splitting mid-sentence.
- **flat**: `NON_TEXT_CHUNKING_STRATEGY="text"` forces segments to be flattened into plain text (with `[HH:MM] Speaker: text` prefixes) and routed through `index_meeting()`, producing uniform text chunks.
- **parent_child**: Same flattening as **flat**, but `PARENT_CHILD_ENABLED=True` creates a two-level hierarchy: large parent chunks for context + small child chunks for retrieval.

### 1.3 How Chunk Configs Are Applied

```python
# Pseudocode of what apply_chunk_config(cfg) does
settings.CHUNK_SIZE = cfg.chunk_size
settings.CHUNK_OVERLAP = cfg.chunk_overlap
settings.PARENT_CHILD_ENABLED = cfg.parent_child_enabled
settings.CHILD_CHUNK_SIZE = cfg.child_chunk_size
settings.CHILD_CHUNK_OVERLAP = cfg.child_chunk_overlap
settings.AUDIO_SEMANTIC_BOUNDARY_ENABLED = cfg.audio_semantic_boundary_enabled
settings.AUDIO_SEMANTIC_BOUNDARY_THRESHOLD = cfg.audio_semantic_boundary_threshold
settings.NON_TEXT_CHUNKING_STRATEGY = cfg.non_text_chunking_strategy
```

---

## 2. Retrieval Configurations (Phase 2)

Phase 2 tests a 2x2 grid on each of the top-2 chunk configs.

### 2.1 Retrieval Grid

| Provider | Reranker | `RAG_RETRIEVER_PROVIDER` | `RERANKER_BINDING` | `fetch_multiplier` |
|----------|----------|--------------------------|--------------------|--------------------|
| vector | off | `vector` | `""` | 1 |
| vector | bge | `vector` | `bge` | 6 |
| hybrid | off | `hybrid` | `""` | 1 |
| hybrid | bge | `hybrid` | `bge` | 6 |

- **vector**: Vector-only retrieval (Chroma cosine similarity).
- **hybrid**: Reciprocal Rank Fusion (RRF) of vector + BM25 scores.
- **bge**: Local BGE cross-encoder reranker (`BAAI/bge-reranker-v2-m3`). Requires `uv sync --extra huggingface`.

### 2.2 How Retrieval Configs Are Applied

```python
settings.RAG_RETRIEVER_PROVIDER = provider   # "vector" or "hybrid"
settings.RERANKER_BINDING = reranker         # "" or "bge"
```

When `reranker` is non-empty, the benchmark explicitly calls `rerank(query, retrieved_docs, top_n=top_k)` and uses `fetch_multiplier=6` to over-fetch candidates.

---

## 3. Locked Benchmark Settings

The following settings are locked to fixed values during all benchmark runs to ensure fair comparison:

| Setting | Locked Value | Purpose |
|---------|--------------|---------|
| `TOP_K` | 10 | Number of final chunks returned |
| `RERANKER_TOP_N` | 10 | Number of chunks kept after reranking |
| `HYBRID_ALPHA` | 0.5 | Weight between vector and BM25 in hybrid search |
| `RAG_RERANK_FETCH_MULTIPLIER` | 6 | Over-fetch factor when reranker is enabled |
| `RAG_FAIR_ADAPTIVE_CHUNKS` | True | Adaptive per-file chunk budgets in broad recall |
| `RAG_FILE_SCOPING_MODE` | `"router_and_funnel"` | File-selection strategy for unscoped queries |
| `RAG_MEETING_SUMMARY_ROUTER_ENABLED` | True | Meeting-level pre-filter for broad recall |
| `RAG_BROAD_RECALL_MULTI_QUERY_ENABLED` | False | Disable multi-query expansion during retrieval benchmark |
| `QUERY_REWRITE_ENABLED` | False | Disable LLM query rewrite during retrieval benchmark |
| `HYBRID_SEARCH_ENABLED` | True | Enable BM25 + vector hybrid search |
| `EMBEDDING_QUERY_CACHE_ENABLED` | True | Cache embedding vectors for repeated queries |
| `VECTOR_SEARCH_TIMEOUT_S` | 60.0 | Extended timeout for slow embedding providers |

These are set by `lock_benchmark_settings()` in `backend/scripts/_bench_chunk_configs.py`.

---

## 4. Invocation Commands

All commands assume you are in the `backend/` directory.

### 4.1 Pre-Transcribe AMI Corpus (One-Time Setup)

```bash
uv run python -m scripts._bench_amicorpus
```

### 4.2 Generate Golden Queries

```bash
# Default: 10 scoped + 5 unscoped
uv run python -m scripts._bench_generate_golden

# Custom counts
uv run python -m scripts._bench_generate_golden \
  --num-scoped 15 \
  --num-unscoped 8 \
  --output-scoped tests/fixtures/benchmark/amicorpus_golden_scoped.json \
  --output-unscoped tests/fixtures/benchmark/amicorpus_golden_unscoped.json
```

### 4.3 List All Phase 1 Configs

```bash
uv run python -m scripts.benchmark rag-chunk-phase1 --list-configs
```

Output:
```
Available chunk configs (use --config-index N to run a single one):
  [0] A Native (Segment-Aware) S | chunk_size=512 | method=native
  [1] A Native (Segment-Aware) M | chunk_size=1024 | method=native
  ...
```

### 4.4 Run a Single Config

```bash
# Run config index 0 (A Native S)
uv run python -m scripts.benchmark rag-chunk-phase1 --config-index 0 --top-k 10
```

### 4.5 Run Phase 1 (All 8 Configs)

```bash
uv run python -m scripts.benchmark rag-chunk-phase1 --top-k 10
```

Results are written to:
- `backend/benchmark-results/rag-chunk-phase1_{timestamp}.json`
- `backend/benchmark-results/rag-chunk-phase1_{timestamp}.md`

### 4.6 Run Phase 2 (Requires Phase 1 Result)

```bash
uv run python -m scripts.benchmark rag-chunk-phase2 \
  --phase1-result benchmark-results/rag-chunk-phase1_2026-04-30T20-54-47.394722_00-00.json \
  --top-k 10
```

Results are written to:
- `backend/benchmark-results/rag-chunk-phase2_{timestamp}.json`
- `backend/benchmark-results/rag-chunk-phase2_{timestamp}.md`

### 4.7 Run Full Benchmark (Phase 1 + Phase 2)

```bash
uv run python -m scripts.benchmark rag-chunk-full --top-k 10
```

This automatically:
1. Runs Phase 1 on all 8 configs
2. Writes Phase 1 results
3. Reads the top-2 configs from Phase 1
4. Runs Phase 2 grid search
5. Writes combined results

### 4.8 Custom Golden Files

If you want to use non-default golden files:

```bash
uv run python -m scripts.benchmark rag-chunk-phase1 \
  --golden-scoped /path/to/custom_scoped.json \
  --golden-unscoped /path/to/custom_unscoped.json \
  --top-k 10
```

---

## 5. Legacy Benchmark Commands

The benchmark harness also supports the original performance and quality benchmarks:

### 5.1 Chat Pipeline Latency

```bash
uv run python -m scripts.benchmark chat --iterations 5
```

### 5.2 Ingest Pipeline Latency

```bash
uv run python -m scripts.benchmark ingest --iterations 3
```

### 5.3 Retrieval Metrics (Old Pipeline)

```bash
uv run python -m scripts.benchmark rag-retrieval --top-k 10
```

This runs against `tests/fixtures/benchmark/golden_set.json` (not the AMI corpus).

### 5.4 Answer Quality (LLM-as-Judge)

```bash
uv run python -m scripts.benchmark rag-answer --judge-repeats 3
```

### 5.5 Run Everything

```bash
uv run python -m scripts.benchmark all --iterations 5 --top-k 10 --judge-repeats 1
```

---

## 6. Config Quick Reference

### 6.1 Phase 1 Config Matrix

```python
# backend/scripts/_bench_chunk_configs.py
AUDIO_CHUNK_CONFIGS = [
    ChunkConfig(name="A Native (Segment-Aware)", preset="S", method="native", chunk_size=512, chunk_overlap=64, parent_child_enabled=False, audio_semantic_boundary_enabled=True, audio_semantic_boundary_threshold=0.5),
    ChunkConfig(name="A Native (Segment-Aware)", preset="M", method="native", chunk_size=1024, chunk_overlap=128, parent_child_enabled=False, audio_semantic_boundary_enabled=True, audio_semantic_boundary_threshold=0.5),
    ChunkConfig(name="A Native (Segment-Aware)", preset="L", method="native", chunk_size=2048, chunk_overlap=256, parent_child_enabled=False, audio_semantic_boundary_enabled=False),
    ChunkConfig(name="B Flat", preset="S", method="flat", chunk_size=512, chunk_overlap=64, parent_child_enabled=False, non_text_chunking_strategy="text"),
    ChunkConfig(name="B Flat", preset="M", method="flat", chunk_size=1024, chunk_overlap=128, parent_child_enabled=False, non_text_chunking_strategy="text"),
    ChunkConfig(name="B Flat", preset="L", method="flat", chunk_size=2048, chunk_overlap=256, parent_child_enabled=False, non_text_chunking_strategy="text"),
    ChunkConfig(name="C Parent-Child", preset="S", method="parent_child", chunk_size=1024, chunk_overlap=128, parent_child_enabled=True, child_chunk_size=256, child_chunk_overlap=32, non_text_chunking_strategy="text"),
    ChunkConfig(name="C Parent-Child", preset="L", method="parent_child", chunk_size=2048, chunk_overlap=256, parent_child_enabled=True, child_chunk_size=512, child_chunk_overlap=64, non_text_chunking_strategy="text"),
]
```

### 6.2 Phase 2 Retrieval Grid

```python
# backend/scripts/_bench_rag_phase2.py
RETRIEVAL_GRID = [
    ("vector", ""),      # Vector only, no rerank
    ("vector", "bge"),   # Vector + BGE rerank
    ("hybrid", ""),      # Hybrid, no rerank
    ("hybrid", "bge"),   # Hybrid + BGE rerank
]
```

### 6.3 Scoring Formula

**Phase 1 Combined Recall:**
```
combined_recall = 0.5 * scoped_recall@10 + 0.5 * unscoped_recall@10
```

**Phase 2 Weighted Score:**
```
weighted_score =
  0.4 * unscoped_recall@10
  + 0.3 * scoped_recall@10
  + 0.2 * unscoped_file_coverage
  + 0.1 * unscoped_ndcg@10
```

---

## 7. File Reference

| File | Purpose |
|------|---------|
| `backend/scripts/benchmark.py` | CLI entry point for all benchmark commands |
| `backend/scripts/_bench_chunk_configs.py` | Chunk config definitions and setting applicators |
| `backend/scripts/_bench_rag_phase1.py` | Phase 1 implementation (8 chunk configs) |
| `backend/scripts/_bench_rag_phase2.py` | Phase 2 implementation (retrieval grid on top-2) |
| `backend/scripts/_bench_amicorpus.py` | AMI corpus ingestion helpers |
| `backend/scripts/_bench_generate_golden.py` | LLM-based golden query generation |
| `backend/scripts/_bench_map_golden.py` | Dynamic ground-truth chunk computation |
| `backend/scripts/_bench_llm_ground_truth.py` | LLM-as-judge chunk filter with disk cache |
| `backend/scripts/_bench_rag_quality.py` | Pure metrics: recall@k, MRR, nDCG, file coverage |
| `backend/scripts/_bench_aggregate.py` | Report formatting (JSON + markdown) |
| `backend/scripts/_bench_env.py` | Isolated temp DB/vectorstore context manager |
| `tests/fixtures/benchmark/amicorpus_golden_scoped.json` | Scoped golden queries |
| `tests/fixtures/benchmark/amicorpus_golden_unscoped.json` | Unscoped golden queries |
| `tests/fixtures/benchmark/amicorpus_transcripts/*.json` | Pre-transcribed AMI segments |
