# Memory system & knowledge graph

**Verified against backend and frontend implementation:** 2026-09-10.

The subsystem is not a single vector search. SQLite owns the fact ledger,
projects, lifecycle, business/system time, provenance, audit data, sessions,
entities, and relations. Chroma contains rebuildable semantic indexes. A
SQLite-backed durable queue separates response latency from extraction and
re-indexing, while revision/source fences prevent stale jobs from publishing.
The `/memory` UI exposes seven views: Projects, Memories, Decisions & tasks,
State changes, Meeting review, Entities, and Past Sessions.

The current architecture and its storage boundaries are summarized in the
[repository-level diagram](../../docs/diagrams/memory-and-kg.md). Evaluation
protocols and the currently published evidence are documented in
[`benchmarking.md`](./benchmarking.md) and the
[documentation index](../../docs/README.md#historical-audits-and-implementation-records).

> Implementation details of long-term memory (facts/preferences/goals) and knowledge graph (entities + relationships).
>
> Code location:
>
> - `backend/src/services/memory/` — memory services (extraction, decay, merge, profiling, search, history, session summary)
> - `_entry.py` — top-level entry (`MemoryEntry` dataclass, unified memory entry structure)
> - `_service/` — core service layer (CRUD, search, extraction, merge, decay synchronization, portrait)
> - `_summary_service.py` — Session summary service (memory across sessions)
> - `_summary_vectorstore.py` / `_vectorstore.py` — summary and memory vector library encapsulation
> - `_history.py` — `SQLiteChatMessageHistory` (LangChain compatible)
> - `_extractor.py` — low-level fact extractor
> - `_parsers.py` — extract/merge/cluster parsing
> - `_decay.py` — Decay fraction calculation
> - `backend/src/services/knowledge_graph/` — entities/relationships/vectorization

## 1. Why long-term memory is needed

The meeting assistant faces **cross-session and cross-time user context**: user preferences, organizational structure, recurring items... This information does not belong to any single meeting record, but will appear repeatedly in the conversation. The system extracts them, stores them independently, and periodically decays and merges them to achieve the "AI remembers me" experience.

## 2. Overall architecture

```
 Conversation flow (chain pipeline)
      │
      ├─► Generate answer
      │
      ▼
 schedule_fact_extraction(ctx)
      │ commit before producer returns
      ▼
 durable_jobs(kind=fact_extraction)
      │ lease / retry / dead-letter
      ▼
 run_fact_extraction_job() → MemoryService.auto_extract_facts()
      │ ┌──► LLM prompt: get_fact_extraction_prompt()
      │ │
      ├─► Extract facts from the latest dialogue turn ─┤
      │ └──► Parse JSON → list[FactCandidate]
      │
      ├─► For each fact:
      │ ├─ upsert user_memories (unique user_id+key)
      │ ├─ Write embedding id → Chroma (memories collection)
      │ └─ Update `last_accessed` / `access_count`
      │
      └─► Trigger KnowledgeGraphService.extract_entities()
            ├─ LLM prompt: get_entity_extraction_prompt()
            ├─ upsert memory_entities
            └─ upsert memory_relations (resolved as subject_id / object_id + predicate)

 Ingest flow (independent of chat questions)
      │
      └─► ready file transcript/document text
            ├─ bounded overlapping evidence windows
            ├─ durable content-addressed `fact_extraction` jobs
            └─ exact meeting/file scope + verbatim evidence quote
```

## 3. Data model review

(See [`database.md`](./database.md) for details)

- **`user_memories`**: `(user_id, key)` is unique. `salience` is the stable user/extraction judgment; `freshness_score` decays independently; `confidence` records evidence quality; `usefulness_score` is updated only through explicit feedback. `valid_from` / `valid_to`, evidence message IDs/excerpt, and conflict links make assertions temporal and auditable. `importance` remains a compatibility alias for salience.
- **`memory_decay_state`**: One row per user, recording the last decay time (column names are subject to `_migrations.py` / warehousing SQL)
- **`memory_entities`**: `(user_id, name, entity_type)` unique; `description`, `embedding_id`, number of occurrences and session traceability fields, etc.
- **`memory_relations`**: foreign key **`subject_id` / `object_id`** points to `memory_entities.id`; relations carry confidence, evidence message IDs, and validity timestamps. `(user_id, subject_id, predicate, object_id)` is unique and a repeated assertion refreshes its provenance.
- **`memory_scopes`**: memory scope association (meeting_id / file_id), used to limit the retrieval scope
- **`entity_scopes`**: Entity scope association, symmetrical with `memory_scopes`
- **`memory_audit_log`**: Memory change audit log, recording the context of CRUD operations
- **`memory_fact_versions`**: append-only assertion snapshots keyed by
  `(memory_id, revision)`. `valid_from` / `valid_to` describe when the fact is
  true in the business world; `recorded_at` / `recorded_to` describe when that
  version was known by this system. A later transition may close either open
  interval without rewriting the captured value or provenance. Every upsert, CAS
  edit, supersession, dispute, confirmation, and retraction
  retains the value, validity window, confidence, evidence, project, and scope
  that were authoritative at that revision.
- Action items additionally expose indexed `action_status`, `assignee`, and
  `due_at` fields for deterministic open/completed/overdue lists.

Current facts are typed as `preference`, `project_fact`, `decision`,
`action_item`, or `fact`, and carry a lifecycle state: `pending`, `confirmed`,
`disputed`, `superseded`, or `retracted`. Only `confirmed` facts are eligible
for answer-time recall. Retraction is reversible/auditable and is distinct from
hard deletion for privacy or retention requests.

## 4. Fact Extraction

Code: `services/memory/_service/_extraction.py`.

### 4.0 Public operating modes

Clients select one stable `memory_mode` instead of coordinating individual
thresholds:

| Mode       | Recall                                     | Extraction | Knowledge graph    | Intended use                    |
| ---------- | ------------------------------------------ | ---------- | ------------------ | ------------------------------- |
| `off`      | disabled, including past-session summaries | disabled   | disabled           | no long-term personalization    |
| `focused`  | up to 3 items, single-hop                  | precise    | disabled           | low latency and low noise       |
| `balanced` | configured production defaults             | balanced   | configured default | normal use                      |
| `deep`     | up to 8 items, multi-hop                   | aggressive | enabled            | complex cross-session reasoning |

The selected mode is captured in the immutable request snapshot and copied
into the durable extraction job. A restarted worker therefore replays the
originating mode rather than whichever global settings happen to be active
later. Low-level settings remain deployment-level escape hatches.
`off` is also enforced independently at the fact, entity/KG, and prior-session
context steps. Their trace spans are marked as skipped with
`skip_reason=memory_mode_off`, so the boundary does not depend only on the outer
orchestration snapshot.

Before memory, entity, summary, contradiction, consolidation, or profile text
is inserted into an LLM prompt, structural markup is escaped. This prevents
stored content from closing its trusted data wrapper and opening a forged
prompt section; system-level data-only instructions remain in place as a
second layer.

### 4.1 Extraction timing

- **Schedule_fact_extraction(ctx)`is called by`services/chain/\_steps_generate.py` after each round of dialogue**
- The extraction is persisted as a restart-safe durable job and does not block the response return
- `memory_mode=off` does not enqueue an extraction job
- Write `ctx.failed_extraction_count` on failure and do not throw it to the caller
- Successful file ingestion also schedules extraction directly from bounded,
  overlapping source windows. Meeting/project memory coverage no longer
  depends on which passages users later retrieve in chat. Every source window
  is scheduled; the legacy per-file setting is only a cooperative scheduling
  burst size and never drops the document tail. Each job carries the immutable
  file revision, character range, content hash, and source event time. A worker
  discards a job if the file was deleted or replaced before execution. The same
  source revision is fenced again inside every outer SQL write transaction, so
  a deletion or replacement during the LLM call rolls the derived write back.
  Chat-triggered jobs preserve every retrieved evidence coordinate (meeting,
  file, page/slide, media timestamps, chunk index, and document revision) and
  carry a revision fence for every referenced file. A multi-file job is rejected
  if even one referenced file is missing, replaced, or lacks a matching fence;
  it is never committed against a partially current evidence set.

### 4.2 Mode: `MEMORY_EXTRACTION_MODE`

```python
_mode_limits = {
    "precise": 1, # Extremely conservative, only key facts
    "balanced": settings.MEMORY_MAX_FACTS_PER_TURN, #default 3
    "aggressive": 5, # as many as possible
}
```

- **precise**: Extract only the most obvious 1 fact; and **skip knowledge graph entity extraction** to reduce noise
- **balanced**: regular production mode
- **aggressive**: training data collection/user portrait cold start phase

### 4.3 Context injection

The extraction prompt will be accompanied by **currently existing top-N important memories** to let LLM know which facts already exist and avoid repeatedly extracting the same content:

```python
existing = memory_service.list_important(user_id, limit=10)
prompt = get_fact_extraction_prompt(
    conversation=recent_messages,
    existing_memories=existing,
    max_facts=mode_limit,
)
```

### 4.4 Deduplication and replacement

Each fact is compared against both high-salience rows and vector-nearest
neighbours. Exact, multilingual lexical, and semantic duplicates are collapsed;
conflicting values go through the contradiction resolver. Same-key updates are
retained as append-only assertion revisions. Different-key replacements record
`superseded_by`; unresolved same-key conflicts are stored under a deterministic
candidate key with `disputed` status instead of being silently discarded.
Semantic duplicate lookup uses the candidate's meeting/file scope, preventing a
same-named person or project in another meeting from being treated as the same
fact. Automatic extraction never uses the assistant's generated answer as its
own evidence: candidates must be supported by the user's explicit message or by
the retrieved source chunks persisted with the durable extraction job. Local
clause matching includes English and Chinese negation/unknown polarity checks.
The extraction schema also carries explicit fact type, project, subject,
predicate, object, validity window, action state/owner/deadline, and a verbatim
`evidence_quote`. A supplied quote must occur in the authoritative input.
Identity-bearing names/codes, polarity, and multilingual value coverage are
checked against the same local evidence clause. Unsupported optional routing
metadata is stripped while the verbatim assertion is retained; contradictory
directional fields still fail closed. If the model paraphrases the display
value but supplies an exact source quote, that quote becomes the stored value
instead of treating the paraphrase as evidence. Model confidence alone never
confirms an assertion: automatic confirmation requires literal support for the
asserted value inside its authoritative quote; all other candidates remain
`pending`. Repeated observations merge scope and append a versioned,
source-window evidence record. Deleting a file removes its scope and evidence
references; a source-extracted assertion is retracted only after its final file
source disappears.

Stable logical keys are derived from project/entity/predicate identity rather
than the current object value (for example, an owner change retains one
`...owner` identity). Exact-key lookup is performed before scoped semantic
deduplication. Writes use revision compare-and-swap, so concurrent workers
cannot silently overwrite a newer assertion. A disputed candidate is confirmed
through `POST /api/v1/memory/resolve-conflict`, which checks the expected winner
revision and atomically supersedes every selected declared alternative.

Directional owner/assignee constructions receive an additional tuple check:
the asserted owner and target must match the same parsed source relation. A
known model schema inversion (`subject=owner`, `object=owned item`) is repaired
only when the human-readable assertion independently matches the quote.
This prevents token-equivalent reversals such as “Bob replaced Alice” being
stored as “Alice replaced Bob”. Recognized older source events cannot overwrite
a newer materialized value; they are retained as disputed candidates for
review. Memory and knowledge-graph extraction share the same assertion
validator: subject, predicate cue, object, polarity, and direction must occur
inside one locally assertive clause. Questions, conditions, attributed claims,
proposals/future statements, negations, empty evidence, and cross-sentence token
synthesis cannot create a confirmed assertion.

### 4.5 Task-specific recall and user control

Queries about decisions, action items, owners, deadlines, risks, dependencies,
or current project status first run an exact typed SQL lookup, then add semantic
neighbours. Known project identifiers are resolved before limiting. Open,
completed, and overdue task queries use indexed action state/deadline predicates;
explicit historical markers can apply a fact-validity cutoff. `valid_at`
selects business time and `known_at` selects system/transaction time; API values
must include an explicit UTC offset so browser-local times cannot be silently
reinterpreted. For
historical queries, current semantic recall is excluded so the answer receives
one coherent bitemporal snapshot across all fact types. Current-only profile, entity
graph, and session-summary views are also omitted until they implement the same
valid-time API. Exhaustive typed queries do
not apply a row cap; if the final prompt still exceeds its token budget,
generation receives an explicit truncation warning and must not claim the list
is complete.
Meeting/file scope is pushed into vector retrieval as a SQL-derived key
allow-list; the final SQL validation remains defense in depth.

Natural absolute cutoffs such as `as of 2025-03-01`,
`截至2025年3月1日`, and `截止到 2025/03/01` are normalized once in the
shared query plan. Comparison questions retain every explicitly named cutoff
and load a separately labelled memory snapshot for each one; a single latest
date is never silently substituted for a multi-date comparison. Document
`date_to` remains an independent corpus filter, while explicit `valid_at` and
`known_at` remain authoritative memory coordinates. Relative phrases without
an absolute date are deliberately not guessed.

The Memory page maps the storage and governance model into these views:

| View              | What it exposes                                                                                                                         | Main contract                                                               |
| ----------------- | --------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| Projects          | Project directory, aliases, linked materials, preparation shortcuts                                                                     | project revisions prevent silent concurrent overwrite                       |
| Memories          | Personal/project facts and reference knowledge; CRUD, import/export, decay, feedback, lifecycle and vector state                        | virtualized records scroll inside the bounded card; filters remain visible  |
| Decisions & tasks | Deterministic decision/action/project-fact lists with project, assignee, status, due date, material, `valid_at`, and `known_at` filters | SQL query and stable paging, not semantic top-k pretending to be exhaustive |
| State changes     | Before/after bitemporal comparison                                                                                                      | compares authoritative fact revisions                                       |
| Meeting review    | Evidence-backed candidates, conflicts, source links, confirm/retract/edit actions                                                       | snapshot paging and expected revision protect review consistency            |
| Entities          | Entity search, relations, batch delete, and alias merge                                                                                 | SQLite graph is authoritative; vectors accelerate lookup                    |
| Past Sessions     | Session summaries and cross-session context                                                                                             | episodic summaries remain separate from typed facts                         |

Fact cards display type, lifecycle state, project, task owner/deadline,
business/system-time validity, source scope, evidence, and vector health. Each
file-backed evidence record links to its meeting/material location. Users can
filter, edit typed/temporal fields, confirm, dispute/retract, open sources, and
inspect immutable revisions. Session deletion separately controls whether facts
derived from that conversation are retracted. Conflict confirmation is an
atomic domain operation, not a cosmetic status edit.

The versioning migration backfills one baseline snapshot for memories that existed
before immutable revisions were introduced. It preserves their current value and
scope but does not fabricate earlier states that were never recorded.

Persisted task continuation uses schema version 4 (root/current objective, open
questions, active scope, temporal coordinates, memory/source references, and a
checksum-bound frozen context); readers accept versions 1–4.
`continuation_mode=latest` reruns against current
indexes and state. `saved_scope` restores the prior scope and temporal controls
but retrieves current evidence. `saved_snapshot` restores the exact assembled
context and complete citation documents bound to the prior AI message. Its hash
and session/message ownership are checked before use. Document, memory, entity,
cross-session, web, recall-feedback, anchor, and extraction branches are disabled
for that turn, so a historical continuation cannot silently mix old and new
evidence. A missing, malformed, tampered, or unsupported snapshot fails with
`SNAPSHOT_UNAVAILABLE` instead of falling back to current retrieval. Legacy v1-v3
sessions replay their frozen citation preview only when it remains recoverable;
exact full-context replay begins with v4 turns.

Historical structured-memory queries first resolve the authoritative version of
each logical key under business time (`valid_at`) and system knowledge time
(`known_at`), then apply fact-type, lifecycle, project, and scope filters. This
prevents an older confirmed/type-compatible revision from resurfacing after a
newer lifecycle or type transition. A `known_at` document query also excludes
meeting files whose `content_recorded_at` is later than the cutoff, and disables
current-only meeting/file summaries, session summaries, KG projections, and web
search. It does not reconstruct document text that was overwritten before file
content versioning existed; use a saved v4 continuation snapshot when exact
answer-time document replay is required.

Meeting-file review changes use a monotonic `source_revision`, not a
second-resolution timestamp. The semantic update, native-index dirty state, and
durable reindex job are committed in one SQLite transaction. Extraction jobs
carry the revision and are fenced again immediately before commit. Rejecting a
file retracts auto-extracted facts only when no non-rejected file evidence still
supports them; retained scopes and immutable semantic events provide the audit
trail. Metadata-only reindexing does not enqueue fact extraction again; only a
real transcript/content-hash change creates new extraction windows.

## 5. Memory decay

Code: `services/memory/_decay.py` + `_service/_decay_sync.py`.

### 5.1 Formula

```python
# Excerpt from services/memory/_decay.py (requires calendar, time, math)
def _compute_decay_score(importance, reference_time, decay_rate=_DECAY_RATE_PER_DAY):
    reference_ts = calendar.timegm(time.strptime(reference_time, "%Y-%m-%d %H:%M:%S"))
    days_elapsed = (time.time() - reference_ts) / 86400
    return importance * math.exp(-decay_rate * days_elapsed)
```

Exponential decay applies only to `freshness_score`. It never mutates salience,
confidence, or usefulness. Recall updates access metadata but is not treated as
confirmation or positive feedback.

### 5.1.1 Retrieve rating weight

Memory retrieval uses a weighted scoring formula:

```python
score = weighted_mean(semantic_similarity, freshness, salience,
                      confidence, usefulness)
```

Default weights are `0.35 / 0.15 / 0.25 / 0.15 / 0.10`. Legacy decay and
importance configuration names are retained as aliases for freshness and
salience. Weights are normalized at runtime.

### 5.2 Execution method

- **Periodic task** `memory_decay_loop` (lifespan best-effort task) is executed every `MEMORY_DECAY_INTERVAL_HOURS`
- **TTL**: hard expiration deletes the SQL row and durably queues derived-vector cleanup
- **Separate tracking per user** `memory_decay_state.last_decay_time` implements incremental decay; maintenance scans the full corpus rather than the paginated list API

### 5.3 The fate of low-scoring memories

- **salience < threshold** → no longer inject prompt (but not physically deleted)
- **Over TTL** → physical SQL deletion + immediate/best-effort Chroma cleanup backed by `pending_vector_deletions`
- **Capacity or low-value stale recall** → set `archived_at` and remove the active vector, while retaining the business/version ledger

## 6. Consolidation

Code: `services/memory/_parsers.py`.

When several memories of the same user are highly similar in semantics, they are merged into a more authoritative memory:

### 6.1 Clustering strategy (dual mode)

- **`MEMORY_SEMANTIC_CLUSTER_ENABLED=True`** (opt in): Use Chroma to check adjacent vectors and cluster them according to the similarity threshold
- **`False`**: Degenerate to plain text overlap (token Jaccard)

### 6.2 Merge Rules

- Triggered only when cluster size ≥ `MEMORY_CONSOLIDATION_MIN_CLUSTER` (default 3)
- LLM generates a comprehensive text based on all memories in the cluster
- The `superseded_by` of the old memory points to the new memory id (not physically deleted, traceable)

### 6.3 Trigger timing

- When `MEMORY_CONSOLIDATION_ENABLED=True`, triggered once after each portrait refresh (see §7)
- It can also be triggered manually via `POST /api/v1/memory/decay` (the interface name is slightly misleading and actually contains the consolidation step)

## 7. User profile refresh (Profile Refresh)

Code: `services/memory/_service/_profile.py`.

- **Trigger**: Refreshed every `MEMORY_PROFILE_REFRESH_INTERVAL` interactions
- **Behavior**: LLM reads top-N important memories → generates a user profile and saves it as the reserved memory (`category=user_profile`, `key=__profile__`)
- **Use**: used as system-level context in the memory injection step of the chain pipeline

## 8. Session Summary (Episodic Memory)

Code: `services/memory/_summary_service.py` (`SessionSummaryService` class).

### 8.1 Generation conditions

- `SESSION_SUMMARY_ENABLED=True`
- The number of current session messages ≥ `SESSION_SUMMARY_MIN_TURNS`
- `POST /api/v1/sessions/{id}/summarize` manually trigger ** or ** background compensatory running

### 8.2 Writing

SQLite is the source of truth. Each row stores `summary`, `topics`,
`key_entities`, `decisions`, `turn_count`, and the optional Chroma
`embedding_id`. Updates are monotonic by `turn_count`: a late LLM result that
covers fewer messages cannot overwrite a newer summary. A retry covering the
same number of messages may replace an older result.

SQLite persistence happens before vector indexing. The service then re-reads
the authoritative row while holding the shared session-vector write lock;
only that exact version is written to the deterministic Chroma ID. The
`embedding_id` backfill uses the same session, user, turn count, and summary as
a compare-and-set guard. This prevents concurrent summarizers from publishing
a stale vector or returning stale API data.

### 8.3 Use

- **Current session**: `SESSION_MAX_HISTORY` bounds the SQLite history read. If
  the loaded history still exceeds `SESSION_MAX_TOKENS`, older turns are
  summarized and recent turns remain verbatim. The generated summary is
  inserted as explicitly untrusted historical data at human-message priority;
  it is never promoted to a system instruction, and structural tags are
  escaped before reuse.
- **Cross-session**: `POST /sessions/search` and the chain pipeline use hybrid
  recall: summary-vector search supplies semantic matches, while user-scoped
  chat FTS5 supplies exact names, numbers, and identifiers. Results are
  deduplicated by session and fused with reciprocal-rank fusion (RRF). If
  Chroma is unavailable, FTS remains an operational fallback. Returned scores
  are normalized ranking signals, not calibrated probabilities.
- Summary vectors retain both `meetings_covered` and `files_covered`; strict
  scoped recall recovers missing legacy provenance from persisted message sources.
- Each completed turn also persists a version-3 task-state checkpoint.
  `root_objective` preserves where the session began, while `objective` tracks
  the active task and `objective_history` records task switches; a genuine
  follow-up does not reset it. Open questions are retained only when the answer
  remains unresolved. Retrieved sources include file/meeting/chunk identity,
  index generation and content hash, and recalled memories include the exact
  revision used. Resuming a session restores this as untrusted data in addition
  to replaying raw messages; it does not replace the message history.

### 8.4 Repair and observability

Startup reconciliation rebuilds session-summary vectors that are missing from
Chroma or have a missing/stale `embedding_id` in SQLite. The periodic index
reconciliation loop performs the same repair in bounded batches, so a
transient indexing failure does not permanently remove episodic memories from
semantic recall. Healthy rows are skipped, and every repair candidate is
re-read under the shared vector lock before replacement.

`session_summary_search_total{path=...}` records the effective retrieval path:
`hybrid`, `vector_only`, `fts_only`, `fts_fallback`, `empty`, or `error`.

### 8.5 Key submodules

| Modules               | Responsibilities                                                                                                                                                                                                              |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `_summary_service.py` | Cross-session memory service: manages the life cycle of session summary, starts backfilling, and generates periodic summary when idle (located in `services/memory/_summary_service.py`, not in the `_service/` subdirectory) |
| `_history.py`         | `SQLiteChatMessageHistory` (LangChain compatible): persist the conversation history to SQLite for reading by chain pipeline                                                                                                   |
| `_extractor.py`       | Low-level fact extractor: encapsulates LLM calls and JSON parsing for use by `_service/_extraction.py`                                                                                                                        |
| `_entry.py`           | Top-level entry: export `MemoryEntry` dataclass to unify the structure of memory entries                                                                                                                                      |

## 9. Knowledge graph

Code: `services/knowledge_graph/`.

### 9.1 Entity extraction

```python
# _service.py::extract_entities
if settings.MEMORY_EXTRACTION_MODE == "precise":
    return # skip KG in precise mode

prompt = get_entity_extraction_prompt(messages, existing_entities)
response = await llm.ainvoke(prompt)
entities, relations = parse_entity_response(response)

await _store_entities(entities)
await _store_relations(relations)
```

### 9.2 Entity Storage

- upsert `memory_entities` (`(user_id, name, entity_type)` unique)
- Also embed name+description into Chroma's `entities` collection
- **Structured logging** logging on failure (no rollback of SQL side writes)

### 9.3 Relational storage

- upsert `memory_relations`
- Relations persist bounded confidence, evidence message IDs, validity intervals,
  source session, and update time in SQLite
- LLM relation output is treated as an untrusted proposal. Before persistence,
  both endpoints and a predicate cue must occur in the same authoritative
  evidence window. Directional owner/lead relations additionally require the
  parsed subject and object in the asserted direction; reversed or target-wrong
  tuples are dropped locally.
- A relation write failure is propagated to the durable extraction job so it can
  retry. Partial per-relation failures are not reported as successful extraction.
- `ENTITY_RELATIONS_LIMIT` controls the upper limit of the number of relationships returned by a single entity (to avoid the explosion of very large celebrity nodes)

### 9.4 Query path

| API                                         | Behavior                                                                     |
| ------------------------------------------- | ---------------------------------------------------------------------------- |
| `GET /api/v1/memory/entities`               | List all entities (paginated)                                                |
| `POST /api/v1/memory/entities/batch-delete` | Delete up to 100 entities in one transaction                                 |
| `GET /api/v1/memory/entities/{name}`        | Return entities + all incoming and outgoing relationships + related memories |
| `DELETE /api/v1/memory/entities/{name}`     | Delete entities, relationships and vectors                                   |
| `POST /api/v1/memory/entities/merge`        | Merge multiple synonymous entities into one main entity                      |

### 9.5 Injection into RAG

The `load_entity_context` step of the chain pipeline:

1. Perform entity recognition (LLM or regular) on user queries
2. Search Chroma `entities` collection to find similar entities
3. Check `memory_relations` based on the entity and expand one hop/two hops
4. Inject the "entity + relationship summary" into the `entity_context` section of the final prompt

## 10. The order of memory injection into Prompt

The `build_context` step of the chain pipeline is assembled according to the following priority:

```
[user_profile] ← Profile (if exists)
[important_memories (top 6)] ← Sort descending by decay score
[session_context] ← Historical session summary (across sessions)
[entity_context] ← Knowledge graph extension
[web_results] ← optional
[retrieved_chunks] ← RAG documentation
[history] ← The last N rounds of the current session
→ generate prompt
```

Each segment has a token budget, which is truncated in descending order of similarity when exceeded.

## 11. API Overview

| Method   | Path                                   | Description                                  |
| -------- | -------------------------------------- | -------------------------------------------- |
| `GET`    | `/api/v1/memory`                       | List (supports filtering and sorting)        |
| `POST`   | `/api/v1/memory`                       | Create                                       |
| `PUT`    | `/api/v1/memory`                       | Update                                       |
| `DELETE` | `/api/v1/memory`                       | Delete                                       |
| `POST`   | `/api/v1/memory/batch`                 | Batch import                                 |
| `POST`   | `/api/v1/memory/batch-delete`          | Delete up to 100 memories in one transaction |
| `GET`    | `/api/v1/memory/export`                | Cursor-paginated JSON export                 |
| `POST`   | `/api/v1/memory/search`                | Semantic search                              |
| `POST`   | `/api/v1/memory/decay`                 | Manual decay + merge                         |
| `GET`    | `/api/v1/memory/entities`              | List entities                                |
| `POST`   | `/api/v1/memory/entities/batch-delete` | Batch-delete entities                        |
| `GET`    | `/api/v1/memory/entities/{name}`       | Entity details                               |
| `DELETE` | `/api/v1/memory/entities/{name}`       | Delete entity                                |
| `POST`   | `/api/v1/memory/entities/merge`        | Merge entities                               |

## 12. Tuning and troubleshooting

| Symptoms                                 | Tuning directions                                                                                                       |
| ---------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| Too much noise in memory retrieval       | `MEMORY_EXTRACTION_MODE=precise` or lower `MAX_FACTS_PER_TURN`                                                          |
| Memory is not updated                    | Confirm `MEMORY_AUTO_EXTRACT=True`; inspect the `fact_extraction` durable job state, dead-letter count, and worker logs |
| Old preferences never disappear          | Increase `decay_rate` / Decrease `TTL_DAYS` / Manual `DELETE`                                                           |
| Entity explosion                         | Decrease `aggressive` → `balanced`; increase entity deduplication threshold                                             |
| Chroma entity dimensions do not match    | Apply embedding changes through a controlled restart, then monitor reconciliation/reprocessing                          |
| Cross-session history cannot be recalled | Confirm `SESSION_SUMMARY_ENABLED=True`; check if `session_summaries` has data                                           |

## 13. Extension direction

1. **Time decay is configurable**: Currently `decay_rate` is a global constant and can be distinguished by category (such as "preference" decays slower than "task")
2. **Vector-Text Hybrid Deduplication**: Now merging relies on pure vector clustering, adding text rules can reduce mis-merging
3. **Fact Confidence**: LLM requires `confidence` when extracting, low confidence goes to the confirmation round
4. **Multi-user isolation enhancement**: Currently segmented by `user_id`, you may consider adding `workspace_id` for multi-tenancy
5. **Graph 2-hop reasoning**: Currently only one hop relationship is taken, and more steps can be expanded based on the query.
