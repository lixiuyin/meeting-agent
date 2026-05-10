# Benchmarking

The benchmark harness measures latency, throughput, and RAG quality for the meeting-agent backend. It is a developer-invoked tool (not a CI gate) because local hardware varies and noisy runners would produce false positives.

## Prerequisites

- Backend dependencies installed (`uv sync --dev`)
- A working `.env` with LLM and embedding API keys (for chat / RAG answer benchmarks)
- Optional: `reportlab` if you need to regenerate fixtures (`pip install reportlab`)

## Quick start

```bash
cd backend

# Run chat pipeline latency benchmark
uv run python -m scripts.benchmark chat --iterations 5

# Run ingest pipeline latency benchmark
uv run python -m scripts.benchmark ingest --iterations 3

# Run component micro-benchmarks
uv run python -m scripts.benchmark micro

# Run everything
uv run python -m scripts.benchmark all --iterations 5
```

## Subcommands

### `chat`

Ingests fixture documents into a temporary DB + Chroma directory, then runs every query in `tests/fixtures/benchmark/queries.json` N times against the RAG pipeline. Collects `PipelineResult.trace` and writes per-span p50/p95/p99 statistics.

```bash
uv run python -m scripts.benchmark chat [--iterations N] [--baseline] [--update-baseline]
```

### `ingest`

For each fixture file, copies it into the temp upload directory, creates a meeting/file record, and calls `process_meeting_file()` N times. Collects the ingest trace and reports per-stage timing.

```bash
uv run python -m scripts.benchmark ingest [--iterations N] [--baseline] [--update-baseline]
```

### `micro`

Runs `pytest -m benchmark` for component-level benchmarks:

- `embedder.embed_documents`
- `rag.retrieve` against a pre-seeded 1k-doc Chroma
- `reranker.rerank_documents`
- `chunking` throughput
- `parser` for a small PDF
- `tokenizer` throughput

```bash
uv run python -m scripts.benchmark micro
```

### `rag-retrieval`

Evaluates retrieval quality against `golden_set.json` using deterministic chunk IDs. Reports:

- `semantic-only@5` / `semantic-only@10` (Recall, MRR, nDCG)
- `hybrid@10`
- `hybrid+rerank@10` (if a reranker is configured)

```bash
uv run python -m scripts.benchmark rag-retrieval [--top-k 10]
```

### `rag-answer`

Runs the full `ask()` pipeline for each golden item and scores the result with three LLM-as-judge metrics:

- **Faithfulness** — are claims in the answer supported by retrieved context?
- **Answer relevance** — does the answer actually address the question?
- **Context precision** — are the retrieved chunks relevant?

Also reports a cheap lexical `ROUGE-L F1` when a reference answer exists.

```bash
uv run python -m scripts.benchmark rag-answer [--judge-repeats 1]
```

### `rag-snapshot`

Compares current answers and source chunk sets against committed snapshots in `benchmark-results/rag_snapshots/`. Flags:

- Source set changes
- Answer text divergence

```bash
# Update snapshots after intentional model / prompt changes
uv run python -m scripts.benchmark rag-snapshot --update-snapshots

# Diff against committed snapshots
uv run python -m scripts.benchmark rag-snapshot
```

### `rag-all`

Runs `rag-retrieval`, `rag-answer`, and `rag-snapshot` in one shot.

### `rag-matrix`

Runs retrieval across a config matrix (varying `chunk_size`, `top_k`, `hybrid_alpha`, etc.) to find optimal settings.

```bash
uv run python -m scripts.benchmark rag-matrix
```

### `rag-chunk-phase1`

Phase 1: compares audio chunk strategies (segment-based vs silence-based vs fixed-size).

### `rag-chunk-phase2`

Phase 2: retrieval grid search on top-2 chunk configs from Phase 1.

### `rag-chunk-full`

End-to-end Phase 1 + Phase 2 chunk optimization pipeline.

### `all`

Runs `chat`, `ingest`, `micro`, and `rag-all`.

## Baseline and regression detection

The first run on a machine should establish a baseline:

```bash
uv run python -m scripts.benchmark chat --update-baseline
```

Subsequent runs can compare against it:

```bash
uv run python -m scripts.benchmark chat --baseline
```

The default regression threshold is **25%** (`BENCH_REGRESSION_THRESHOLD` env var). The script exits non-zero if any p95 regresses beyond the threshold.

## Reading the report

Each run writes two files to `backend/benchmark-results/`:

- `<name>_<timestamp>.json` — full raw data
- `<name>_<timestamp>.md` — human-readable markdown table

## Fixtures

Synthetic fixtures live in `tests/fixtures/benchmark/`:

| File | Purpose |
|------|---------|
| `sample.pdf` | Text-heavy PDF (exercises marker parser path) |
| `scanned.pdf` | Image-only PDF (forces OCR fallback) |
| `sample.pptx` | 3-slide presentation |
| `sample.wav` | 15s sine sweep (exercises transcriber) |
| `queries.json` | Latency query set |
| `golden_set.json` | Hand-written Q/A pairs for RAG eval |

Regenerate fixtures if needed:

```bash
cd tests/fixtures/benchmark
python generate.py
```

## Adding a new micro-benchmark

1. Add a test class in `tests/benchmark/test_micro.py`
2. Mark it with `@pytest.mark.benchmark`
3. Mock any external API (embedder, reranker, LLM) so the test stays fast and free
4. Run with `pytest -m benchmark`

## Adding a new fixture

1. Update `tests/fixtures/benchmark/generate.py`
2. Run the generator
3. Add the fixture file and any associated queries to `queries.json` or `golden_set.json`
4. Commit the generated files
