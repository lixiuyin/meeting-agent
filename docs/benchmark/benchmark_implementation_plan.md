# Plan: Extend Benchmark for audio modalities (Chunk + RAG two-stage evaluation)

> **Historical implementation plan.** The planned modules and CLI commands now
> exist. Keep this file for design rationale; do not use its statements about
> missing runner support as current behavior. See
> [`benchmark_config_guide.md`](benchmark_config_guide.md) for this experiment
> and [`backend/docs/benchmarking.md`](../../backend/docs/benchmarking.md) for
> the maintained evaluation contract.

## Background

At the time this plan was written, the project already had a two-stage benchmark test plan (`docs/benchmark/benchmark_test_plan.md`) for systematically selecting the optimal **Chunk strategy** and **retrieval strategy** combination for each file mode. The then-current benchmark script (`backend/scripts/benchmark.py`) supported only legacy synthetic fixtures (`sample.pdf`, `sample.pptx`) and had the following shortcomings:
- Use legacy chunk IDs (such as `chunk_0`) instead of the full `meeting_{mid}_file_{fid}_chunk_0`
- Only supports **Scoped** to retrieve reviews, does not support **Unscoped (Broad Recall)**
- Chunk strategies are not differentiated by mode (no differentiated evaluations such as segment-aware, page-aware, etc.)

This plan is based on the existing code and extends the benchmark to support **audio mode**, using AMI corpus fixtures (`backend/tests/fixtures/Dataset/amicorpus/`), containing `.wav` audio files from 4 conferences (`ES2015a`–`ES2015d`). The goal is to implement a two-stage benchmark (Chunk comparison + retrieval Grid Search) that can be implemented.

## Core Insight: Isolation does not require manual cleanup

The existing `bench_environment()` context manager (`backend/scripts/_bench_env.py`) has created a **fully isolated temporary environment** (temporary database + temporary vector library + temporary upload directory) for each benchmark run. This means:
- **No need to manually delete vector libraries** to avoid index pollution between different chunk strategies
- Each Phase 1 / Phase 2 operation only needs to be executed in a separate `bench_environment()`, and the operating system will automatically delete the temporary directory after exiting

---

## Part 1: Amicopus audio data ingestion process

### 1.1 Audio Transcription
The existing `process_meeting_file()` pipeline already fully supports audio files:
- Copy `.wav` to `UPLOAD_DIR`
- Call ASR via `services/transcriber.py` and its AssemblyAI provider binding
- Returns `FileArtefact` with `segments` (speaker + timestamp + text)

**Prerequisite**: `ASSEMBLYAI_API_KEY` needs to be configured in `.env`.

### 1.2 Added Amicopus ingest assistant
Add in `backend/scripts/_bench_fixtures.py` (or create a new `backend/scripts/_bench_amicorpus.py`):

```python
async def ingest_amicorpus_meeting(meeting_name: str) -> tuple[int, int]:
    """Create meeting, copy .wav, run ASR pipeline. Return (meeting_id, file_id)."""
```

Specific steps:
1. Create a `Meeting` record, the title is `meeting_name`, and the date is fixed
2. Copy `amicorpus/{meeting_name}/audio/{meeting_name}.Mix-Headset.wav` to the upload directory
3. Create `meeting_files` record, `file_type="audio"`
4. Call `await process_meeting_file(file_id)` to trigger transcription
5. Return `(meeting_id, file_id)`

After transcription is complete, the chunks in the vector library are determined by the currently active chunk setting. This is the basis for the Phase 1 comparison.

---

## Part 2: Benchmark data construction method (LLM automatically generates Golden Set)

### 2.1 Core idea: Decoupling Query/Answer and Chunk ID

Different chunk strategies generate different chunks. If you manually mark the chunk ID for each strategy, the workload is huge and unmaintainable. Our plan is:

1. **LLM generates Query + Answer** based on content (not bound to specific chunk ID)
2. **Dynamic Ground Truth Mapping**: For each chunk strategy, automatically calculate which chunks contain the key information of the answer

The golden set generated in this way is **chunk-agnostic** - query and answer are fixed, but `expected_chunks` is dynamically calculated according to the current chunk policy.

### 2.2 Step 1: LLM generates Query + Answer

First select a reference chunk strategy (such as the default Segment-Aware M) to index all 4 meetings and export the text of all chunks. Then call LLM to generate queries/answers in batches.

**Prompt Design (Scoped)**:
```
You are a test data generator for a conference question answering system. Given the following snippet of a conference transcript:

{chunk_texts}

Please generate {n} real questions that users may ask, requiring:
1. Each question must be answerable using only information from the text above
2. Question types need to cover: fact query, speaker-specific query, time range query, summary query
3. Give a standard answer (answer), which must be completely based on the text content
4. Indicate which meetings/files this issue involves (meeting_id, file_id)

Output format (JSON):
[
  {
    "query": "...",
    "expected_answer": "...",
    "expected_meeting_ids": [1],
    "expected_file_ids": [1],
    "query_type": "factual|speaker|temporal|summary"
  }
]
```

**Prompt Design (Unscoped)**:
```
Below are summaries and key takeaways from multiple sessions:

{meeting_summaries}

Please generate {n} cross-conference queries, requiring:
1. Answers to questions are spread across multiple meetings
2. Types include: comparison, summary, cross-meeting fact query
3. Give a standard answer and indicate which meetings/documents are involved

Output format (JSON)...
```

**Implementation module**: Create new `backend/scripts/_bench_generate_golden.py`

```python
async def generate_queries_from_chunks(
    chunks: list[dict],
    scope_type: str, # "scoped" or "unscoped"
    num_queries: int,
) -> list[dict]:
    """Calling LLM to generate query + answer based on chunks, excluding chunk IDs."""
```

Key details:
- To avoid the query being too local, the text input to the LLM should be a combination of **adjacent 3–5 chunks** (preserving the complete context) rather than a single chunk
- For audio modality, provide `speaker` and `timestamp` information explicitly in prompt to guide LLM to generate speaker-related and time-related queries
- Use the project's existing LLM calling method (`src.services.llm`) instead of external API to ensure configuration consistency

### 2.3 Step 2: Dynamic calculation of Expected Chunks

For each chunk strategy, after indexing is completed, `expected_chunks` needs to be automatically calculated for all queries under the strategy.

Create new `backend/scripts/_bench_map_golden.py`:

```python
def compute_expected_chunks(
    query_item: dict,
    chunks: list[dict], # All chunks under the current chunk policy, each item contains chunk_id, text, metadata
    method: str = "hybrid",
) -> list[str]:
    """
    Calculate which chunks contain the information required for the answer for the current chunk policy.
    Returns a list of chunk_ids.
    """
```

**Mapping Strategy (Hybrid Method Recommended)**:

| Strategy | Description |
|------|------|
| **Keyword Coverage** | Extract key entities (nouns, numbers, proper nouns) from `expected_answer` and check whether each chunk contains these entities. Good coverage but may be noisy. |
| **Embedding similarity** | Calculate the embedding cosine similarity between `expected_answer` and each chunk, and take Top-N (such as Top-3). It can capture chunks that are semantically related but have no common keywords. |
| **LLM Determination** (optional) | Give the chunk content and answer to LLM and ask "Does the chunk contain the information required for the answer?" Accurate but costly. |

**Recommended implementation**: First combine **Keyword Coverage + Embedding Similarity**, and take the union:
```python
# 1. Keyword coverage
keywords = extract_keywords(query_item["expected_answer"])
keyword_hits = [c for c in chunks if contains_keywords(c["text"], keywords)]

# 2. Embedding similarityanswer_emb = embed(query_item["expected_answer"])
chunk_embs = embed([c["text"] for c in chunks])
similarities = cosine_similarity(answer_emb, chunk_embs)
embedding_hits = [chunks[i] for i in top_k_indices(similarities, k=3)]

# 3. Take the union and remove duplicates
expected_chunks = list({c["chunk_id"] for c in keyword_hits + embedding_hits})
```

In this way, even if the chunk segmentation method changes (such as changing from Segment-Aware to Flat), as long as the content is the same, the corresponding chunks can be found automatically.

### 2.4 Golden Set file format

Finally, two JSON files are stored (only query + answer, without chunk IDs):

- `backend/tests/fixtures/benchmark/amicorpus_golden_scoped.json`
- `backend/tests/fixtures/benchmark/amicorpus_golden_unscoped.json`

**Scoped example**:
```json
{
  "version": 1,
  "scope_type": "scoped",
  "modality": "audio",
  "items": [
    {
      "id": "audio_scoped_001",
      "query": "What did Alice say about budgeting in ES2015a?",
      "expected_meeting_ids": [1],
      "expected_file_ids": [1],
      "expected_answer": "Alice proposes a 20% increase in the budget.",
      "query_type": "speaker"
    }
  ]
}
```

**Unscoped example**:
```json
{
  "version": 1,
  "scope_type": "unscoped",
  "modality": "audio",
  "items": [
    {
      "id": "audio_unscoped_001",
      "query": "Which meetings discussed the budget increase?",
      "expected_meeting_ids": [1, 2],
      "expected_file_ids": [1, 2],
      "expected_answer": "Both ES2015a and ES2015b discussed budget increases.",
      "query_type": "summary"
    }
  ]
}
```

**Dynamic binding of chunk IDs at runtime**:
Every time the benchmark runs (a certain chunk strategy):
1. Read the above JSON (query + answer fixed)
2. Get all chunks in the current vector library
3. Call `compute_expected_chunks()` to dynamically calculate `expected_chunks` for each item
4. Use dynamically calculated chunk IDs for retrieval evaluation

### 2.5 Complete workflow

```
┌───────────────────────────────────────────┐
│ 1. Index all 4 meetings using the reference chunk strategy │
│ (such as Segment-Aware M) │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌───────────────────────────────────────────┐
│ 2. Export all chunks text │
│ (including speaker, timestamp, text content) │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌───────────────────────────────────────────┐
│ 3. LLM generates Golden Query + Answer │
│ - Scoped: 8–12 items │
│ - Unscoped: 4–6 items │
│ Output: amicopus_golden_scoped.json │
│amicorpus_golden_unscoped.json │
│ (only query/answer, excluding chunk IDs) │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌───────────────────────────────────────────┐
│ 4. Benchmark runtime dynamic binding │
│For each chunk strategy: │
│ a. Reindex │
│ b. Read golden JSON │
│ c. compute_expected_chunks() │
│ d. Run retrieval evaluation │
└────────────────────────────────────────────┘
```

### 2.6 Advantages and Precautions

**Advantages**:
- **Full Automation**: No need for humans to read chunks or write queries
- **Chunk-agnostic**: The same set of query/answer can be reused for all chunk strategies
- **Fair and Comparable**: The ground truth of each chunk strategy is dynamically calculated based on the segmentation results of the strategy to avoid the unfair situation of "using Flat chunk ID to evaluate Segment-Aware"
- **Extensible**: When adding a new chunk strategy, you only need to re-run the dynamic mapping without re-labeling.

**Note**:
- The accuracy of dynamic mapping depends on the quality of keyword extraction and embedding similarity. It is recommended to perform sampling verification after implementation: randomly select 10 queries and manually check whether the chunks returned by `compute_expected_chunks()` indeed contain answer information
- If `expected_answer` is very short (such as "Alice"), keyword coverage may return too many chunks, and a minimum keyword number threshold can be added (such as matching at least 2 keywords)
- For the Parent-Child strategy, the retrieval unit is the child chunk, but the contents of child chunks should be checked during dynamic mapping (because they are the ones that are actually retrieved)

---

## Part 3: How to extend existing Benchmark code

### 3.1 Fix Chunk ID matching logic in existing code
Currently `run_rag_retrieval_benchmark()` in `backend/scripts/benchmark.py` uses legacy prefix splicing:
```python
expected = {f"meeting_{meeting_id}_{chunk}" for chunk in item.get("expected_chunks", [])}
```
Need to compare the full ID directly instead:
```python
expected = set(item.get("expected_chunks", []))
```
The ID of the search result should also be obtained directly from `metadata.chunk_id`; if it does not exist, use `_chunk_id_prefix()` to construct it based on `meeting_id`, `file_id`, `chunk_index`.

### 3.2 Added Phase 1 / Phase 2 evaluation logic
Create new modules `backend/scripts/_bench_rag_phase1.py` and `backend/scripts/_bench_rag_phase2.py` (can also be combined into one function).

#### Phase 1: Chunk strategy comparison
Input: A dictionary of chunk configurations.

For each configuration, execute within a separate `bench_environment()`:
1. Ingest all 4 amicopus audio files
2. For each query in `amicorpus_golden_scoped.json`:
   - Call `retrieve(query, meeting_ids=[mid], top_k=10, fetch_multiplier=1)`
   - Record `Recall@10`, `MRR`, `NDCG@10`
3. For each query in `amicorpus_golden_unscoped.json`:
   - Call `retrieve(query, meeting_ids=None, file_ids=None, top_k=10)`- Record `Recall@10`, `MRR`, `NDCG@10`, `File Coverage@10`
4. Calculate the comprehensive score: `0.5 × Scoped_Recall@10 + 0.5 × Unscoped_Recall@10`

**8 sets of configurations to be tested for audio modes**:

| Method | Default | CHUNK_SIZE | AUDIO_SEMANTIC_BOUNDARY_ENABLED | AUDIO_SEMANTIC_BOUNDARY_THRESHOLD | PARENT_CHILD_ENABLED | CHILD_CHUNK_SIZE |
|------|------|-----------|--------------------------------|-----------------------------------|---------------------|-----------------|
| A Native (Segment-Aware) | S | 512 | True | 0.5 | False | — |
| A Native (Segment-Aware) | M | 1024 | True | 0.5 | False | — |
| A Native (Segment-Aware) | L | 2048 | False | — | False | — |
| B Flat | S | 512 | — | — | False | — |
| B Flat | M | 1024 | — | — | False | — |
| B Flat | L | 2048 | — | — | False | — |
| C Parent-Child | S | 1024 | — | — | True | 256 |
| C Parent-Child | L | 2048 | — | — | True | 512 |

**How to switch configuration**:
- Modify `settings.CHUNK_SIZE = cfg["chunk_size"]` etc. before ingestion
- **Flat / Parent-Child**: Set `settings.NON_TEXT_CHUNKING_STRATEGY = "text"`, and then run the `process_meeting_file()` pipeline normally. The system will automatically flatten the segment/page content into plain text through `_should_route_artefact_to_text_chunking()` and send it to `index_meeting()`. No need to manually stitch text together.
- **Native**: Set `settings.NON_TEXT_CHUNKING_STRATEGY = "native"` (default), run the pipeline normally, and automatically call `index_meeting_segments()`.
- After all configurations are switched, make sure to call `delete_meeting_chunks(meeting_id, file_id=file_id)` to clean up the old indexes (the isolation environment of `bench_environment` already guarantees this automatically).

**Top-2 Filter**:
After all 8 groups are run, select the top-2 by `Combined_Recall@10` and ensure priority from **different methods** (according to plan §2.6 rules).

#### Phase 2: Retrieval strategy Grid Search
For the top-2 chunk configuration selected in Phase 1, each group runs 4 search combinations × 2 scopes:

| Retrieval Strategy | Reranker | Configuration |
|---------|----------|------|
| Vector | OFF | `RAG_RETRIEVER_PROVIDER=vector`, `RERANKER_BINDING=""` |
| Vector | ON | `RAG_RETRIEVER_PROVIDER=vector`, `RERANKER_BINDING=cohere/bge` |
| Hybrid | OFF | `RAG_RETRIEVER_PROVIDER=hybrid`, `RERANKER_BINDING=""` |
| Hybrid | ON | `RAG_RETRIEVER_PROVIDER=hybrid`, `RERANKER_BINDING=cohere/bge` |

Each group is combined in a separate `bench_environment()`:
1. Reindex all meetings with the same top-2 chunk configuration
2. Run scoped queries (`meeting_ids=[mid]`)
3. Run unscoped queries (`meeting_ids=None`)
4. Record all indicators
5. Calculate the weighted comprehensive score: `0.4 × Unscoped_Recall + 0.3 × Scoped_Recall + 0.2 × File_Coverage + 0.1 × NDCG`

### 3.3 New CLI subcommand
Added in `_build_parser()` in `backend/scripts/benchmark.py`:

```python
# rag-chunk-phase1
rag_p1 = sub.add_parser("rag-chunk-phase1", help="Phase 1: Comparing chunk strategies for audio modalities")
rag_p1.add_argument("--golden-scoped", default=str(FIXTURE_DIR / "amicorpus_golden_scoped.json"))
rag_p1.add_argument("--golden-unscoped", default=str(FIXTURE_DIR / "amicorpus_golden_unscoped.json"))
rag_p1.add_argument("--output", default=str(RESULTS_DIR / "phase1_results.json"))

# rag-chunk-phase2
rag_p2 = sub.add_parser("rag-chunk-phase2", help="Phase 2: Retrieve grid search for top-2 chunk")
rag_p2.add_argument("--phase1-result", required=True)
rag_p2.add_argument("--output", default=str(RESULTS_DIR / "phase2_results.json"))

# rag-chunk-full
rag_full = sub.add_parser("rag-chunk-full", help="End-to-end run Phase 1 + Phase 2")
```

### 3.4 Configuration locking in a single run
Fix the following configuration before each run to ensure a fair comparison:
```python
settings.TOP_K = 10
settings.RERANKER_TOP_N = 10
settings.HYBRID_ALPHA = 0.5
settings.RAG_RERANK_FETCH_MULTIPLIER = 6
settings.RAG_FAIR_ADAPTIVE_CHUNKS = True
settings.RAG_FILE_SCOPING_MODE = "router_and_funnel" # Fixed Broad Recall file selection strategy
settings.RAG_MEETING_SUMMARY_ROUTER_ENABLED = True # Fixed meeting level pre-routing
settings.RAG_BROAD_RECALL_MULTI_QUERY_ENABLED = False # Exclude Multi-Query interference
settings.QUERY_REWRITE_ENABLED = False # Eliminate LLM interference of query rewrite
```

> **About `RAG_SUMMARY_ROUTER_ENABLED` and `RAG_MEETING_SUMMARY_ROUTER_ENABLED`**: > - `RAG_MEETING_SUMMARY_ROUTER_ENABLED` controls meeting-level summary pre-routing (in the early stages of `_retrieve_broad_recall()`). > - `RAG_SUMMARY_ROUTER_ENABLED` controls file-level summary routing (used internally by scoping strategy). Both are left on to simulate a production environment.
> - `RAG_FILE_SCOPING_MODE` must be locked, otherwise different strategies (`router_and_funnel` vs `router_only`) will lead to inconsistent file selection behavior, affecting benchmark comparability.

---

## Part 4: File modification list

### Modify existing files
1. **`backend/scripts/benchmark.py`**
   - Repair chunk ID matching logic, change from splicing to direct comparison of complete IDs
   - Added new subcommands `rag-chunk-phase1`, `rag-chunk-phase2`, `rag-chunk-full`
   - Implement `run_rag_chunk_phase1()`, `run_rag_chunk_phase2()`

2. **`backend/scripts/_bench_fixtures.py`**
   - Added `ingest_amicorpus_meeting()` helper
   - Added `ingest_all_amicorpus()`, returning `{meeting_name: (mid, fid)}`

3. **`backend/scripts/_bench_aggregate.py`**- Added `format_chunk_benchmark_markdown()` to render Phase 1/2 result table

### Add new file
4. **`backend/scripts/_bench_amicorpus.py`**
   - Auxiliary functions for copying amicopus audio, creating conferences, and calling ASR pipelines

5. **`backend/scripts/_bench_chunk_configs.py`**
   - Data classes that define 8 sets of audio mode configurations
   - `apply_chunk_config(cfg)` function, safely modify `settings`
   - Added `apply_non_text_strategy(strategy: str)`, used to switch `"native"` / `"text"` mode

6. **`backend/scripts/_bench_rag_phase1.py`**
   - Phase 1 core logic: traverse configuration → ingest → evaluate scoped + unscoped → calculate comprehensive score → return to top-2

7. **`backend/scripts/_bench_rag_phase2.py`**
   - Phase 2 core logic: read top-2 → traverse 4 search × rerank combinations → evaluate → calculate weighted score

8. **`backend/scripts/_bench_generate_golden.py`**
   - Call LLM to automatically generate query + answer based on chunks content (chunk-agnostic)

9. **`backend/scripts/_bench_map_golden.py`**
   - Dynamic Ground Truth mapping: automatically calculates `expected_chunks` for each chunk strategy
   - Implement a hybrid mapping strategy of keyword coverage + Embedding similarity

10. **`backend/tests/fixtures/benchmark/amicorpus_golden_scoped.json`**
11. **`backend/tests/fixtures/benchmark/amicorpus_golden_unscoped.json`**

---

## Part 5: How to run the entire evaluation process

### Preconditions
- `ASSEMBLYAI_API_KEY` has been configured in `backend/.env`
- golden set is marked and placed in fixtures directory

### Step by step execution

1. **Generate Golden Set at one time** (skip if golden set already exists):
   ```bash
   cd backend
   # First use the reference chunk policy (default Segment-Aware M) to index all meetings
   uv run python -m scripts._bench_generate_golden \
       --output-scoped tests/fixtures/benchmark/amicorpus_golden_scoped.json \
       --output-unscoped tests/fixtures/benchmark/amicorpus_golden_unscoped.json \
       --num-scoped 10 --num-unscoped 5
   # Optional: manual sampling to verify the quality of generated query/answer
   ```

2. **Run Phase 1** (~8 independent runs, re-transcribed each time):
   ```bash
   uv run python -m scripts.benchmark rag-chunk-phase1
   ```
   Output: `backend/benchmark-results/phase1_results_{timestamp}.json` + `.md`

3. **Run Phase 2** (~8 independent runs):
   ```bash
   uv run python -m scripts.benchmark rag-chunk-phase2 \
       --phase1-result benchmark-results/phase1_results_xxx.json
   ```
   Output: `backend/benchmark-results/phase2_results_{timestamp}.json` + `.md`

4. **Run the complete process with one click**:
   ```bash
   uv run python -m scripts.benchmark rag-chunk-full
   ```

### Dealing with the cost and time of transcription
AssemblyAI transcription is slow and expensive, and `bench_environment()` creates an isolation temporary directory each time, resulting in re-transcription on every run. Mitigation options:
- **Option A (recommended)**: Accept the cost after the first transcription, and follow the normal process when the benchmark is run.
- **Option B (Quick Path)**: Pre-transcribe 4 sessions at a time, saving the segments as `backend/tests/fixtures/benchmark/amicorpus_transcripts/ES2015a_segments.json`. Add shortcut logic in `_bench_amicorpus.py`: when the environment variable `BENCH_USE_PRETRANSCRIBED=1` exists and the pre-transcription file exists, skip ASR, load JSON directly and construct artefact post-index

We will implement the fast path for option B in `_bench_amicorpus.py`:
```python
if os.environ.get("BENCH_USE_PRETRANSCRIBED") and pretranscribed_path.exists():
    segments = json.loads(pretranscribed_path.read_text())
    # Skip the ASR stage of process_meeting_file and directly create artefact and index it
else:
    #Normal ASR pipeline
```

---

## Part 6: Verification method

After the implementation is completed, execute the following command to verify:

```bash
cd backend
uv run python -m scripts.benchmark rag-chunk-phase1 --output /tmp/phase1_test.json
```

Checkpoint:
1. The Markdown report contains 8 rows of tables (one row per group by default)
2. Each row displays Scoped Recall@10, Unscoped Recall@10, and Combined scores.
3. The JSON output contains the `top_2` field, and the two configurations belong to different methods
4. There is no chunk ID splicing error in the log.

Re-validation Phase 2:
```bash
uv run python -m scripts.benchmark rag-chunk-phase2 --phase1-result /tmp/phase1_test.json
```

Checkpoint:
1. The report contains an 8-row grid (2 chunk configurations × 4 search combinations)
2. Unscoped row contains `File Coverage@10` indicator
3. The final output contains a `recommendation` field indicating the optimal configuration

---

## Appendix: BM25/Vector consistency fix in Parent-Child mode (Option A)

### Problem background

At the time this repair was designed, Parent-Child chunking had serious **retrieval-side inconsistencies**:

- **Vector side**: Retrieve hit child chunk, check back and return **parent chunk** through `_resolve_parent_chunks()`.
- **BM25 side**: `_add_to_bm25()` re-splits the original text and writes it into the child chunk. `_bm25_retrieve()` directly returns the **child chunk** without parent review.
- **ID space inconsistency**: The child ID in Vector is `..._child_{i}_{j}`, and the child ID in BM25 is `..._chunk_{i*1000+j}`.
- **RRF deduplication failure**: `_rrf_dedup_key()` relies on `chunk_id` / `chunk_index` / content hash, parent and child are different in these three dimensions, causing the same semantic content to enter top-k at the same time with two granularities, occupying two positions.

This will cause distortion in the Recall/MRR calculation of Hybrid retrieval in the benchmark, and the golden set cannot match both results at the same time.

### Repair target

Let BM25 also return **parent chunk** in Parent-Child mode, which is exactly the same as the return granularity on the Vector side, to achieve:
1. ID space alignment (both return `..._parent_{i}`).
2. RRF deduplication takes effect (the same parent will not be counted repeatedly).
3. Golden set only needs to mark the parent ID to match both Vector and BM25.

### Modify files and specific logic

#### Modification 1: `_add_to_bm25()` of `backend/src/services/rag/_indexer_store.py`

**Current Issue**: `parent_id` and `chunk_type` are missing in the metadata when BM25 is indexed, and the chunk ID uses an independent `chunk_{index}` format.

**Modified content**:

1. **Unified chunk ID**: In Parent-Child mode, the child chunk ID of BM25 is changed to be consistent with the vector library: `f"{prefix}_child_{i}_{j}"`.
2. **Rich metadata**: Write `"parent_id"` (pointing to the parent chunk ID) and `"chunk_type": "child"` for review during retrieval.

**Modified core logic (pseudocode)**:

```python
def _add_to_bm25(meeting_id: int, text: str, metadata: dict, separators: list[str]) -> None:
    #...The logic of the previous splitter remains unchanged...
    prefix = _chunk_id_prefix(meeting_id, metadata.get("file_id"))
    indexed = 0
    try:
        with get_write_connection() as conn:
            if settings.PARENT_CHILD_ENABLED:
                for i, parent_text in enumerate(parent_chunks):
                    parent_id = f"{prefix}_parent_{i}"
                    for j, child_text in enumerate(child_splitter.split_text(parent_text)):
                        chunk_id = f"{prefix}_child_{i}_{j}"
                        add_bm25_chunk(
                            conn,
                            chunk_id=chunk_id,
                            meeting_id=meeting_id,
                            content=child_text,
                            tokenized="[]",
                            metadata=json.dumps({
                                "meeting_id": meeting_id,
                                "chunk_index": i * 1000 + j,
                                "chunk_type": "child",
                                "parent_id": parent_id,
                                **metadata,
                            }),
                        )
                        indexed += 1
            else:
                # Flat mode keeps the original logic unchanged
                for chunk_text, chunk_index in chunks:
                    chunk_id = f"{prefix}_chunk_{chunk_index}"
                    add_bm25_chunk(
                        conn,
                        chunk_id=chunk_id,
                        meeting_id=meeting_id,
                        content=chunk_text,
                        tokenized="[]",
                        metadata=json.dumps({
                            "meeting_id": meeting_id,
                            "chunk_index": chunk_index,
                            **metadata,
                        }),
                    )
                    indexed += 1
        logger.info("Meeting %d: added %d chunks to FTS5 index", meeting_id, indexed)
    except Exception as e:
        logger.warning("Failed to persist BM25 chunks to database: %s", e)
```

> **Note**: The logic of Flat mode remains unchanged, only the Parent-Child branch is modified.

#### Modification 2: Added a general parent check function in `backend/src/services/rag/_vector.py`

Currently `_resolve_parent_chunks()` tightly couples the return format of Vector retrieval (`list[tuple[Any, float]]`). In order to be reused by BM25, a more general version needs to be added.

**New function**:

```python
def resolve_parent_chunks_by_ids(
    parent_ids: list[str],
    child_scores: dict[str, float],
) -> list[dict]:
    """Given the parent_id list and the corresponding child score, batch check vectorstore to obtain the parent chunk.

    For multiple child hits of the same parent, the one with the best score (lowest distance/highest similarity) is retained.
    """
    if not parent_ids:
        return []
    vectorstore = get_vectorstore()
    try:
        parent_data = vectorstore.get(
            ids=parent_ids,
            include=["documents", "metadatas"],
        )
    except Exception:
        logger.warning("Failed to fetch parent chunks", exc_info=True)
        return []

    out = []
    for idx, content in enumerate(parent_data["documents"]):
        meta = parent_data["metadatas"][idx]
        pid = parent_data["ids"][idx]
        if pid in child_scores:
            out.append({
                "content": content,
                "metadata": meta,
                "score": float(child_scores[pid]),
            })
    return out
```

**Modify the original `_resolve_parent_chunks()`**:

Make it call `resolve_parent_chunks_by_ids()` internally and keep the external interface unchanged:

```python
def _resolve_parent_chunks(
    vectorstore: Any,
    child_results: list[tuple[Any, float]],
    threshold: float | None,
    lower_is_better: bool = True,
) -> list[dict]:
    seen_parents: dict[str, float] = {}
    for doc, score in child_results:
        if threshold is not None:
            if lower_is_better:
                if score > threshold:
                    continue
            elif score < threshold:
                continue
        parent_id = doc.metadata.get("parent_id")
        if not parent_id:
            continue
        if parent_id not in seen_parents or (
            (lower_is_better and score < seen_parents[parent_id])or (not lower_is_better and score > seen_parents[parent_id])
        ):
            seen_parents[parent_id] = score
    return resolve_parent_chunks_by_ids(list(seen_parents.keys()), seen_parents)
```

#### Modification 3: `_bm25_retrieve()` of `backend/src/services/rag/_bm25.py`

**Modification**: Before returning the result, perform parent check on the child chunk hit in Parent-Child mode.

**Determine whether back-checking is required**: `"parent_id"` and `"chunk_type" == "child"` exist in the result metadata.

**Modified core logic**:

```python
def _bm25_retrieve(
    query: str,
    meeting_ids: list[int] | None,
    file_ids: list[int] | None,
    k: int,
    *,
    trace: TraceContext | None = None,
    speaker_names: list[str] | None = None,
) -> list[dict]:
    #... The logic of the previous section fts5_search remains unchanged...

    out = []
    parent_id_to_best_score: dict[str, float] = {}
    child_hits: list[dict] = []

    for r in results:
        try:
            meta = json.loads(r["metadata"]) if r["metadata"] else {}
        except json.JSONDecodeError:
            meta = {"meeting_id": r["meeting_id"]}

        score = float(-r["rank"]) if r["rank"] else 0.0
        parent_id = meta.get("parent_id")

        # Child hit in Parent-Child mode: collect the optimal score and check the parent later
        if parent_id and meta.get("chunk_type") == "child":
            if parent_id not in parent_id_to_best_score or score > parent_id_to_best_score[parent_id]:
                parent_id_to_best_score[parent_id] = score
            continue

        # Flat mode or parent hit: keep it directly
        out.append({
            "content": r["content"],
            "metadata": meta,
            "score": score,
        })

    # Check back parent chunks (if child hits exist)
    if parent_id_to_best_score:
        from ._vector import resolve_parent_chunks_by_ids
        parents = resolve_parent_chunks_by_ids(
            list(parent_id_to_best_score.keys()),
            parent_id_to_best_score,
        )
        out.extend(parents)

    if trace:
        trace.finish_span("bm25_search")
    return out
```

> **Border case handling**:
> 1. If a parent ID in `parent_id_to_best_score` has been deleted in vectorstore (theoretically this should not happen, because BM25 and vectorstore are written synchronously), `resolve_parent_chunks_by_ids()` will naturally skip the ID.
> 2. When the same parent is hit by multiple children, the one with the highest BM25 score is retained (because the BM25 score is "the higher, the better").
> 3. If the vectorstore checkback fails (network/timeout), record a warning and only retain the existing flat/parent results to avoid empty returns.

### Modified expected behavior

| Dimensions | Vector Side | BM25 Side | Consistency |
|------|----------|---------|--------|
| **match object** | child chunk | child chunk | ✅ consistent |
| **return object** | parent chunk | parent chunk | ✅ consistent |
| **chunk ID** | `..._parent_{i}` | `..._parent_{i}` | ✅ Consistent |
| **metadata** | `chunk_type: "parent"` | `chunk_type: "parent"` | ✅ Consistent |
| **RRF Removal** | Normal | Normal | ✅ The same parent will not be repeated |

### Impact on Benchmark

After the fix is complete, in the review of the Parent-Child strategy:
1. **golden set only needs to mark the parent chunk ID** (such as `meeting_42_file_101_parent_3`) to match the results of Vector and BM25 at the same time.
2. Parent + child duplication no longer appears in the result pool of **Hybrid RRF**, and top-k utilization is higher.
3. **Phase 1/Phase 2 indicator calculation** (Recall@10, MRR, NDCG) is more accurate and credible.

---

## Summary

This plan reuses the existing isolation mechanism (`bench_environment`), the existing ingestion pipeline (`process_meeting_file` → AssemblyAI) and the existing retrieval interface (`retrieve()` supports scoped/unscoped; Broad Recall is called file by file through `fair_retrieve_per_file()`) to build a two-stage benchmark for the audio modality. Main extension points:
- Ingestion helper for amicopus `.wav` files
- golden set JSON file with complete chunk ID
- Phase 1/2 evaluation logic, traversing chunk configuration in an isolated environment
- CLI extension for `benchmark.py`
- Pre-transcription fast path to avoid duplicate ASR overhead
- **BM25/Vector consistency fix in Parent-Child mode (Option A)**

All modifications are limited to the `backend/scripts/`, `backend/src/services/rag/` and fixtures directories.
