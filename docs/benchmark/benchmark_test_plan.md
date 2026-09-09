# RAG Benchmark test plan: Chunk strategy × retrieval strategy

> **Design specification, not current production defaults or release evidence.**
> The two-phase audio runner is implemented; current commands and metric
> semantics are documented in
> [`benchmark_config_guide.md`](benchmark_config_guide.md) and
> [`backend/docs/benchmarking.md`](../../backend/docs/benchmarking.md).

## 1. Overview

This document defines a two-stage benchmark for systematically selecting the optimal **Chunk Strategy** and **Retrieval Strategy** combination for each file mode.

- **Phase 1 — Chunk strategy comparison**: The fixed retrieval configuration is `Hybrid + Rerank`, compare the chunk methods in each mode, and select top-2.
- **Phase 2 — Retrieval strategy Grid Search**: For the top-2 chunk strategy of each mode, conduct a grid search of the retrieval configuration in the **Scoped (limited range)** and **Unscoped (Broad Recall)** scenarios respectively.

**Why do it in two stages instead of doing the entire grid at once?**

The Chunk strategy determines the *granularity and metadata richness* of the index units, and the retrieval strategy determines *how to find* these units. There are significant interactive effects between the two (for example: BM25 benefits more from fine-grained chunks; the behavior of the reranker changes with chunk size), but if all combinations are tested in the first stage, the matrix size will be uncontrollable. Phase 1 first reduces the chunk design space, and Phase 2 then evaluates the interaction effects within this space.

---

## 2. Phase 1: Chunk strategy comparison

### 2.1 Design principles

- **Fixed retrieval configuration**: Use `HybridStrategy` + `Reranker ON` as
  this experiment's controlled comparison setting to isolate the impact of the
  chunk itself. The repository runtime default currently leaves reranking
  disabled.
- **Fixed Scope type**: Tested on `Scoped` and `Unscoped` data sets respectively, and finally selected top-2 by weighted average score.
- **Plain text variant**: For structured modalities (video/audio, document), an additional plain text index pipeline needs to be generated, and the extracted `text` field (without segment/page structure) is directly sent to `index_meeting()` for comparison with the native chunk method.

### 2.2 Modal → Native Chunk Mapping

| Modal | File Type | Processor Output | Native Chunk Function | Native Chunk Name |
|------|---------|---------------|----------------|----------------|
| **Video/Audio** | `video`, `audio` | `FileArtefact.segments` (ASR transcript with speaker/timestamp) | `index_meeting_segments()` | **Segment-Aware** |
| **Document** | `pdf`, `ppt`, `doc`, `xls`, `csv` | `FileArtefact.parsed_doc` (text + table + image resources organized by page) | `index_meeting_pages()` | **Page-Aware** |
| **Picture** | `png`, `jpg`, `jpeg`, `webp` | `FileArtefact.segments` (single segment, including caption and OCR text) | `index_meeting_segments()` | **Caption-OCR Flat** |

> **Image description**: The image processor will generate a `segment` containing caption and OCR spliced text. `index_meeting_segments()` will treat it as a semantic group; if the text length exceeds `CHUNK_SIZE`, it will not be further split in a single segment scenario (single segment boundary logic). Therefore image content is usually indexed into **1–2 chunks**.

### 2.3 Chunk methods to be tested for each mode

For each mode, the following **3 chunk methods** are tested:

| Number | Method | Description | Implementation |
|------|------|------|----------|
| A | **Native** | The default chunk path for this mode (see table above) | Use the existing pipeline to execute as is |
| B | **Pure-Text Flat** | Extracted plain text + `RecursiveCharacterTextSplitter` (`PARENT_CHILD_ENABLED=False`) | Feed `FileArtefact.text` (or `parsed_doc.to_text()`) into `index_meeting()` |
| C | **Pure-Text Parent-Child** | Extracted plain text + two-level split (`PARENT_CHILD_ENABLED=True`) | Feed `FileArtefact.text` (or `parsed_doc.to_text()`) into `index_meeting()` and enable parent-child configuration |

### 2.4 Parameter preset

For each method, **3 sets of parameter presets** are tested, covering three chunk granularities of small/medium/large.

#### Flat / Page-Aware / Caption-OCR Flat parameters

| Default | `CHUNK_SIZE` | `CHUNK_OVERLAP` | Description |
|------|-------------|------------------|------|
| **S** (Small) | 512 | 64 | High granularity; large number of chunks; good for keyword/BM25 matching |
| **M** (Medium) | 1024 | 128 | Plan baseline; balanced configuration |
| **L** (Large) | 2048 | 256 | Low granularity; small number of chunks; retain more intra-chunk context |

#### Parent-Child parameter

| Default | Parent `CHUNK_SIZE` | Parent Overlap | Child `CHILD_CHUNK_SIZE` | Child Overlap |
|------|--------------------|----------------|--------------------------|---------------|
| **S** | 1024 | 128 | 256 | 32 | small parent, fine-grained child |
| **M** | 1024 | 128 | 256 | 32 | Same as S (parent-child is less sensitive to parent size) |
| **L** | 2048 | 256 | 512 | 64 | Big parent, big child; fewer total units |

> **Note**: Parent-Child actually only needs to test **S** and **L** two groups (skip M), because child size dominates the retrieval granularity, while parent size mainly affects the use of context window.

#### Segment-Aware Parameters

| Default | `CHUNK_SIZE` (maximum limit per group) | `AUDIO_SEMANTIC_BOUNDARY_ENABLED` | `AUDIO_SEMANTIC_BOUNDARY_THRESHOLD` | Description |
|------|------------------------|-----------------------------------|----------------------------------------|------|
| **S** | 512 | `True` | 0.5 | Small grouping, strict semantic boundaries |
| **M** | 1024 | `True` | 0.5 | Plan baseline; medium grouping, standard boundary sensitivity |
| **L** | 2048 | `False` | — | Large size limit, **turn off semantic boundaries** (pure size-based grouping) |
| **M-Loose** | 1024 | `True` | 0.3 | More bounds → smaller semantic groups |
| **M-Strict** | 1024 | `True` | 0.7 | Fewer boundaries → larger semantic groups |

> **Note**: The core comparisons of Segment-Aware are three groups: **S**, **M**, and **L**. **M-Loose** and **M-Strict** are optional - only add if video/audio is the focused modality and boundary sensitivity is expected to have a significant impact.

### 2.5 Phase 1 test matrix summary

| Modal | Method A (Native) | Method B (Flat) | Method C (Parent-Child) | Number of single runs |
|------|------------------|---------------|-----------------------|----------|
| Video/Audio | Segment-Aware × 3–5 presets | Flat × 3 presets | Parent-Child × 2 presets | **8–10** |
| Documentation | Page-Aware × 3 presets | Flat × 3 presets | Parent-Child × 2 presets | **8** |
| Image | Caption-OCR × 3 presets | Flat × 3 presets | Parent-Child × 2 presets | **8** |

Each set of runs is performed once on both **Scoped** and **Unscoped** datasets.

### 2.6 Top-2 filtering criteria

For each "chunk method + preset" combination for each modal, calculate:

```
Combined_Recall@10 = 0.5 × Scoped_Recall@10 + 0.5 × Unscoped_Recall@10
```Sort by `Combined_Recall@10` from high to low and select the **top-2** default. If two presets belong to the same **method** (for example, both are Flat), only the best preset in that method will be retained, and the next best preset of a different method will be taken. This ensures that the Phase 2 grid covers diverse chunk architectures.

**Tie Breaker**: If Recall@10 is the same, the method with higher `File Coverage@10` (Unscoped only) will be preferred.

---

## 3. Phase 2: Retrieval strategy Grid Search

### 3.1 Why do we need to do Grid Search after Phase 1?

Chunk strategy and retrieval strategy are not independent:

1. **BM25 (Hybrid) benefits from granularity**: fine-grained flat chunks can generate more discrete keyword targets, while large semantic segments have lower keyword density. The chunk method that performs well under Hybrid may not be optimal under Native.
2. **Reranker’s discrimination is affected by chunk size**: Reranker scores based on the complete chunk text. If the chunk is too large, the relevant sentences may be submerged in redundant content, causing the reranker score to be lowered, thus affecting the cutoff judgment.
3. **Parent-Child changes the retrieval unit**: the child chunk is actually retrieved, but the parent chunk provides extended context. Reranking child chunks has different effects than reranking parent chunks; the relative value of reranker to Parent-Child is higher than Flat.
4. **Scope changes the value of Hybrid**: In Scoped mode, the candidate pool is very small (only a few dozen chunks), and there is not much difference between Native and Hybrid; under Unscoped Broad Recall, the candidate pool is global (thousands of chunks), and the value of BM25's exact keyword matching is amplified.

Therefore, it cannot be assumed that the optimal chunk strategy under `Hybrid+Rerank` will still be optimal under `Native+Rerank` or `Hybrid` (without rerank). Must be jointly optimized.

### 3.2 Scoped Grid Search

For each modality, take its **Phase 1 top-2 chunk strategy** and run the following combinations:

| Retrieval Strategy | Reranker | Configuration Items | Description |
|---------|----------|--------|------|
| `VectorStrategy` | OFF | `RAG_RETRIEVER_PROVIDER=vector`, `RERANKER_BINDING=""` | Pure vector baseline |
| `VectorStrategy` | ON | `RAG_RETRIEVER_PROVIDER=vector`, `RERANKER_BINDING=cohere/bge` | vector + rerank |
| `HybridStrategy` | OFF | `RAG_RETRIEVER_PROVIDER=hybrid`, `RERANKER_BINDING=""` | Vector + BM25 (RRF fusion) |
| `HybridStrategy` | ON | `RAG_RETRIEVER_PROVIDER=hybrid`, `RERANKER_BINDING=cohere/bge` | Experimental hybrid + reranker cell; not the repository runtime default |

**Execution method**: Explicitly pass in `meeting_ids=[mid]` (or `file_ids=[fid]`) when calling retrieval to force the Scoped retrieval path.

**Record metrics for each round**:
- `Recall@10`
- `MRR`
- `NDCG@10`

### 3.3 Unscoped (Broad Recall) Grid Search

Use the same **top-2 chunk strategy** and the same **4 retrieval × rerank combinations** as Scoped.

**Unscoped key configuration** (need to remain fixed):
- `RAG_SUMMARY_ROUTER_ENABLED=True` (production default)
- `RAG_FAIR_ADAPTIVE_CHUNKS=True`
- `RAG_FILE_SCOPING_MODE=router_and_funnel` (production broad-recall strategy)

**Execution method**: **Do not pass in** `meeting_ids` or `file_ids` when calling retrieval, triggering the complete Broad Recall path:
1. Summary Router filters candidate files
2. `fair_retrieve_per_file()` retrieves each file independently
3. Over-fetch mechanism takes effect (`per_file_fetch = max(budget*2, budget+2)`)
4. Per-file guarantee truncation takes effect (`min_floor = max(top_k, distinct_files)`)
5. Global rerank and deduplication

**Record metrics for each round**:
- `Recall@10`
- `MRR`
- `NDCG@10`
- **`File Coverage@10`**: What proportion of files containing golden chunks appear in the top-10 results?
- **`Per-File Recall Mean`**: Recall@10 mean value of each related file (fairness indicator)

### 3.4 Phase 2 Grid Search table template

Take the top-2 chunk strategy `{C1, C2}` of a certain mode as an example:

| Chunk Strategy | Search Strategy | Rerank | Scoped Recall@10 | Scoped MRR | Scoped NDCG | Unscoped Recall@10 | Unscoped MRR | Unscoped NDCG | Unscoped File Coverage | **Comprehensive Score** |
|-----------|----------|--------|-------------------|------------|-------------|--------------------|---------------|---------------|------------------------|-------------|
| C1 | Native | OFF | | | | | | | | |
| C1 | Native | ON | | | | | | | | |
| C1 | Hybrid | OFF | | | | | | | | |
| C1 | Hybrid | ON | | | | | | | | |
| C2 | Native | OFF | | | | | | | | |
| C2 | Native | ON | | | | | | | | |
| C2 | Hybrid | OFF | | | | | | | | |
| C2 | Hybrid | ON | | | | | | | | |

**Final selection for each modality**: Calculate the comprehensive score according to the following formula, whichever is the highest:

```
Overall Score = 0.4 × Unscoped_Recall + 0.3 × Scoped_Recall + 0.2 × File_Coverage + 0.1 × NDCG
```

> Weights can be adjusted based on business priorities. If the system is mainly based on Scoped queries, the weight of `Scoped_Recall` can be increased.

---

## 4. Phase 1 → Phase 2 transfer process

```
┌─────────────────────────────────────────────────────────┐
│ PHASE 1: Chunk strategy comparison │
│ Fixed condition: Hybrid + Rerank ON │
│ Dataset: golden_set_scoped.json + golden_set_unscoped.json │
└─────────────────────────────────────────────────────────┘
                              │
                              ▼
        ┌──────────────────────┼─────────────────────┐
        │ │ │
        ▼ ▼ ▼
   ┌─────────┐ ┌─────────┐ ┌──────────┐
   │ Video │ │ Documentation │ │ Pictures │
   │ Top-2 │ │ Top-2 │ │ Top-2 │
   │ Chunk │ │ Chunk │ │ Chunk │
   └────┬────┘ └────┬────┘ └────┬────┘
        │ │ │
        └──────────────────────┼─────────────────────┘
                              ▼┌─────────────────────────────────────────────────────────┐
│ PHASE 2: Search Strategy Grid Search │
│ Variable: Native/Hybrid × Rerank ON/OFF │
│ Dataset: golden_set_scoped.json + golden_set_unscoped.json │
│Per-modal output: Optimal (Chunk + Retrieval + Rerank) combination │
└─────────────────────────────────────────────────────────┘
```

### 4.1 Why can’t we directly select the optimal Chunk and the optimal search separately, and then combine them?

An intuitive shortcut is:
1. First find the optimal chunk strategy (Phase 1).
2. Then independently find the optimal search strategy.
3. Combine the two directly.

This approach is **ineffective** due to the **interaction effect**:

| Interaction effects | Description |
|---------|------|
| **Chunk Size × Hybrid** | BM25 keyword matching works best when the chunk is small and focused. A large chunk of 2048 characters may perform well under Native (the embedding context is more complete), but will fail under Hybrid because BM25 cannot accurately locate keywords in a huge chunk. |
| **Chunk Size × Reranker** | Reranker scores based on the complete chunk text. If the chunk is too large, relevant sentences may be buried, resulting in a decrease in the reranker score and false negatives at the cutoff. |
| **Segment-Aware × Native** | Segment-Aware chunk retains speaker label and timestamp. This structural metadata is useful for embedding similarity calculations (speaker/time queries), but may introduce noise for pure keyword queries (BM25). |
| **Parent-Child × Reranker** | Parent-Child retrieves the child chunk (small granularity), but parent provides extended context. Without reranker, the child chunk may appear too short; with reranker, the child text can be accurately scored. The relative value of reranker to Parent-Child is higher than Flat. |
| **Scope × Strategy** | The candidate pool in Scoped mode is very small (only a few dozen chunks), and there is not much difference between Native and Hybrid. Under Unscoped Broad Recall, the candidate pool is global (thousands of chunks), and the value of Hybrid's exact keyword matching is significantly amplified. The "optimal" retrieval strategy depends on the scope type. |

**Conclusion**: Chunk and retrieval must be jointly optimized. Phase 1 narrows the chunk search space; Phase 2 evaluates interaction effects within this space.

---

## 5. Benchmark data set specification

### 5.1 Data set splitting requirements

**Two separate golden sets** need to be prepared. Never mix scoped and unscoped queries in the same file.

### 5.2 `golden_set_scoped.json`

Each query only targets **one known file/session**, and the golden chunk must be within that scope.

```json
{
  "version": 1,
  "scope_type": "scoped",
  "items": [
    {
      "id": "scoped_q001",
      "query": "What did Alice say about the budget in the Q1 planning meeting?",
      "fixture_file": "sample.pdf",
      "meeting_id": 42,
      "file_id": 101,
      "expected_chunks": [
        "meeting_42_file_101_chunk_3",
        "meeting_42_file_101_chunk_4"
      ],
      "expected_file_ids": [101],
      "expected_answer": "Alice says the budget needs to be increased by 20%."
    }
  ]
}
```

### 5.3 `golden_set_unscoped.json`

Each query does not specify a range. Answers may span multiple documents/sessions. You must explicitly record which files are relevant in order to calculate File Coverage.

```json
{
  "version": 1,
  "scope_type": "unscoped",
  "items": [
    {
      "id": "unscoped_q001",
      "query": "Which meetings discussed the budget increase?",
      "expected_chunks": [
        "meeting_42_file_101_chunk_3",
        "meeting_43_file_102_chunk_1"
      ],
      "expected_file_ids": [101, 102],
      "expected_meeting_ids": [42, 43],
      "expected_answer": "Budget increases were discussed at both the Q1 planning meeting and the all-hands meeting."
    }
  ]
}
```

### 5.4 Data set size recommendations

| Modal | Number of Scoped queries | Number of Unscoped queries | Description |
|------|-------------|----------------|------|
| Video/Audio | 8–12 | 4–6 | Requires speaker-specific and temporal queries |
| Documentation | 12–16 | 6–10 | Mixed single-page detailed query and cross-page comprehensive query |
| Images | 4–6 | 2–4 | Focus on caption-based and OCR-based queries |

**Minimum feasible size**: At least 5 queries per scope type per modal. The greater the number of queries, the better the statistical stability.

### 5.5 Chunk ID naming convention and ambiguity elimination

#### Why must I use the full Chunk ID?

The actual chunk ID in the system is generated by `_chunk_id_prefix(meeting_id, file_id)`, the rules are as follows:

- When there is `file_id`: `meeting_{meeting_id}_file_{file_id}_chunk_{index}`
- When there is no `file_id` (legacy single file meeting): `meeting_{meeting_id}_chunk_{index}`

Under the current multi-file architecture, most chunk IDs contain file_id. If only `"chunk_3"` is written in `golden_set` and then spliced ​​into `meeting_42_chunk_3` by the benchmark script, it will not match the actual ID `meeting_42_file_101_chunk_3`, which will cause the recall calculation to always be 0.

In addition, in the Unscoped scenario, the answer to a query may be distributed in multiple `meeting_id` + `file_id` combinations, and it is completely impossible to distinguish the ownership by just writing `"chunk_3"`.

#### Complete Chunk ID Example

| Scene | Old way of writing (ambiguous) | New way of writing (complete ID) |
|------|----------------|----------------|
| Scoped, single file | `["chunk_3"]` | `["meeting_42_file_101_chunk_3"]` |
| Unscoped, across files | `["chunk_3", "chunk_1"]` | `["meeting_42_file_101_chunk_3", "meeting_43_file_102_chunk_1"]` |
| Legacy None file_id | `["chunk_0"]` | `["meeting_42_chunk_0"]` |

#### How to determine the complete Chunk ID?

Before marking the golden set, first perform an index on the test data, and then query the vectorstore or `bm25_index` table to obtain the actual chunk ID:

```python
from src.services.rag._vectorstore import get_vectorstore
vs = get_vectorstore()
results = vs.get(where={"meeting_id": 42}, include=["metadatas"])
for cid, meta in zip(results["ids"], results["metadatas"]):
    print(cid, meta.get("content_type"), meta.get("file_id"))
```

Or query directly in SQLite:

```sql
SELECT chunk_id, meeting_id, metadata FROM bm25_index WHERE meeting_id = 42;
```

#### Benchmark script modification points

Historical implementation note: the planned change targeted
`backend/scripts/benchmark.py`. The current runner canonicalizes physical
chunk IDs before comparison; the obsolete prefix-splicing example below is
retained only to explain the design transition.

```python#Old splicing logic (needs to be deleted)
# expected = {f"meeting_{meeting_id}_{chunk}" for chunk in item.get("expected_chunks", [])}

# New direct comparison logic
expected = set(item.get("expected_chunks", []))
```

At the same time, the chunk ID in the search results should also be taken directly from `metadata.chunk_id` (if it exists) or directly compared based on the `id` field of the returned results to avoid re-splicing.

---

## 6. Implementation instructions

### 6.1 How to generate plain text variants

For each fixture file, after normal processing is complete, extract its plain text and re-call `index_meeting()` for indexing:

| Modal | Source text | Index function | Key configuration |
|------|--------|---------|---------|
| Video/Audio | `FileArtefact.text` (transcribed text with speaker) | `index_meeting()` | `PARENT_CHILD_ENABLED=False/True` |
| Documentation | `parsed_doc.to_text()` (spliced text of each page) | `index_meeting()` | `PARENT_CHILD_ENABLED=False/True` |
| Image | `FileArtefact.text` (caption + OCR) | `index_meeting()` | `PARENT_CHILD_ENABLED=False/True` |

> **Note**: When testing plain text variants, be sure to delete the native index of the file first (or use an independent temporary vector library) to avoid mutual contamination of index data of different chunk strategies.

### 6.2 Benchmark Runner extension

Historical implementation note: the original runner tested only **Scoped**
retrieval (`meeting_ids=[meeting_id]`). The implemented Phase 1/2 runners now
exercise Unscoped retrieval with the following call shape:

```python
# Unscoped retrieval call (trigger Broad Recall)
results = retrieve(
    query,
    meeting_ids=None, # Do not specify a range
    file_ids=None, # No range specified
    top_k=top_k,
)
```

Or use the full pipeline:

```python
from src.services.chain import ask
result = await ask(question=query, user_id="benchmark") # Do not pass in meeting_ids
```

### 6.3 Configuration locking within a single run

To ensure a fair comparison, the following configurations need to be locked in a single round of benchmark:

| Configuration item | Phase 1 value | Phase 2 value |
|--------|-------------|-------------|
| `TOP_K` | 10 | 10 |
| `RERANKER_TOP_N` | 10 | 10 |
| `HYBRID_ALPHA` | 0.5 | 0.5 |
| `RAG_RERANK_FETCH_MULTIPLIER` | 6 | 6 |
| `RAG_SUMMARY_ROUTER_ENABLED` | True | On demand |
| `RAG_FAIR_ADAPTIVE_CHUNKS` | True | On demand |
| `QUERY_REWRITE_ENABLED` | False | False |

> **Why should we turn off query rewrite? ** Query rewrite introduces LLM calls that may change query semantics. In the retrieval benchmark, the ability of the retrieval system to answer the *original question* should be measured, excluding the interference of query rewrite.

---

## 7. Execution list

### Phase 1 Checklist

- [ ] Prepare `golden_set_scoped.json`, containing scoped queries for each mode
- [ ] Prepare `golden_set_unscoped.json`, containing unscoped queries across files
- [ ] Generate 3 combinations of chunk methods × parameter presets for each mode
- [ ] Run all combinations (Hybrid + Rerank) on the Scoped dataset
- [ ] Run all combinations (Hybrid + Rerank) on Unscoped dataset
- [ ] Calculate Combined Recall and select the top-2 of each mode

### Phase 2 Checklist

- [ ] Configure 4 retrieval × rerank combinations for top-2 of each modality
- [ ] Execute Scoped Grid Search and record Recall/MRR/NDCG
- [ ] Execute Unscoped Grid Search, record Recall/MRR/NDCG + File Coverage + Per-File Recall
- [ ] Select the final optimal configuration of each mode according to the weighted comprehensive score
- [ ] (Optional) Run `rag-snapshot` on the final selected configuration to establish a regression baseline

---

## 8. Example of expected output

### Phase 1 output example (video/audio)

| Ranking | Method | Default | Scoped Recall@10 | Unscoped Recall@10 | Combined | Selected |
|------|------|------|------------------|--------------------|----------|----------|
| 1 | Segment-Aware | M | 0.82 | 0.71 | **0.765** | ✅ Top-1 |
| 2 | Pure-Text Flat | S | 0.78 | 0.68 | 0.730 | ✅ Top-2 |
| 3 | Segment-Aware | L | 0.75 | 0.70 | 0.725 | — |
| 4 | Pure-Text Parent-Child | S | 0.76 | 0.65 | 0.705 | — |

### Phase 2 output example (video/audio)

| Chunk | Search | Rerank | Scoped Rec@10 | Unscoped Rec@10 | File Cov | Comprehensive score | Ranking |
|-------|------|--------|---------------|------------------|----------|----------|------|
| Seg-Aware M | Hybrid | ON | 0.82 | 0.71 | 0.88 | **0.763** | 1 |
| Seg-Aware M | Hybrid | OFF | 0.80 | 0.65 | 0.82 | 0.712 | 2 |
| Flat S | Hybrid | ON | 0.78 | 0.68 | 0.85 | 0.730 | 3 |
| Flat S | Native | ON | 0.72 | 0.55 | 0.70 | 0.617 | 4 |

**Final recommended configuration for video/audio mode**: `Segment-Aware (M) + Hybrid + Rerank ON`.
