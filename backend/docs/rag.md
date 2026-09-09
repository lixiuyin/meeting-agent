# RAG Architecture, Principles, and Optimization Guide

> This document is a single authoritative document for the Meeting Agent RAG subsystem, covering **Architecture Overview → Indexing (Chunking) → Retrieval (Retrieval) → Reranking (Rerank) → Post-processing → Configuration Reference → Optimization Direction**.
>
> Code location:
> - `backend/src/services/rag/` — RAG infrastructure (vector library, indexing, retrieval, reordering, query rewriting, funnel filtering, scope routing, etc.)
> - `backend/src/services/chain/` — orchestration layer (routing, context assembly, LCEL generation, streaming events, trace)
> - `backend/src/core/config.py` — configuration aggregation (YAML + env)
> - `backend/config/main.yaml` — Default YAML configuration

---

## Contents

1. [Overall Architecture](#1-overall-architecture)
2. [Configuration system and switch matrix](#2-configuration-system-and-switch-matrix)
3. [Index Process (Chunking)](#3-indexing-process-chunking)
4. [Retrieval process (Retrieval)](#4-retrieval-process-retrieval)
5. [Rerank](#5-reranking-process-rerank)
6. [Post-processing: Duplication removal & context assembly](#6-post-processing-duplication--contextual-assembly)
7. [Query rewriting and adaptive top-k](#7-query-rewriting-and-adaptive-top-k)
8. [Configuration Reference Table](#8-configuration-reference-table)
9. [Scenario Configuration Template](#9-scenario-configuration-template)
10. [Performance Characteristics](#10-performance-characteristics)
11. [Troubleshooting](#11-troubleshooting)
12. [Optimization direction (continuous evolution)](#12-optimization-direction-continuous-evolution)

---

## 1. Overall architecture

The RAG pipeline is split into two layers:

| Level | Position | Responsibilities |
| --- | --- | --- |
| **RAG Infrastructure** | `services/rag/` | Vector library singleton, indexing, retrieval, reordering, query rewriting |
| **Chain Orchestration Layer** | `services/chain/` | Routing, context assembly, LCEL generation, streaming events, trace |

The two layers pass state through `PipelineContext` / `PipelineResult` (`chain/_context.py`). The Chain layer splits "Rewrite → Retrieval → Reranking → Deduplication → Context Assembly → Generation" into steps that can be traced independently in the form of `_steps_*`:

- `chain/_steps_session.py` — session/query rewriting
- `chain/_steps_retrieve.py` — retrieval + reordering + near-duplicate suppression
- `chain/_steps_context.py` — long-term memory / knowledge graph / session summary / web search / conversation history assembly
- `chain/_steps_generate.py` — construct the prompt, run the LCEL chain, save messages, and commit durable fact-extraction work
- `chain/_routing.py` — intent classification (`casual` vs `rag`)
- `chain/_formatting.py` — sources extraction
- `chain/_api.py` — Top-level `ask()` / `ask_stream()` entry

### 1.1 End-to-end process (take `ask_stream` as an example)

```
User Query
   │
   ▼
[Routing] `_routing.py` determines `casual` / `rag` (the colloquial "retrieval" in the document is the `rag` in the code)
   │
   ▼
[Query Rewrite] rag/_query.py
   - Short questions & no pronouns → Skip
   - Otherwise call lightweight LLM (QUERY_REWRITE_MODEL) override
   │
   ▼
[Query Plan] rag/_query_plan.py
   - Parse speaker/time/scope constraints from the original question
   - Keep semantic rewrites and lexical expressions separate
   - Publish the immutable plan before document, memory, entity and history
     branches fan out, so every branch observes identical temporal semantics
   │
   ▼
[Retrieve] chain/_steps_retrieve.py → rag/_retriever.py
   - adaptive top_k (determine_adaptive_top_k)
   - When Reranker is enabled over-fetch = top_k * RAG_RERANK_FETCH_MULTIPLIER
   - Optional Multi-Query: each variant has an independent scope+query candidate cache key
   - Meeting-summary routing is a soft prior with a reserved global exploration quota
   - Single or Hybrid (Vector + BM25/FTS5) + RRF fusion
   │
   ▼
[Rerank] rag/_reranker.py
   - Cohere SDK / generic Cohere-compatible HTTP API / local BGE Cross-Encoder
   - scope-aware reranker score cutoff (`RERANKER_SCOPED_MIN_SCORE` / `RERANKER_UNSCOPED_MIN_SCORE`)
   - Small meeting-domain priors prefer decision logs/minutes for decisions and
     actions, and minutes/transcripts for meeting summaries; relevance remains
     dominant
   │
   ▼
[Dedup] chain/_steps_retrieve.py suppress_near_duplicates()
   - 4-gram overlap rate ≥ 0.85 is considered a duplicate
   │
   ▼
[Context Assembly] _steps_context.py
   - Inject long-term memory, knowledge graph entities, session summaries, web search results, and conversation history
   │
   ▼
[Generate] _steps_generate.py
   - LCEL chain → LLM generation/streaming
   - StreamBus pushes step/token/sources/trace/done event
   - Persist the answer, then enqueue durable fact/entity extraction
```

### 1.2 Core design principles

1. **Best-effort Downgrade** — Failure of any optional component (reranker, query rewrite, hybrid, multi-query, memory, KG, web search) should not crash the main process. Instead, use `logger.warning(..., exc_info=True)` + return in the original order.
2. **Thread-safe singleton** — All heavy objects (Chroma, LLM, Embeddings, Reranker, Cohere Client, Rewrite LLM) use **double-checked locking** and can be reset when settings change.
3. **Deterministic ID & Idempotent Upsert** — The default prefix is ​​`meeting_{meeting_id}_file_{file_id}_chunk_{i}` (if there is no `file_id`, it is `meeting_{meeting_id}_chunk_{i}`); there are also parent/child ids distinguished by chunking strategy (see `_indexer.py`). Support `delete_meeting_chunks(meeting_id, file_id=...)` precise deletion.
4. **Non-blocking** — Vector retrieval, BM25, LLM calls, and rerank all go through `asyncio.to_thread()` to protect the FastAPI event loop.
5. **Trace Observable** — `TraceContext` opens/closes span by step (`chunk`, `embed`, `vectorstore_upsert`, `retrieve`, `rerank`, `suppress_near_duplicates`...), and supports benchmark and online diagnosis.
6. **Contextual retrieval, clean citations** — Native vector and BM25 index
   text is prefixed with stable meeting/file/section/speaker/time metadata plus
   an editable meeting-material role and approval state. Filename inference is
   only the initial role; users can review it in the Materials UI. Updating a
   role or approval state queues an immutable-generation native reindex.
   Parent-child indexes also attach a bounded parent-context hint so short
   follow-ups and pronouns retain their local topic during retrieval.
   `retrieval_context_prefix_len` removes that prefix before reranking output,
   prompt citation text, API sources, and UI previews. The contextual retrieval
   version is part of the index fingerprint, so old and new embeddings cannot
   be mixed silently.
7. **Meeting authority semantics** — `transcript`, `minutes`, `agenda`,
   `decision_log`, and `attachment` remain distinct from `unreviewed`, `draft`,
   `reviewed`, `approved`, and `rejected`. Retrieval priors are reduced for
   drafts; rejected material is excluded unless the query explicitly asks about
   rejected or withdrawn content. Current SQLite review state is overlaid after
   retrieval, so stale vector metadata cannot bypass the policy while a reindex
   is running. Role and approval labels are exposed to the model as evidence data.
8. **Strict explicit constraints** — speaker and timestamp filters fail closed.
   An explicit time range with no overlapping chunk returns no evidence instead
   of silently widening to the full meeting. File ownership, deletion, rejection,
   and `known_at` system-time visibility are revalidated after every retrieval and
   retry path.

---

## 2. Configuration system and switch matrix

### 2.0 Public retrieval modes

The supported per-request interface is `retrieval_profile`, not a bundle of
manual thresholds:

| Profile | Query rewrite | Multi-query | Reranker | Default result budget |
| --- | --- | --- | --- | --- |
| `fast` | off | off | off | at most 5 |
| `balanced` | configured production defaults | configured default | configured default | configured default |
| `thorough` | on | on | enabled when configured | at least 16 |

The frontend omits `top_k` by default so the chosen profile remains
authoritative. `top_k` is retained only as a backwards-compatible expert API
override. Profiles are captured in an immutable request snapshot and never
mutate process-global settings.
The base configuration is captured atomically before profile bounds are
derived, so a concurrent settings reload cannot mix thresholds from two
configuration generations.

Retrieved documents, summaries, web results, and memory text are untrusted
prompt data. Context assembly escapes structural markup before placing them in
trusted XML-like sections; the system prompt separately instructs the model not
to execute instructions found inside those sections.

### 2.1 Three levels of priority

```
config/main.yaml (non-secret default: model name, RAG parameters, upload limit)
        ↓ merged by
.env (secrets, env overrides)
        ↓ merged by
OS environment variables (highest priority)
        ↓
src/core/config.py (pydantic-settings aggregation)
```

### 2.2 RAG key switch (`main.yaml` default value)

| Switch                           | Default  | Main Effects                                             |
| -------------------------------- | -------- | -------------------------------------------------------- |
| `SEMANTIC_CHUNKING_ENABLED`      | false    | Enable structure-aware splitting                         |
| `PARENT_CHILD_ENABLED`           | false    | Enable parent-child double-layer slicing (small-to-big)  |
| `HYBRID_SEARCH_ENABLED`          | true     | Enable explicit vector + BM25 + RRF retrieval             |
| `MULTI_QUERY_ENABLED`            | false    | Generate query variant multi-way retrieval               |
| `QUERY_REWRITE_ENABLED`          | true     | LLM rewrite query                                        |
| `RERANKER_BINDING`               | `""` (disabled) | `cohere` / generic `http` / local `bge` / disabled       |
| `DISTANCE_METRIC`                | l2       | `l2` / `cosine`                                          |
| `RAG_RETRIEVER_PROVIDER`         | `hybrid` | `vector` / `hybrid` / `multimodal` / `hybrid_multimodal` |
| `RAGANYTHING_ENABLED`            | false    | Enable multimodal retrieval of RAGAnything               |
| `RAGANYTHING_FALLBACK_TO_NATIVE` | true     | Downgrade to vector/hybrid when RAGAnything fails         |

`main.yaml` enables hybrid retrieval, query rewriting, hierarchical funnel,
and summary routing, but leaves reranking opt-in. `.env` or environment
variables may override these defaults. The precedence rules in
[`configuration.md`](./configuration.md) and the final runtime `Settings()`
values are authoritative.

---

## 3. Indexing process (Chunking)

Entry: `rag/_indexer.py:index_meeting(meeting_id, text, metadata, trace)`

Branch to `_index_flat()` or `_index_parent_child()` depending on `PARENT_CHILD_ENABLED`, then always mirror the same logical chunks through `_add_to_bm25()`. The BM25 mirror is maintained even when hybrid retrieval is disabled so availability fallback and later hot enablement do not require reindexing.

### 3.1 Delimiter level

`_SEPARATORS` cascades from high-level semantic boundaries to lower-level boundaries
(`RecursiveCharacterTextSplitter` tries them from left to right):

```python
_SEPARATORS = [
    "\n\n---\n\n", # Paragraph separator (Markdown horizontal line)
    "\n\n", # Paragraph
    "\n", # lines
    ".", # Chinese period
    "!", # Chinese exclamation mark
    "?", # Chinese question mark
    "\uFF1B", # Full-width semicolon separator
    ". ", # English period
    "! ", # English exclamation mark
    "? ", # English question mark
    " ", # word boundary
    "", # Character-level fallback
]
```

Covers Chinese and English writing conventions and ensures that chunking prioritizes semantic boundaries rather than mid-sentences.

### 3.2 Flat Chunking (`_index_flat`)

**Default parameters**: `CHUNK_SIZE_TOKENS=384`, `CHUNK_OVERLAP_TOKENS=64`.
`CHUNK_SIZE` / `CHUNK_OVERLAP` remain compatibility inputs for older deployments,
but current vector and BM25 indexes use the language-neutral token budgets. These
fields are part of the index fingerprint, so changing them requires reindexing.

Process:

1. If `SEMANTIC_CHUNKING_ENABLED=True`:
   - First `_split_by_structure(text, max_chunk_size=CHUNK_SIZE_TOKENS)` makes a rough cut at topic boundaries.
   - Segments that still exceed the token budget are split again with `RecursiveCharacterTextSplitter(length_function=count_tokens)`.
2. Otherwise, go directly to the token-counted recursive splitter with the configured 384/64 token budget.
3. Construct `Document(page_content, metadata)`:
   ```python
   metadata = {
       "meeting_id": meeting_id,
       "chunk_index": i,
       "file_id": metadata.get("file_id"),
       "file_type": metadata.get("file_type"),
       ...the rest of the metadata,
   }
   ```
4. `prefix = meeting_{meeting_id}_file_{file_id}` (`meeting_{meeting_id}` when there is no `file_id`), and then generate
   `f"{prefix}_chunk_{i}"` - Deterministic IDs are the cornerstone of idempotent upsert and delete-by-meeting/file.
5. Leave it to `_dedup_existing_chunks()` to filter the unchanged chunks, and then go to `_upsert_with_trace()`.

#### 3.2.1 Structure-aware splitting `_split_by_structure`

Location: `rag/_chunkers.py`

Regular `_TOPIC_BREAK_PATTERNS` identifies the natural topic boundaries of meeting minutes:

| Type | Regular snippet | Example |
| --- | --- | --- |
| Markdown title | `^#{1,4}\s+` | `## Technical discussion` |
| Ordered list | `^\d+[\.\)]\s+` | `1. Introduction`, `2) Solution` |
| Bullet uppercase | `^[-*]\s+[A-Z]` | `- Action items` |
| Horizontal line | `^-{3,}$`, `^={3,}$` | `---` / `===` |
| Speaker tag | `^Speaker\s+\d` | `Speaker 1` |
| Chinese section | `^[.*?]` | `[Meeting Minutes]` |
| Chinese meeting header | `^(Meeting\|Discussion\|Summary\|Resolution\|Topic)[:]` | `Topic: Q2 Planning` |

Algorithm:

```python
lines = text.split("\n")
segments: list[str] = []
current: list[str] = []

for line in lines:
    if _TOPIC_BREAK_PATTERNS.match(line) and current:
        segments.append("\n".join(current))
        current = [line]
    else:
        current.append(line)
segments.append("\n".join(current))

# Greedily merge small segments to max_chunk_size
merged, buffer, buffer_len = [], [], 0
for segments in segments:
    if buffer and buffer_len + len(seg) + 1 > max_chunk_size:
        merged.append("\n".join(buffer))
        buffer, buffer_len = [seg], len(seg)
    else:
        buffer.append(seg)
        buffer_len += len(seg) + 1
if buffer:
    merged.append("\n".join(buffer))

return merged if merged else [text]
```

**Advantages**: Preserve topic cohesion; **Limitations**: Regularization depends on document format, free text benefits are limited, and regularization needs to be adjusted for specific fields.

### 3.3 Parent-Child Chunking (`_index_parent_child`)

Enable condition: `PARENT_CHILD_ENABLED=True`

Parameters:

- Parent block: `CHUNK_SIZE_TOKENS=384`, `CHUNK_OVERLAP_TOKENS=64`
- Sub-chunk: `CHILD_CHUNK_SIZE_TOKENS=160`, `CHILD_CHUNK_OVERLAP_TOKENS=24`

Structure (storage in Chroma):

```
Parent: meeting_1_parent_0 chunk_type="parent"
  ├─ Child: meeting_1_child_0_0 chunk_type="child", parent_id="meeting_1_parent_0"
  ├─ Child: meeting_1_child_0_1 chunk_type="child", parent_id="meeting_1_parent_0"
  └─...
Parent: meeting_1_parent_1
  └─...
```

**Retrieval Behavior**:

- `_build_filters` automatically appends `{"chunk_type": "child"}` to ensure that vector search only hits neutron chunks (more precise).
- After hitting the sub-chunks, `_resolve_parent_chunks()` is used to remove duplicates according to `parent_id`, retain the best score, pull the parent chunks in batches (more complete), and return them to LLM.
- This is the classic **small-to-big** strategy: small for retrieval, big for context.
- Cost: The amount of embedding is approximately doubled (the parent also needs to be embed, because `_upsert_with_trace` embeds all docs uniformly).

### 3.4 Deduplication and incremental index `_dedup_existing_chunks`

Location: `_indexer.py:37`

```python
def _content_hash(text: str) -> str:
    normalized = " ".join(text.lower().split())
    return hashlib.sha256(normalized.encode()).hexdigest()[:12]

existing = vectorstore.get(ids=ids, include=["documents"])
existing_map = {eid: _content_hash(doc) for eid, doc in zip(existing["ids"], existing["documents"])}

new_docs, new_ids = [], []
for doc, chunk_id in zip(docs, ids):
    if existing_map.get(chunk_id) == _content_hash(doc.page_content):
        continue
    new_docs.append(doc)
    new_ids.append(chunk_id)
```

Earnings:- **Repeated ingestion of the same file will not be re-embed** (saving API fees/GPU time).
- **Only write to the changed chunk** to reduce write amplification.

**Known limitations**:
- `_dedup_existing_chunks` uses `(chunk_id, sha256(normalized_page_content)[:12])` to determine whether to skip; `chunk_id` already contains `meeting_id` + `file_id` (see `_chunk_id_prefix`), **The same text in different files will not share chunk id**.
- The hash **does not contain** other metadata (if page number and title changes are not reflected in the text, the old vector may still be used); see §12 for optimization discussion.

### 3.5 Upsert + Trace `_upsert_with_trace`

```python
# Step 1: embed
trace.start_span("embed", "index")
embeddings = get_embeddings().embed_documents([d.page_content for d in docs])
trace.finish_span("embed")

# Step 2: upsert (directly use Chroma underlying collection to avoid LangChain packaging overhead)
trace.start_span("vectorstore_upsert", "index")
vectorstore._collection.upsert(
    ids=ids,
    documents=[d.page_content for d in docs],
    embeddings=embeddings,
    metadatas=[d.metadata for d in docs],
)
trace.finish_span("vectorstore_upsert")
```

The two spans are timed separately, making it easier for the benchmark to distinguish between embed and library writing bottlenecks.

### 3.6 BM25/FTS5 mirror index `_add_to_bm25`

The same text is always mirrored and written to SQLite's `bm25_index` table. Normal mode uses
flat `RecursiveCharacterTextSplitter`; BM25 also uses parent/child when `PARENT_CHILD_ENABLED=True`
Granularity to be consistent with vector paths:

```python
add_bm25_chunk(
    conn,
    chunk_id=f"{prefix}_chunk_{i}",
    meeting_id=meeting_id,
    content=chunk,
    tokenized="[]", #The actual tokenize is handed over to FTS5
    metadata=json.dumps({"meeting_id": ..., "chunk_index": i, ...}),
)
```

SQLite triggers automatically synchronize inserts/deletions from the `bm25_index` table to the `chat_messages_fts`/`bm25_fts` FTS5 virtual table (see `core/database/bm25.py`).

**Note**:
- BM25 reuses `_SEPARATORS` and parent/child configuration, but ordinary flat paths and vector indexes are not used
  The semantics of `_split_by_structure` are rough; the parsed page/segment document is passed through `_add_docs_to_bm25()`
  Mirror the chunks it has generated.
- `tokenized="[]"` is a placeholder, FTS5 uses the default tokenizer (`unicode61` or `porter`) to segment words.
- Query sanitization converts FTS operators and punctuation to token
  boundaries instead of deleting them. For example, `ZXQ-4817` becomes the
  equivalent of `"ZXQ" OR "4817"`; joining it into `ZXQ4817` would not match
  the two tokens emitted by FTS5. The same rule is used for cross-session chat
  search.

### 3.7 Delete `delete_meeting_chunks`

```python
def delete_meeting_chunks(meeting_id: int, file_id: int | None = None) -> None:
    where = {"meeting_id": meeting_id}
    if file_id is not None:
        where["file_id"] = file_id
    vectorstore.delete(where=where)
    _remove_from_bm25(meeting_id, file_id=file_id)
```

- The vector side supports precise deletion of `(meeting_id, file_id)`.
- The BM25 side also supports deletion by `meeting_id` or `(meeting_id, file_id)`; file-level deletion will match
  File reindex lock prevents orphan entries from being created concurrently with reconstruction and deletion.

---

## 4. Retrieval process (Retrieval)

Entry: `rag/_retriever.py:retrieve(query, meeting_ids, file_ids, top_k, fetch_multiplier, file_types, date_from, date_to, rag_mode)`

### 4.1 Strategy pattern `_strategies.py`

The retrieval layer uses an explicit strategy selected by `RAG_RETRIEVER_PROVIDER` (`vector` / `hybrid` / `multimodal` / `hybrid_multimodal`; legacy `native` maps to `vector`):

| policy class               | provider value      | behavior                                                                 |
| -------------------------- | ------------------- | ------------------------------------------------------------------------ |
| `NativeStrategy`           | `vector`            | Vector retrieval, with user-scoped BM25 only as an availability fallback |
| `HybridStrategy`           | `hybrid`            | Vector + BM25 RRF fusion                                                 |
| `MultimodalStrategy`       | `multimodal`        | RAGAnything multi-modal retrieval, with vector fallback                  |
| `HybridMultimodalStrategy` | `hybrid_multimodal` | Vector + RAGAnything two-way RRF fusion                                  |

`select_strategy()` returns the corresponding strategy instance based on the normalized provider string. Each strategy implements the `RetrievalStrategy` protocol (`name` + `retrieve()` method).

`MultimodalStrategy` and `HybridMultimodalStrategy` use vector/hybrid retrieval for meeting/file-scoped queries because RAGAnything does not support precise scope filtering. For unscoped queries, all branches—including RAGAnything, summary routing, funnel selection, and BM25—enforce the authenticated `user_id`.

### 4.2 Filter construction `_build_filters`

```python
clauses = []
has_scope_ids = bool(meeting_ids or file_ids)
if settings.PARENT_CHILD_ENABLED and not has_scope_ids:
    clauses.append({"chunk_type": "child"})
if meeting_ids:
    clauses.append({"meeting_id": {"$in": meeting_ids}})
if file_ids:
    clauses.append({"file_id": {"$in": file_ids}})
if file_types:
    clauses.append({"file_type": {"$in": file_types}})
if date_from:
    clauses.append({"meeting_date": {"$gte": int(date_from.strftime("%Y%m%d"))}})
if date_to:
    clauses.append({"meeting_date": {"$lte": int(date_to.strftime("%Y%m%d"))}})
```

Key points:
- Accepts the `file_ids` parameter and supports precise filtering by file.
- Dates are stored as `YYYYMMDD` int to support `$gte / $lte` (Chroma does not support numeric comparisons for strings).
- Single clauses are returned directly without the `$and` wrapper.
- parent-child filtering is only appended when there is no scope ids, and `chunk_type=child` is not forced when there is a scope.

### 4.3 Vector retrieval `_vector_retrieve`

```python
results = vectorstore.similarity_search_with_score(query, k=k, filter=filters)
is_cosine = settings.DISTANCE_METRIC == "cosine"
```

**Distance metric semantics**:

| Metric | Score direction | Threshold meaning |
| --- | --- | --- |
| `l2` (default) | lower is better | `score > threshold` to be filtered |
| `cosine` | The current implementation is based on distance score processing, the lower the better | `score > threshold` is filtered |

Filtering is skipped when `threshold=None` (hybrid fusion paths do this).

These directions apply only inside the vector adapter while filtering and resolving parent chunks. Before `retrieve()` returns, distance scores are converted with `1/(1+raw)` and every result is marked `score_kind=relevance`. BM25, RRF, funnel, multimodal and reranker paths use the same public higher-is-better contract.

**Parent-Child resolution** `_resolve_parent_chunks`:

```pythonseen_parents: dict[str, float] = {} # parent_id -> best score
for doc, score in child_results:
    parent_id = doc.metadata.get("parent_id")
    if not parent_id:
        continue
    if parent_id not in seen_parents or _better(score, seen_parents[parent_id], is_cosine):
        seen_parents[parent_id] = score

parent_data = vectorstore.get(ids=list(seen_parents.keys()), include=["documents", "metadatas"])
return [{"content": c, "metadata": m, "score": seen_parents[pid]} for c, m, pid in ...]
```

### 4.4 Hybrid Retrieve + RRF `_hybrid_retrieve`

```python
def _hybrid_retrieve(query, filters, fetch_k, top_k, threshold):
    meeting_ids = _extract_in_filter(filters, "meeting_id")
    file_ids = _extract_in_filter(filters, "file_id")
    vector_results = _vector_retrieve(query, filters, fetch_k, threshold=None) # No filtering
    bm25_results = _bm25_retrieve(query, meeting_ids, file_ids, fetch_k)
    return _rrf_merge(vector_results, bm25_results, top_k)
```

- **No threshold filtering is performed on the vector side** before fusion, ensuring that documents hit by BM25-only get a fair rank in RRF.
- BM25 side accepts `meeting_ids` and `file_ids` filters.

**RRF formula** (`_rrf_merge`):

```python
score(doc) = α / (k + rank_vec + 1) + (1-α) / (k + rank_bm25 + 1)
```

Among them:
- `k` comes from `RRF_K_PARAM`; `_adaptive_k()` will adapt when `fetch_k` is passed in:
  `fetch_k <= 10` use `max(base_k // 3, 10)`, `fetch_k <= 30` use
  `max(base_k // 2, 20)`, otherwise use `base_k`. funnel file-level merging is additionally controlled by `RAG_FUNNEL_RRF_K`.
- `α = HYBRID_ALPHA` (default 0.5, equal weight)
- `rank_*` is the 0-based ranking of the document in each path

**Score normalization**: Each path first divides the unweighted reciprocal-rank
term by `1 / (k + 1)`, then applies `α` or `1-α`. Keeping the weight outside
the denominator is essential; otherwise it cancels and `HYBRID_ALPHA` cannot
change the ranking. The fused set is finally divided by its top score. Thus the
output maximum is 1, but it is a relative rank score—not a calibrated
probability or answer-confidence signal:

```python
max_score = merged[0][1]
return [{**doc_map[key], "score": score / max_score} for key, score in merged]
```

**`_rrf_merge_multi`** is used to merge more than two result lists (such as merging vectors + RAGAnything in `hybrid_multimodal` mode):

```python
def _rrf_merge_multi(result_lists: list[tuple[list[dict], float]], top_k, k=60):
    # result_lists: [(results, weight), ...]
    for results, weight in result_lists:
        for rank, doc in enumerate(results):
            key = _rrf_dedup_key(doc)
            rrf_scores[key] = rrf_scores.get(key, 0.0) + weight / (k + rank + 1)
```

It is also normalized by the highest theoretical score of each path and the highest score of the combined result.

**Deduplication key `_rrf_dedup_key`**:

```python
meta = doc.get("metadata") or {}
if meta.get("chunk_id"):
    return str(meta["chunk_id"])
return hashlib.sha256(
    " ".join(doc.get("content", "").lower().split()).encode()
).hexdigest()[:32]
```

The current policy gives priority to using `metadata.chunk_id`; if missing, the first 32 bits of SHA-256 are calculated on the normalized text.
`meeting_id + chunk_index` is no longer used because the metadata schema of the vector and BM25 may be different and it is easy to merge incorrectly.

### 4.5 BM25 Retrieval `_bm25_retrieve`

```python
def _bm25_retrieve(query, meeting_ids, file_ids, k):
    with get_connection() as conn:
        results = fts5_search(conn, query, meeting_ids=meeting_ids, file_ids=file_ids, limit=k)

for r in results:
    meta = json.loads(r["metadata"]) if r["metadata"] else {"meeting_id": r["meeting_id"]}
    # FTS5 rank is a negative BM25 score; taking negative values makes "bigger is better"
    out.append({"content": r["content"], "metadata": meta, "score": float(-r["rank"])})
```

- Accepts the `file_ids` parameter and supports precise filtering by file.
- FTS5 has built-in BM25, and the `rank` field itself is a **negative** BM25 score, which must be explicitly negative.
- FTS syntax characters are treated as separators, not concatenated away, so
  punctuation-heavy ticket and incident identifiers preserve their token
  boundaries.
- Failure to downgrade returns `[]`, and the entire hybrid link becomes a pure vector.

### 4.6 Sibling Co-Retrieval `retrieve_sibling_chunks`

Location: `rag/_retriever.py`

```python
def retrieve_sibling_chunks(docs, *, max_per_anchor=1, max_total=4):
    """Fetch sibling multimodal chunks from the same file/page as top hits."""
```

After the main retrieval is completed, multi-modal sibling chunks (tables, picture descriptions, OCR, etc.) on the same page/file as the hit chunks are pulled from the database. Switch controlled by `RAG_SIBLING_CORETRIEVE_ENABLED` (default `True`).

Parameters:
- `max_per_anchor`: Each anchor chunk can pull up to several brothers (`RAG_SIBLING_CORETRIEVE_PER_ANCHOR`, default 1)
- `max_total`: The upper limit of the total number of brothers (`RAG_SIBLING_CORETRIEVE_MAX_TOTAL`, default 4)

Sibling lookup is queried from the database using `get_page_sibling_chunks()` or directly from the Chroma metadata match via `_vector_sibling_fallback()` if the database has no results.

### 4.7 BM25 drift detection and reconstruction

Location: `rag/_retriever.py`

**`check_and_rebuild_bm25_if_drifted()`**: Detect data drift between FTS5 and Chroma at startup. Rebuild is automatically triggered when the row count difference exceeds a configurable threshold (`BM25_DRIFT_THRESHOLD`, default 10%).

**`rebuild_bm25_from_chroma(force=False)`**: Rebuild FTS5 index from existing Chroma data. Default to only rebuild when FTS5 is empty; always rebuild when `force=True` (for drift detection scenarios). Skip chunks with `chunk_type=parent`.

### 4.8 Multi-Query (Scoped/Broad)

Location: `chain/_steps_retrieve.py:_generate_query_variants` + `retrieve_documents`

Trigger conditions:
- `MULTI_QUERY_ENABLED=True`
- **Non-simple query** (short questions are not worth multi-way expansion)
- broad recall must also be turned on `RAG_BROAD_RECALL_MULTI_QUERY_ENABLED`; the switch is broad
  Mode-independent kill-switch, turned off by default to control cost and repetitive wide-fetch.

process:1. Generate `MULTI_QUERY_COUNT=3` variants using the main LLM:
   ```
   "Generate {n} alternative phrasings of the following question for search purposes.
    Each variant should capture the same intent but use different words or angles.
    Return ONLY a JSON array of strings, no explanation."
   ```
2. Original query + variants (usually 4 in total) are retrieved in parallel, each query is
   `max(effective_k // len(queries), 3) * fetch_multiplier` allocates budget; each variant can still go
   Hybrid + RRF.
3. Scoped mode merges variant results through `_dedup_docs` to retain better scores for the same document.
4. Broad mode runs meeting/file scope selection independently for each variant, and then uses file-level RRF (or configured zigzag)
   Merge, then enter fair per-file retrieval; `BroadRecallContext` reuses the same wide-fetch as much as possible.
5. The merged result is truncated and handed over to pre-rerank dedup and reranker.

Therefore Multi-Query and Hybrid are not mutually exclusive; their cost is additional variant generation and retrieval, and whether they are enabled should be passed
The benchmark verifies recall, latency and token cost on the corpus.

### 4.9 Fair Per-File Retrieval `_fair_retriever.py`

Location: `rag/_fair_retriever.py`

When using broad recall (without explicit file_ids), `fair_retrieve_per_file()` guarantees that every file in the scope can contribute a chunk:

```python
async def fair_retrieve_per_file(
    query: str,
    scope_file_ids: list[int],
    *,
    chunks_per_file: int | dict[int, int] = 2,
    cached_docs: dict[int, list[dict]] | None = None,
) -> list[dict]:
```

- For each file in `scope_file_ids`, call `retrieve()` and qualify `file_ids=[file_id]`, `top_k=chunks_per_file`
- `chunks_per_file` can be a uniform int or a `dict[int, int]` (allocate different budgets by file ID)
- `cached_docs` supports fetching directly from the wide-fetch cache, skipping Chroma calls
- Concurrency is controlled by the `settings.RAG_FAIR_CONCURRENCY` semaphore
- The results are based on `chunk_id → (meeting_id, file_id, chunk_index) → content sha1` three-level deduplication

### 4.10 Funnel Score Aggregation `_funnel.py`

Location: `rag/_funnel.py`

Provides score aggregation of chunk → file → meeting, which is the core of funnel narrow:

| Function                     | Purpose                                                                                                                       |
| ---------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `aggregate_by_meeting()`     | Chunk scores are aggregated by meeting_id and return top-N meetings                                                           |
| `aggregate_by_file_scored()` | Chunk scores are aggregated by file_id, returning `(file_id, score)` pairs; support title prior, chunk-count fairness factors |
| `normalize_scores()`         | Normalize by per-document score provenance; legacy untagged L2 distance uses `1/(1+s)`                                      |
| `fetch_title_priors()`       | SQL queries the matching degree between the conference title/description and the query token, and returns boosting            |
| `fetch_file_title_priors()`  | File-level title prior, including full-match bonus                                                                            |
| `restrict_pool()`            | Filter document pool by meeting_ids / file_ids (immutable pattern)                                                            |

The aggregation method is controlled by `RAG_FUNNEL_AGGREGATION`: `top_k_mean` (default, takes top-K mean), `max`, `count`.

### 4.11 Funnel Narrow `_funnel_narrow.py`

Location: `rag/_funnel_narrow.py`

`narrow_scope_via_funnel()` is the complete process of file selection in broad recall mode:

1. **Wide fetch**: A large number of Chroma retrievals cover the meeting + anchor scope (`wide_k` is dynamically calculated by `RAG_FUNNEL_WIDE_K_MIN/MAX` and log-scaling)
2. **Aggregate**: `aggregate_by_file_scored()` convolves the chunk score to the file level
3. **Evidence floor**: filter weak files according to `RAG_FUNNEL_EVIDENCE_MODE` (`absolute` / `ratio` / `percentile`); router top-K files are protected (not eliminated due to weak chunk evidence)
4. **Merge**: router and funnel file list merge (`rrf` strategy or legacy `zigzag`)
5. **Anchor injection**: `apply_anchor_evict()` ensures that the session anchor file appears
6. Return `ScopeSelection` (including file_scores + docs_by_file cache)

M4 optimization: summary router and wide-fetch are executed in parallel (`asyncio.create_task`), eliminating serial waits.

### 4.12 Anchor Injection `_anchor_inject.py`

Location: `rag/_anchor_inject.py`

`apply_anchor_evict()` injects "must-contain" anchor files into the candidate scope, and eliminates non-anchor files from the tail when the cap is exceeded:

- `cap`: maximum number of files
- `quota_ratio`: the maximum proportion that anchor files can occupy (`RAG_ANCHOR_QUOTA_RATIO`)
- Return `(new_scope, evicted_count)`
- Shared by two consumers: `_funnel_narrow.py` and `RouterOnlyStrategy`

### 4.13 Query Analysis `_query_analysis.py`

Location: `rag/_query_analysis.py`

`analyze_query()` provides pure regex (no LLM calls) lightweight query analysis:

**Speaker name extraction** — Three-layer strategy:
1. Known speakers (conference metadata/speaker_mappings) → exact substring matching (word-boundary-aware, LRU cache compiled regular)
2. English name → regular capital words (excluding question words such as `What/How/Why`)
3. Chinese → 2-4 words CJK matching triggered by speaker-query pattern (excluding common words such as `meeting/discussion`)

**Temporal hint detection** — Supports:
- Absolute time: "first 2 minutes", "last 5 minutes", "first 3 minutes" (stored as `absolute_seconds` tuple)
- Relative area: "early/middle/late/mid-late/first half/second half" etc. (mapped to `ratio_min/ratio_max`)
- Chinese number parsing (`_parse_zh_number`)

Return `QueryAnalysis` (`speaker_names` + `temporal_hint` + `topic_query`).

Speaker constraints are hard chunk-level constraints. Explicit
`speaker`/`speakers_in_chunk` metadata must contain the requested speaker; a
file-level roster only proves that the person appears somewhere in the file and
cannot rescue a chunk attributed to somebody else. Text matching is retained
only for legacy chunks without speaker metadata. If no chunk matches, retrieval
returns no speaker-scoped evidence instead of degrading to unrelated results.

---

## 4B. Scope routing (Scoping)

When the user does not specify `file_ids` explicitly, the RAG pipeline needs to decide "which files to retrieve". The scope routing module is responsible for this step.

### 4B.1 Data types `_scope_types.py`

`ScopeSelection` (frozen dataclass): The result of the strategy call, including:
- `scope_file_ids` — ordered list of file IDs
- `file_scores` — File-level relevance scores `[0, 1]`, used for downstream adaptive chunk allocation
- `docs_by_file` — wide-fetch cache, grouped by file_id, for `fair_retrieve_per_file` reuse

`BroadRecallContext`: Request-level memoization, multiple query variants share wide-fetch results of the same meeting scope (double-checked locking + `asyncio.Lock`).

### 4B.2 File Scoping Strategies `_scoping_strategies.py`

The `FileScopingStrategy` protocol defines the `select_scope()` interface. Four implementations are selected by `RAG_FILE_SCOPING_MODE`:

| policy                    | mode value                    | behavior                                                              |
| ------------------------- | ----------------------------- | --------------------------------------------------------------------- |
| `RouterAndFunnelStrategy` | `router_and_funnel` (default) | summary router + funnel parallel, RRF merge                           |
| `FunnelOnlyStrategy`      | `funnel_only`                 | Skip router, funnel is solely responsible for file selection          |
| `RouterPreFilterStrategy` | `router_pre_filter`           | router first narrows the meeting scope, and funnel operates within it |
| `RouterOnlyStrategy`      | `router_only`                 | router selects files directly without funnel narrow                   |

#### Implementation boundaries

When `ctx.file_ids` is empty, retrieval enters broad recall; when files are explicitly specified, it enters scoped retrieval. `RAG_FILE_SCOPING_MODE` selects one of the four broad-recall strategies above, while `file_ids` and `meeting_ids` constrain the requested scope. The former `RAG_HIERARCHICAL_ENABLED` setting had no remaining runtime effect and was removed so configuration does not advertise a false master switch.

### 4B.3 Scope Routing `_routing.py`

`_enumerate_scope_files()` — Enumerate all ready file IDs from the database (fallback baseline for broad recall).

`_route_scope_files_via_summary()` — Call summary router to pre-narrow files and return file_id list; return `None` when disabled/empty.

`_route_scope_files_with_scores()` — Same as above but returns the `(file_id, score)` pair.

`router_prefilter_meetings()` — Use the router’s top file selections to infer meeting IDs for use by the `router_pre_filter` strategy.

All functions take Prometheus metrics (`SUMMARY_ROUTER_REQUEST_TOTAL`, `SUMMARY_ROUTER_FILES_ROUTED`) and trace spans.

### 4B.4 Summary Router `_summary_router.py`

File-level routing, based on per-file summary embeddings + BM25 + RRF:

`route_files_by_summary(query, meeting_ids)`:
1. Vector retrieval: find similar document summaries in summary vectorstore
2. BM25 search (when `RAG_SUMMARY_ROUTER_HYBRID_ENABLED=True`): `fts5_search_file_summaries` keyword matching
3. RRF fusion: `_rrf_fuse_file_lists()` merged with `RAG_SUMMARY_ROUTER_HYBRID_ALPHA` weight
4. Fallback: vector-only fallback when BM25 is unavailable

`route_files_with_scores()` — Same as above but retaining scores (for use by trace).

Key configuration:
- `RAG_SUMMARY_ROUTER_ENABLED` — master switch
- `RAG_SUMMARY_ROUTER_TOP_FILES` — select up to several files
- `RAG_SUMMARY_ROUTER_MIN_SCORE` — minimum normalized higher-is-better relevance threshold; raw vector distances are converted before filtering
- `RAG_SUMMARY_ROUTER_FALLBACK_TO_CHUNK` — Whether to fall back to full volume when router has no hit

---

## 5. Reranking process (Rerank)

Entry: `rag/_reranker.py:rerank(query, docs, top_n, is_unscoped, min_per_file)`

```python
binding = settings.RERANKER_BINDING.lower()
if not binding or not docs:
    return docs
top_n = top_n or settings.RERANKER_TOP_N

if binding == "cohere":
    ranked = _rerank_cohere(query, docs, top_n)
elif binding == "bge":
    ranked = _rerank_bge(query, docs, top_n)
else:
    return docs

# scope-aware lowest score filtering (passed in by chain/_retrieve_post.py is_unscoped)
min_score = settings.RERANKER_UNSCOPED_MIN_SCORE if is_unscoped else settings.RERANKER_SCOPED_MIN_SCORE
if min_score > 0:
    ranked = [d for d in ranked if d.get("score", 0) >= min_score]
return ranked
```

### 5.1 Cohere path

**API Key Selection**: `RERANKER_API_KEY` takes priority, fallback to `LLM_API_KEY`.

**Two calling methods**:

#### (a) HTTP direct connection `_rerank_cohere_http`

When `RERANKER_BASE_URL` is non-empty (e.g. OpenRouter compatible endpoint):

```python
url = base_url.rstrip("/") + "/rerank"
response = _get_reranker_http_client().post(
    url,
    headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    },
    json={
        "model": model, # e.g. "cohere/rerank-4-pro"
        "query": query,
        "documents": [d["content"] for d in docs],
        "top_n": top_n,
    },
)
response.raise_for_status()
data = response.json()
return [{**docs[r["index"]], "score": r["relevance_score"]} for r in data["results"]]
```

#### (b) Official SDK `_rerank_cohere`

Without `RERANKER_BASE_URL`:

```python
client = _get_cohere_client(api_key) # cohere.ClientV2 singleton
response = client.rerank(model=model, query=query, documents=[...], top_n=top_n)
return [{**docs[r.index], "score": r.relevance_score} for r in response.results]
```

**Single instance management**: `_cohere_client` + `_cohere_client_key`, reconstructed through DCL when API key changes.

**Return semantics**: Cohere `relevance_score ∈ [0, 1]`, compared by scoped/unscoped threshold respectively; the current default value is
`RERANKER_SCOPED_MIN_SCORE=0.10`, `RERANKER_UNSCOPED_MIN_SCORE=0.05`.
The `RERANKER_MIN_SCORE` field still exists in Settings for configuration compatibility, but the current rerank implementation does not read it.

### 5.2 BGE local path

```python
from sentence_transformers import CrossEncoder
_reranker_model = CrossEncoder("BAAI/bge-reranker-v2-m3")  # Singleton
pairs = [(query, doc["content"]) for doc in docs]
scores = model.predict(pairs)
ranked = sorted(zip(scores, docs), key=lambda x: x[0], reverse=True)
return [{**doc, "score": float(score)} for score, doc in ranked[:top_n]]
```

**Features**:
- Cross-Encoder (not bi-encoder): does a forward for each `(query, doc)` pair, with higher accuracy than pure vector similarity.
- Thread-safe loading of singleton model.
- **Scores are logit (not normalized)**, range well beyond [0,1], and are **not directly comparable** to Cohere. Scoped/unscoped
  Thresholds for BGE usually require recalibration (see §12).

### 5.3 Downgrade behavior

All abnormal scenarios (library not installed, key missing, network error, API current limit):

```python
except Exception:
    logger.warning("... rerank failed", exc_info=True)
    return docs # Return in original order
```

**No circuit breaker** — the main process continues, but the LLM receives the
over-fetched results without reranking (usually with reduced quality). The
`rerank` trace span marks this case as `degraded` and records the backend,
candidate/output/reranked counts, latency, score range, and degradation reason.
Disabled, empty, and too-small candidate paths are explicit skipped spans, so
the response trace can distinguish configuration, no-data, low-yield, and
backend-failure cases without adding duplicate top-level response fields.

### 5.4 Cooperation with the retrieval layer

| Parameters | Default | Function |
| --- | --- | --- |
| `RAG_RERANK_FETCH_MULTIPLIER` | 6 | The vector library takes `top_k * 6` candidates and gives them to reranker |
| `RERANKER_TOP_N` | 20 | The intermediate document pool after rerank; the final scoped result is cut to `ctx.top_k`, and the broad mode retains at least one piece of each file |
| `RERANKER_UNSCOPED_MIN_SCORE` / `RERANKER_SCOPED_MIN_SCORE` | 0.05 / 0.10 | Broad / Scoped score lower limit (BGE needs to be calibrated) |

Typical default effect: `top_k=8, multiplier=6 → up to fetch 48 → rerank intermediate pool 20 →
Post-process and then cut to the final top-k`. When there are fewer than `max(top_k * 2, 12)` the reranker will be skipped because there are not enough candidates to filter.

---

## 6. Post-processing: Duplication & Contextual Assembly

### 6.1 Near duplicate suppression `suppress_near_duplicates`

Location: `chain/_steps_retrieve.py` (function `suppress_near_duplicates`, threshold constant `_CONTENT_SIMILARITY_THRESHOLD`)

```python
_CONTENT_SIMILARITY_THRESHOLD = 0.85

kept, kept_ngrams = [], []
for doc in ctx.docs:
    ngrams = _ngrams(doc["content"], n=4) # 4-gram collection
    is_dup = any(
        (len(ngrams & existing) / min(len(ngrams), len(existing))) >= 0.85
        for existing in kept_ngrams
        if ngrams and existing
    )
    if not is_dup:
        kept.append(doc)
        kept_ngrams.append(ngrams)

ctx.docs = kept
```

**Algorithm**:
- Traverse in order of reranker output (high score first)
- Generate character-level 4-gram sets for each document
- If the n-gram overlap rate with the retained document is ≥ 0.85 → discard
- Otherwise keep

**Purpose**: The same topic may appear repeatedly in the transcript, and rerank will push similar paragraphs to the front; suppressing allows LLM to see more independent information without wasting context tokens.

After this overlap filter, `_select_final_documents()` applies the configured
`_MMR_LAMBDA` in a deterministic lexical MMR pass when focused selection still
has more evidence than the final `top_k` budget. Summary/comparison intents use
cross-file coverage selection first.

### 6.2 Context assembly `_steps_context.py`

Inject multiple contexts in sequence (executed when the complete RAG pipeline is run, that is, `_classify_intent` is `rag`):

1. **Long-term memory** (`MemoryService.search_semantic`, etc., via `load_memories`) — Semantic recall based on user + query
2. **Knowledge Graph Entity** (`KnowledgeGraphService.get_entity_context`) — Named entities & related relationships in query
3. **Session Summary** (`SessionSummaryService.search`) — episodic memory across sessions
4. **Web search results** (`search.py`) – optional external information
5. **Conversation History** — Last N messages

Finally, it is assembled into prompt by `_steps_generate.py:build_context()`. Each item is best-effort, and failure does not affect the generation.

### 6.3 Generate `_steps_generate.py`

- Construct `ChatPromptTemplate`, inject: `{context}`, `{chat_history}`, `{memories}`, `{entities}`, `{session_summaries}`, `{web_results}`, `{question}`
- LCEL chain: `prompt | llm | StrOutputParser()`
- Streaming: push `step`, `token`, `sources`, `status`, `trace`, `web_results`, `error`, `done` events through `StreamBus`. `status=degraded` is a separate quality signal; retrieval-only context prefixes are stripped before any fallback excerpt is emitted.
- After message persistence, `schedule_fact_extraction()` commits a
  `fact_extraction` record to `durable_jobs`. The embedded worker executes the
  unified fact/entity extraction path with lease, retry, dedupe, cancellation,
  and dead-letter semantics; it is not a fire-and-forget coroutine.

---

## 7. Query rewriting and adaptive top-k

### 7.1 Query rewriting `rag/_query.py:rewrite_query`

```python
async def rewrite_query(question: str) -> str:
    if _is_simple_query(question):
        return question # short & no pronoun → skip

    llm = _get_rewrite_llm() or get_llm()
    prompt = ChatPromptTemplate.from_messages([("human", _QUERY_REWRITE_PROMPT)])
    response = await asyncio.to_thread(cached_retry_invoke, llm, prompt.format_messages(query=question))
    return response.content.strip()
```

**Prompt**:

```
Rewrite the query to improve document retrieval quality.
- If the query is in Chinese, include relevant English technical terms.
- Expand abbreviations and acronyms.
- Add synonymous phrases that might match the document language.
- Keep the core intent unchanged.
- Return ONLY the rewritten query, nothing else.
```

**Skip rule `_is_simple_query`**:

```python
_REWRITE_MAX_TOKENS = 6 # 6 words or less are considered simple
_ANAPHORA_PATTERN = re.compile(r"\b(it|that|this|they|them|these|those|the above|the previous|the last)\b", re.IGNORECASE)

def _is_simple_query(question: str) -> bool:
    return len(question.split()) <= 6 and not _ANAPHORA_PATTERN.search(question)
```

Long questions or pronouns (may require context resolution) → rewrite; short keyword search → use directly.

**Single case rewrite LLM**: `_get_rewrite_llm()` uses a separate lightweight model (`QUERY_REWRITE_MODEL`, such as `gpt-4o-mini`) to avoid occupying the quota of the main LLM for low-complexity tasks such as rewrite.

### 7.2 Adaptive top-k `determine_adaptive_top_k`

```python
_COMPLEXITY_KEYWORDS = {"how many", "compare", "analyze", "list all", "summary of",
    "relationship between", "difference between", "why did",
    "what caused", "step by step", "explain",
    "all of the", "what is the", "what are the",
    "could you", "can you",
}
_SIMPLE_QUESTION_MIN_CHARS = 30

def determine_adaptive_top_k(question: str, user_requested_k: int | None) -> int:
    if user_requested_k is not None:
        return user_requested_k
    q = question.lower().strip()
    if len(q) < 30 and not any(kw in q for kw in _COMPLEXITY_KEYWORDS):
        return 3 # Simple fact questions and answers
    return 8 # Enumeration/comparison/analysis
```

When the user explicitly passes `top_k`, it always takes precedence; otherwise, it is determined by heuristic rules.

---

## 8. Configuration reference table

| Parameters (YAML key) | Default value | Type | Description |
| --- | --- | --- | --- |
| **Slice** | | | |
| `rag.chunk_size` | 1024 | int | Maximum number of characters for flat slices |
| `rag.chunk_overlap` | 128 | int | Number of overlapping characters in adjacent chunks |
| `rag.child_chunk_size` | 256 | int | parent-child mode sub-chunk size |
| `rag.child_chunk_overlap` | 32 | int | Child chunk overlap |
| `rag.parent_child_enabled` | false | bool | Enable parent-child double-layer slicing |
| `rag.semantic_chunking_enabled` | false | bool | Enable structure-aware slicing |
| **Search** | | | |
| `rag.distance_metric` | l2 | str | `l2` / `cosine` |
| `rag.top_k` | 8 | int | Default number of blocks that end up in LLM; user/intent policy can be further tuned |
| `rag.score_threshold` | 1.5 | float | Vector distance/similarity threshold (different meanings by measure) |
| `rag.hybrid_search_enabled` | true | bool | Enable explicit vector + BM25 + RRF retrieval; compatible hot changes do not require reindexing |
| `rag.hybrid_alpha` | 0.5 | float | RRF vector weight (1-α for BM25) |
| `rag.rerank_fetch_multiplier` | 6 | int | Vector library overfetch multiple |
| `rag.multi_query_enabled` | false | bool | Enable multi-query expansion |
| `rag.multi_query_count` | 3 | int | Number of query variants generated |
| `rag.query_rewrite_enabled` | true | bool | Enable LLM query rewriting |
| `rag.query_rewrite_model` | `""` | str | Rewrite dedicated lightweight model |
| `rag.retriever_provider` | `hybrid` | str | `vector` / `hybrid` / `multimodal` / `hybrid_multimodal` |
| `rag.raganything_enabled` | false | bool | Enable multi-modal retrieval of RAGAnything |
| `rag.raganything_fallback_to_native` | true | bool | Fallback to vector/hybrid when RAGAnything fails |
| `rag.raganything_working_dir` | `""` | str | RAGAnything storage directory (if empty, use `data/raganything/`) |
| `rag.raganything_index_timeout_seconds` | 120.0 | float | Index timeout |
| `rag.raganything_query_timeout_seconds` | 30.0 | float | Query timeout |
| `rag.raganything_llm_timeout_seconds` | 90.0 | float | LLM call timeout |
| `rag.index_tables` | true | bool | Index table content |
| `rag.index_image_captions` | true | bool | Index image description |
| `rag.image_ocr_min_length` | 15 | int | Minimum length of OCR text (anything shorter than this will not be indexed) |
| `rag.content_type_rerank_enabled` | true | bool | Rerank by content type |
| `rag.sibling_coretrieve_enabled` | true | bool | Enable sibling chunk collaborative retrieval |
| `rag.sibling_coretrieve_per_anchor` | 1 | int | Number of siblings pulled per anchor |
| `rag.sibling_coretrieve_max_total` | 4 | int | The upper limit of the total number of siblings |
| `rag.memory_context_max_tokens` | 800 | int | Memory context token budget |
| `rag.entity_context_max_tokens` | 600 | int | Entity context token budget |
| `rag.session_context_max_tokens` | 800 | int | Session context token budget |
| `rag.context_load_timeout_s` | 8.0 | float | Context load timeout (seconds) |
| `rag.skill_match_timeout_s` | 15.0 | float | Skill match timeout (seconds) |
| **Rearrange** | | | |
| `rag.reranker_binding` | `cohere` | str | `cohere` / `bge` / `""`(disabled) |
| `rag.reranker_model` | `cohere/rerank-4-pro` | str | model name |
| `rag.reranker_api_key` | `""` | str (secret) | read from env |
| `rag.reranker_base_url` | `https://openrouter.ai/api/v1` | str | HTTP compatible endpoint; if empty, use the official SDK |
| `rag.reranker_top_n` | 20 | int | rerank intermediate document pool; the final result is also subject to scope and `ctx.top_k` |
| `rag.reranker_min_score` | 0.15 | float | rerank minimum score (needs to be recalibrated under BGE) |

---

## 9. Scenario configuration template

### 9.1 Minimalist (development/testing)

```yaml
rag:
  chunk_size: 1024
  chunk_overlap: 128
  top_k: 5
  distance_metric: l2
  reranker_binding: ""
  hybrid_search_enabled: false
  semantic_chunking_enabled: false
  parent_child_enabled: false
```

Features: fastest, most cost-effective; unstable quality. For local debugging.

### 9.2 Balance (recommended for production)

```yaml
rag:
  chunk_size: 1024
  chunk_overlap: 128
  top_k: 5
  distance_metric: cosine
  reranker_binding: "cohere"
  reranker_top_n: 5
  reranker_min_score: 0.15
  hybrid_search_enabled: false
  semantic_chunking_enabled: false
  parent_child_enabled: false
```

Features: vector retrieval plus Cohere reranking provide two quality safeguards;
latency remains controllable and quality is more stable.

### 9.3 High accuracy (long documents/complex business)

```yaml
rag:
  chunk_size: 512
  chunk_overlap: 64
  child_chunk_size: 256
  child_chunk_overlap: 32
  top_k: 5
  distance_metric: cosine
  reranker_binding: "bge"
  reranker_top_n: 5
  hybrid_search_enabled: true
  hybrid_alpha: 0.5
  semantic_chunking_enabled: true
  parent_child_enabled: truemulti_query_enabled: true
  multi_query_count: 3
  query_rewrite_enabled: true
```

Features: Fully functional, highest recall and accuracy; high cost, large delay, used for critical businesses. It is recommended to run `scripts/benchmark rag-all` offline to verify the gain.

---

## 10. Performance Characteristics

| Configuration | Indexing time | Query latency | Embedding cost | Rerank cost |
| --- | --- | --- | --- | --- |
| Minimalist | Fast | Fastest | Low | 0 |
| Balanced (+ Cohere) | Fast | Medium | Low | Medium (API call) |
| High precision (all enabled) | Slow (parent-child ≈ 2×) | Slow (Hybrid + RRF + Multi-Query) | High | High (BGE local GPU or Cohere) |

**Typical bottlenecks**:
- **Index**: embedding is much slower than chunking. Parent-child doubling the amount of embedding is the biggest amplifier.
- **Query**: Multi-query will multiply the retrieval time by 4; rerank (especially BGE CPU inference) is the main source of delay before the first token.
- **LLM generation**: When top_k is small, the amount of tokens entering LLM after rerank is linearly related to the generation speed.

---

## 11. Troubleshooting

### The search results are empty or irrelevant

1. Is `score_threshold` too strict? In L2 mode, 1.5 is already too strict for 768-dimensional embedding.
2. When parent-child is turned on, are the sub-blocks too small (< 128), causing fragmentation?
3. Try `hybrid_search_enabled=true` to let BM25 supplement keyword matching.
4. Turn on `multi_query_enabled` to expand query expressions.
5. Look at the `docs_retrieved` of the `retrieve` span in the trace to determine whether the filter is too strict or the vector is not recalled at all.

### Reranker has no effect or error

- Cohere:
  - Check if `RERANKER_API_KEY` or `LLM_API_KEY` is set
  - Check if quota, network, `RERANKER_BASE_URL` are correct
  - Search the log for `Cohere rerank failed` / `Cohere rerank HTTP failed`
- BGE:
  - `pip install sentence-transformers`
  - The first startup will download `BAAI/bge-reranker-v2-m3` (~2GB)
  - Each query has ~second-level latency under CPU; GPU recommended
  - Scoped/unscoped reranker threshold is meaningless for BGE. It is recommended to calibrate according to benchmark or perform sigmoid normalization.
- General: Reranker failure will result in **silent downgrade**, please check the logs for key indicators.

### Embedding fee is too high

- Turn off `parent_child_enabled` (embedding amount is halved)
- Increase `chunk_size` (reduce the number of chunks)
- Confirm that `_dedup_existing_chunks` takes effect (log `all N chunks unchanged, skipping`)
- Switch to local embedding provider (Ollama/HuggingFace)

### High query latency

- Turn off `multi_query_enabled` (save LLM variant generation + 3× retrieval)
- Turn off `hybrid_search_enabled` (save FTS5 queries + RRF merging)
- Lower `top_k` and `reranker_top_n`
- BGE → Cohere (replace CPU inference with network IO, most scenarios are faster)

---

## 12. Optimization direction (continuous evolution)

Sorted by **Impact × Cost**; each item is marked with the current code location for easy implementation.

### 12.1 Chunking & Indexing

1. **Calibrate the implemented token-based splitter**
   - Current status: flat and parent-child indexes use `count_tokens` with 384/64 and 160/24 token budgets, so CJK and English no longer share misleading character limits.
   - Next step: compare alternative token budgets with `scripts/benchmark rag-all`; changing them updates the index fingerprint and requires reindexing.

2. **Calibrate opt-in structure-aware and parent-child modes**
   - Status: `SEMANTIC_CHUNKING_ENABLED` and `PARENT_CHILD_ENABLED` both default to `False`.
   - Recommendation: run `scripts/benchmark rag-all` for comparison. If the recall/answer quality is significantly better than flat, change the default value and comment it explicitly in `config/main.yaml`.

3. **Use the speaker/mute boundary**
   - `transcriber.py`'s timestamped transliteration with **speaker changes** and **silence intervals** is a stronger topic boundary than text regularization.
   - Suggestion: Pass the speaker switching point as a hard boundary to the chunker in `processor/_pipeline.py`.

4. **Deduplication hashing incorporates more dimensions**
   - `_dedup_existing_chunks` still only does short hashing of `page_content`; `chunk_id` already contains `file_id`, cross-file collisions have been alleviated.
   - If there are still stale vectors caused by "metadata changed but text unchanged", you can consider piecing the key metadata fragments into the hash input.

5. **Index consistency and metadata hashing**
   - Currently BM25 supports precise cleaning by `(meeting_id, file_id)`, and the parent-child mode will mirror the child granularity.
   - `_dedup_existing_chunks` still only does a short hash of the normalized `page_content`; if the metadata changes but the text remains unchanged,
     Might keep old vectors. Consider incorporating key metadata such as page number and content type into the hash input, and supplement migration/rollback testing.

### 12.2 Retrieval

1. **RRF dynamic parameter verification**
   - Currently `RRF_K_PARAM` is configurable, and `_adaptive_k(fetch_k, base_k)` will adjust k according to the candidate size; funnel also
     `RAG_FUNNEL_RRF_K`. Subsequent benchmarks should be used to verify the default values ​​and score calibration under different corpus sizes.

2. **Dynamic `HYBRID_ALPHA`**
   - Short keyword queries should be biased towards BM25 (`α < 0.5`), and long natural language should be biased toward vectors (`α > 0.5`).
   - Suggestion: `determine_hybrid_alpha(question)`, based on query length/language/keyword density.

3. **Multi-Query Cost/Benefit Assessment**
   - Currently Multi-Query can be combined with Hybrid, both Scoped and Broad paths are supported; Broad is supported by
     `RAG_BROAD_RECALL_MULTI_QUERY_ENABLED` is controlled individually.
   - The number of query variants, vector/BM25 request budget, file-level RRF and answer quality should be compared later to avoid adding linear costs on low-yield corpus.

4. **Diagnostic retry when filtering has no results**
   - Currently, we mainly retain the original problem retry and best-effort/fail-open logic, and will not arbitrarily relax the user's explicit
     `meeting_ids`, `file_ids`, file type or date filtering.
   - If you want to add relaxed filtering in the future, you must explicitly mark the trace and response metadata to avoid crossing the user-specified security boundary.

5. **Final evidence diversity (implemented)**
   - `_select_final_documents()` applies a deterministic lexical MMR pass for
     focused/factual selection. `_MMR_LAMBDA` is the documented
     relevance-versus-redundancy weight; summary/comparison intents first retain
     cross-file coverage above a relative relevance floor.
   - This is text-overlap MMR, not embedding-space MMR. Compare the two with the
     benchmark before adding another embedding call to the online path.

### 12.3 Rerank

1. **BGE Score Calibration**
   - Cohere returns `[0,1]`; BGE returns logit (no upper bound). Current scoped/unscoped thresholds require separate calibration for BGE.
   - Suggestions (choose one):
     - Do `sigmoid` normalization on BGE scores
     - Or create calibrated threshold configurations for scoped/unscoped/BGE/Cohere respectively

2. **BGE batch inference optimization**
   - `CrossEncoder.predict(pairs)` defaults to pairwise; on GPUs should be explicit `batch_size`, `show_progress_bar=False`, and `.to("cuda").eval()` on initialization.

3. **Two-stage rerank**
   - First use a cheap bi-encoder to coarsely queue ~20 items, and then feed the cross encoder, which can significantly reduce the delay when the fetch_multiplier is large.

4. **Rerank result caching**
   - The rerank results of the same query + the same candidate set (especially streaming retries) are worth caching.
   - Suggestion: `md5(query + sorted(doc_ids))` as key, LRU to memory.

5. **HTTP client reuse (implemented)**
   - `_rerank_cohere_http` uses a shared thread-safe `httpx.Client`, matching the
     existing `asyncio.to_thread()` execution model. Connections are pooled,
     reset closes the old client, shutdown closes the active client, and
     transient network/429/5xx failures are retried with bounded backoff.

6. **Reranker observability (implemented)**
   - The response's existing `trace` payload carries one `rerank` span with
     `backend`, `executed`, candidate/output/reranked counts, latency, score
     range, and a skip or degradation reason. Prometheus separately tracks
     request, failure, low-quality fallback, and duration metrics.

### 12.4 Query Rewrite & Routing

1. **Rewrite scheduling**
   - The non-streaming pipeline overlaps session creation and query rewriting.
     Streaming reuses one history preload, then rewrites before retrieval because
     the resolved query is an input to the query plan and embedding cache.
   - Any further speculation must be limited to work independent of the resolved
     query; do not race retrieval with two query meanings and silently merge them.

2. **HyDE (Hypothetical Document Embeddings)**
   - For long and difficult questions, using LLM to generate a "hypothetical answer" and then doing vector retrieval is usually more effective than query rewriting.
   - Low implementation cost: new step `_steps_hyde.py`, after rewrite and before retrieve.

3. **Intent classification calibration**
   - `QueryPlan` already distinguishes factual, summary, comparison, and
     exhaustive intents while the outer route distinguishes casual from
     retrieval. The remaining work is multilingual benchmark calibration and an
     explicit unknown/ambiguous outcome, not another overlapping classifier.

### 12.5 Context Assembly

1. **Token budget-aware pack (implemented)**
   - `_steps_generate.py` uses `count_tokens()` and one global allocator across
     chunks, file/meeting summaries, web, session, entity, and memory context.
     It reserves history, system-prompt, and answer budgets, records dropped
     chunks, and has a defensive final truncation for pathological settings.

2. **Citations / source grounding (implemented at evidence-chunk level)**
   - The prompt uses stable `[N]` markers, final post-budget context order is kept
     byte-aligned with `sources[N-1]`, and the frontend renders citation chips,
     source previews, and source navigation.
   - Sentence-level span attribution remains a separate research feature; it
     should be evaluated for entailment and citation completeness before being
     presented as stronger grounding.

3. **Long context de-redundancy upgrade**
   - `_resolve_parent_chunks` under parent-child have been merged with the same parent; but similar parents across meetings may still be redundant.
   - Suggestion: do one more parent-level merge before `suppress_near_duplicates`.

### 12.6 Evaluation and Observability

0. **Meeting evidence governance (implemented)**
   - `benchmark evidence-governance` is a deterministic, provider-free suite for
     rejected-evidence exclusion/audit access, strict explicit time windows,
     monotonic source-revision fencing, and authority labels in generation
     context. It records per-case rows, dataset/implementation hashes, and four
     separately named accuracies; it is also part of `make eval-audit`.

1. **Rerank evaluation (implemented)**
   - `benchmark.py` records whether reranking actually executed and why it was
     skipped. Recall/MRR/nDCG values are `null` for skipped queries and aggregate
     only over executed rows, preventing a fallback ranking from being reported
     as a reranker score.

2. **Trace fields**
   - Retrieval already records document counts and scoped routing details;
     reranking records before/after counts, backend, latency, score range, and
     skip/degradation state. Keep raw query text out of durable trace metadata by
     default; use request-local debugging with redaction when query inspection is
     necessary.

3. **Benchmark into CI**
   - Use the core indicator of `benchmark rag-all` (recall@k / answer faithfulness) as an optional job for CI, and fail PR when the threshold degrades. Avoid "don't dare to move the RAG parameters once they are adjusted".

---

## 13. One sentence summary

Currently RAG has covered most of the key modules required at the industrial level -
**Structure awareness + parent-child chunking, adaptive top-k, Hybrid + RRF, Cohere/BGE dual reranker, Query Rewrite, Multi-Query, Dedup, Trace**, and is very attentive to engineering details such as "best-effort degradation, singleton concurrency safety, deterministic upsert, per-file vector cleaning" and other engineering details.

The focus of the next round of optimization should shift from **"adding additional functions"** to **"making the default switch truly on, making indicators quantifiable, and making rerank and hybrid dynamically adaptive"** - combining benchmarks to capture the optimal configuration, and then using CI to cover quality degradation.
