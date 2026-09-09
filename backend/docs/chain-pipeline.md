# Chain Pipeline: the orchestration layer of RAG Q&A

> Go from `ask()` / `ask_stream()` to the complete steps of generating an answer, including parallel context loading, streaming event bus and skill integration.
>
> Code location: `backend/src/services/chain/` (request entry points,
> `_steps_*` stages, `_retrieve_*` retrieval modules, prompt/generation helpers,
> provenance adapters, and durable extraction scheduling).

The streaming latency guard applies only after two checks. First, a conservative
answer-shape router admits atomic person/date/number/boolean/status lookups to a
local BM25 probe. Second, a post-retrieval evidence filter checks expected
answer shape, score concentration, and cross-source ambiguity. Weak evidence is
automatically promoted to the normal retrieval and reranking path. Only a
validated probe may skip the remote reranker; the trace records both the
`fast_path_evidence` decision and a skipped `rerank` span with reason
`chat_latency_guard`.

The 2.5-second interactive target is an observability SLO, not a cancellation
deadline. Validated fast streams have separate first-visible-token (10s),
between-token stall (15s), and total generation (30s) safety budgets. Ordinary
synthesis requests retain the normal generation timeout.

## 1. Layering

```
src/services/chain/
├── __init__.py # Request-scoped public API only
├── _api.py # ask() entry + _run_pipeline arrangement
├── _api_stream.py # ask_stream() entry (producer-consumer StreamBus)
├── _context.py # PipelineContext / PipelineResult dataclass
├── _common.py # Shared logger and public auxiliary functions
├── _routing.py # Intent classification (casual vs retrieval) + _casual_response
├── _formatting.py # Source reference extraction, answer formatting
├── _fallback.py # Streaming → non-streaming downgraded circuit breaker
├── _skill_matching.py # get_skill_loader / get_skill_matcher singleton
├── _anthropic_cache.py # Anthropic prompt cache tag
├── _extraction.py # Fact/entity extraction scheduling
├── _generate_helpers.py # LLM generation helper function
├── _meeting_summary_lifecycle.py # Meeting summary life cycle management
├── _memory_sources.py # Convert admissible memory evidence to source previews
├── _query_routes.py # Conservative routing for explicit recorded-ledger queries
├── _resolver.py # Query parsing/disambiguation
├── _retrieve_broad.py # Wide area search (multiple sources)
├── _retrieve_filters.py # Retrieve filter structure
├── _retrieve_post.py # Post-retrieval processing (deduplication/suppression/sorting)
├── _retrieve_routing.py # Retrieve routing decisions
├── _retrieve_utils.py # Retrieve auxiliary tool function
├── _speaker_context.py # Speaker context injection
├── _steps_session.py # ensure_session, rewrite_query
├── _steps_retrieve.py # retrieve + rerank + dedup branch
├── _steps_context.py # memories / entity / session / web / history
├── _steps_generate.py # build_context + LLM generation + save message + scheduling fact extraction
├── _judge_prompts.py # RAG quality evaluation prompt
└── _per_file_summary.py # File-by-file summary
```

## 2. Data structure

### 2.1 `PipelineContext`

Input + unified container for intermediate states. Core fields:

```python
@dataclass
class PipelineContext:
    # input
    question:str
    session_id: str | None = None
    user_id: str = "default"
    meeting_ids: list[int] | None = None
    file_ids: list[int] | None = None
    top_k: int | None = None
    use_web_search: bool = False
    web_search_results: int | None = None
    file_types: list[str] | None = None
    date_from: datetime.date | None = None
    date_to: datetime.date | None = None
    rag_mode: str | None = None

    trace: TraceContext = field(default_factory=TraceContext)
    llm: BaseChatModel | None = None
    embeddings: Embeddings | None = None # embeddings instance, pipeline entrance injection
    settings_epoch: int = 0
    settings_snapshot: SettingsSnapshot | None = None

    # Intermediate results
    rewritten_query: str = ""
    docs: list[dict] = field(default_factory=list)
    scope_file_ids: list[int] = field(default_factory=list) # The file scope determined in the routing stage
    query_analysis: QueryAnalysis | None = None # Speaker name + time analysis
    memory_context: str = ""
    session_context: str = ""
    entity_context: str = ""
    web_context: str = ""
    web_results: list[dict] = field(default_factory=list)
    history_messages: list[BaseMessage] = field(default_factory=list)
    meeting_context: str = "" #Assembled document context
    combined_context: str = "" # All assembled system context
    past_session_refs: list[dict] = field(default_factory=list)
    query_embedding: list[float] | None = None # Query vector (prewarm cache)

    # output
    answer: str = ""
    failed_extraction_count: int = 0

    # Skill matching results (parallel with retrieve, consumed before generate)
    skill_name: str | None = None
    skill_confidence: float | None = None

    # Number of chunks discarded by token budget during retrieval phase
    dropped_chunks: int = 0
```

### 2.2 `PipelineResult`

Flat structure returned externally:

```python
@dataclass
class PipelineResult:
    answer:str
    sources: list[dict]
    session_id: str
    web_results: list[dict] | None = None
    past_sessions: list[dict] | None = None
    extraction_failed: bool = False
    trace: dict | None = None # Serialized span tree
    skill_used: str | None = None
    skill_confidence: float | None = None
    context_truncated: int | None = None #Token budget truncates the number of discarded chunks
```

## 3. Synchronization entry: `ask()`

```python
async def ask(
    question: str,
    session_id: str | None = None,user_id: str = "default",
    meeting_ids: list[int] | None = None,
    file_ids: list[int] | None = None,
    top_k: int | None = None,
    use_web_search: bool = False,
    web_search_results: int | None = None,
    file_types: list[str] | None = None,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    rag_mode: str | None = None,
) -> PipelineResult:
```

Internal process:

1. Get `settings_epoch` + `build_settings_snapshot()` to construct `PipelineContext`
2. `_classify_intent(question)` does intent classification
3. Casual → short path (`ensure_session` + `_casual_response(question)` + `save_messages`)
4. Parse the LLM / embeddings singleton and inject `ctx.llm` / `ctx.embeddings` (make sure each step in the pipeline uses the same instance, even if the settings change midway)
5. Skill matching is started concurrently with **`asyncio.create_task()`**, overlapping execution with subsequent RAG pipelines
6. `_run_pipeline(ctx, None, skill_task=skill_task)` ensures the session, resolves the query, then publishes one immutable query plan before parallel retrieval, memory, entity, session-context, and history branches start
7. The skill result is consumed before `generate_answer`; match successful → load skill complete definition, no match → normal RAG path
8. Assembly `PipelineResult` returns

### 3.1 Casual short circuit

`_routing.py::_classify_intent()` uses regular rules to match **short greetings/thanks/short confirmations** (see `_GREETING_PATTERN`, `_SMALLTALK_PATTERN`, `_CJK_GREETING_PATTERN`, `_CJK_SMALLTALK_PATTERN`, supports both English and Chinese, Japanese and Korean), and returns if hit `casual`, to avoid meaningless RAG calls. Additional length guarding for mixed CJK text (>6 characters force RAG). The short path uses `_casual_response(question)` to return the canned copy (**without calling LLM**).

### 3.2 Skill matching

- `_skill_matching.py` provides `get_skill_loader()` and `get_skill_matcher()` singletons (DCL + thread-safe)
- `SkillLoader.load_summaries()` returns lightweight summaries for matching
- `IntentMatchingService.match()` returns best match + confidence (timeout `SKILL_MATCH_TIMEOUT_S` skipped)
- Matching is started with **`asyncio.create_task()`** and executed in parallel with `_run_pipeline`; skill results are consumed before `generate_answer`
- Double query matching: If `rewritten_query` is different from the original query, the two queries will be matched separately and the result with higher confidence will be obtained.
- Match successful → `loader.get_full(name)` loads the complete definition → `skill.model_dump()` passes to `_run_pipeline`

## 4. Core orchestration: `_run_pipeline()`

```python
async def _run_pipeline(
    ctx: PipelineContext,
    skill_definition: dict[str, Any] | None = None,
    *,
    skill_task: asyncio.Task[Any] | None = None, # Parallel skill matching tasks
) -> None:
    ctx.trace.start_span("pipeline", "pipeline")
    with otel_span("chain.run_pipeline"):
        # Step 1-2: Session preparation + query rewriting (parallel)
        await asyncio.gather(
            asyncio.to_thread(ensure_session, ctx),
            rewrite_query_step(ctx),
        )

        # Step 2.5: Warm up the query vector cache (let subsequent parallel branches share the same embedding call)
        await _prewarm_query_embedding(ctx)

        # Step 3: Load local context in parallel (core benefit point)
        context_timeout = _context_branch_timeout(ctx)
        await asyncio.gather(
            _retrieve_branch(), # retrieve → pre_rerank_dedup → rerank → suppress
            _best_effort("memories", load_memories(ctx), context_timeout), # With timeout isolation
            _best_effort("session", load_session_context(ctx), context_timeout), # With timeout isolation
            _best_effort("entity", load_entity_context(ctx), context_timeout), # With timeout isolation
            _best_effort("history", load_history(ctx), context_timeout), # With timeout isolation
        )

        # Step 3.25: Web policy runs after local retrieval.
        await _best_effort("web", perform_web_search(ctx), settings.WEB_SEARCH_TIMEOUT_S)

        # Step 3.5: Consume skill_task results (usually completed at this time, 0ms waiting)
        if skill_task is not None and skill_definition is None:
            try:
                match = await skill_task
            except Exception:
                match = None # Failure does not block the pipeline
            if match and match.matched:
                skill_definition = loader.get_full(match.skill.name).model_dump()
                ctx.skill_name = match.skill.name
                ctx.skill_confidence = float(match.score)

        # Step 4: Assemble the final context (blocking operations go to to_thread)
        await asyncio.to_thread(build_context, ctx)

        # Step 5: LLM generation
        await generate_answer(ctx, skill_definition)

        # Step 6: Persistence + durable fact-extraction enqueue
        await asyncio.to_thread(save_messages, ctx)
        await schedule_fact_extraction(ctx) # Persist durable job

    # Step 7: Refresh pending touch operations in memory
    flush_pending_touches()
    ctx.trace.finish_span("pipeline")
```

### 4.1 Why parallelism

Memory, session context, entities, history, and local retrieval are independent and load in parallel. Web search deliberately runs afterward. `web_search_mode=always` always performs the requested lookup; `fallback` may skip it only when a producer supplies an explicitly calibrated, bounded local-confidence value. RRF, vector relevance, and reranker rank scores are never interpreted as probabilities. The legacy `use_web_search=true` flag maps to `always`. Session preparation and query rewriting also run in parallel in Steps 1-2.

### 4.2 Failure isolation

Non-critical `load_*` branches are wrapped with `_best_effort(name, coro, timeout)`:

- **Timeout protection**: `CONTEXT_LOAD_TIMEOUT_S` (separate `WEB_SEARCH_TIMEOUT_S` for web search)
- **Timeout**: record `CONTEXT_STEP_TIMEOUT_TOTAL`, finish the span as `timeout`, and continue without throwing
- **Exception**: record `CONTEXT_STEP_ERROR_TOTAL`, finish the span as `degraded`, log the exception, and continue
- `retrieve_branch` is not isolated (core dependencies, failures should be propagated upward)

The main pipeline only throws exceptions for fatal errors (without documentation, high-quality answers cannot be generated).

### 4.3 Trace spans

`TraceContext` (`core/trace.py`) uses the **`start_span(label, phase, ...)` + `finish_span(label, status)`** pairing, without the context manager form of `span()`.

```python
ctx.trace.start_span("rewrite_query", "retrieve")
try:
    # ... mutate ctx ...
    ctx.trace.finish_span("rewrite_query")
except Exception:
    ctx.trace.finish_span("rewrite_query", "error")
    raise
```

The `trace` field in the return body of `ask()` / `ask_stream()` comes from `ctx.trace.to_dict()` (including `trace_id`, `total_ms`, `spans[]`). Span status is one of `success`, `error`, `timeout`, or `degraded`; the last two make a best-effort fallback visible without falsely classifying the whole request as failed. Pipeline logs include the non-success span list.

`prewarm_query_embedding` is an explicit child span. It records the selected
binding/model and whether the cache warm-up succeeded, was skipped, or degraded.
This keeps shared embedding latency from being incorrectly attributed to the
concurrent skill-matching branch.

## 5. Details of key steps

### 5.1 `ensure_session`

- If `ctx.session_id` is empty → create a new session
- If it exists but cannot be found in DB → create it as well
- Update `last_active`

### 5.2 `rewrite_query_step`

- Prioritize the use of **query resolver (resolver)** (`_resolver.py`): first load the lightweight history window (up to `MAX_RESOLVER_HISTORY_MESSAGES=8`) and call `resolve_query()` for disambiguation/referential resolution
- If the resolver has not changed the original query and `QUERY_REWRITE_ENABLED=True`, fall back to the **legacy rewrite path** (`rag._query.rewrite_query`)
- If the resolver is disabled with `RESOLVER_ENABLED=False`, or the query is determined to be a simple query (`_is_simple_query`), it also falls back to the legacy path
- Legacy rewrite uses **independent lightweight model** `QUERY_REWRITE_MODEL` (can be set to a cheaper model than the main LLM)
- Can produce 1 rewritten query or `MULTI_QUERY_COUNT` queries (multiple query mode, generated by `_generate_query_variants` in `_steps_retrieve.py`)

### 5.3 `retrieve_branch`

See [`rag.md`](./rag.md) for details. Key points:

1. **retrieve**: Chroma semantic retrieval (can + BM25 hybrid RRF fusion)
2. **pre_rerank_dedup** (`_retrieve_post.py`): Use **n-gram Jaccard overlap rate** to do cheap deduplication before reranking (threshold `RAG_PRE_RERANK_DEDUP_THRESHOLD`, default 0.92) to avoid paying Cohere/BGE call cost for near-duplicate documents
3. **rerank**: Cohere or BGE cross-encoder, retain top_n
4. **suppress_near_duplicates**: Do **4-character n-gram Jaccard overlap rate** for adjacent reserved documents; if `overlap >= 0.85` (`_CONTENT_SIMILARITY_THRESHOLD`), it will be regarded as a near duplicate and discard the lower ranked ones
5. **final selector**: Always enforce the final `top_k`, even when the optional reranker is disabled or skipped; broad queries reserve coverage across distinct files before filling remaining positions by rank

### 5.4 `load_memories`

- First `memory_service.get(user_id, "__profile__")` injects the user profile (if any)
- Then `memory_service.search_semantic(..., limit=min(MEMORY_MAX_CONTEXT_ITEMS, 8), min_importance=MEMORY_MIN_IMPORTANCE, meeting_ids=..., file_ids=...)` gets memories relevant to the current problem; `__profile__` is excluded from this generic path to prevent duplicate prompt injection
- After a successful answer, update access time/count only. Recall alone does not increase importance; promotion requires an explicit confirmation or a contradiction-resolution write

### 5.5 `load_session_context`

- Check the `session_summaries` table
- Vector retrieval of historical session summaries (cross-session episodic memory)
- Only inject the top K related to the current query

### 5.6 `load_entity_context`

- First perform lightweight entity recognition on `ctx.rewritten_query`
- Check Chroma `entities` collection to find similar entities
- Expand `memory_relations`
- Text spelled out like "[entity] is related to ..."

### 5.7 `perform_web_search`

- Only executed when `ctx.use_web_search=True` and `SEARCH_BINDING` (and corresponding API key, except DuckDuckGo) are configured
- Implementation: `services/search.py` (DuckDuckGo/SerpAPI/Tavily/Bing/Exa)
- The upper limit of the number of results `SEARCH_MAX_RESULTS`; in `_run_pipeline`, this coroutine uses **`asyncio.wait_for(..., settings.WEB_SEARCH_TIMEOUT_S)`** as the parallel branch timeout (different from the general `CONTEXT_LOAD_TIMEOUT_S`)
- **Does not replace RAG**, only supplements it

### 5.8 `load_history`

- Get the latest messages of the current conversation
- When `SESSION_MAX_TOKENS` is exceeded (and the number of messages > 6), session summary is triggered to replace old messages (the last 4 original texts are retained, and the rest are summarized and compressed)
- The result is `list[BaseMessage]` after being cleaned by `sanitize_history_messages` (removing `[N]` reference marks in AI messages, truncating over-long messages, and cropping according to token budget)

### 5.9 `build_context`

For the assembly sequence, see [`memory-and-kg.md`](./memory-and-kg.md#10-the-order-of-memory-injection-into-prompt).

- Each segment has an independent budget (hard cap)
- **skill injection**: If a skill is matched, the `prompt_sections` of the skill will be inserted into the specific location of the system prompt.

### 5.10 `generate_answer`

Signature: `async def generate_answer(ctx: PipelineContext, skill_definition: dict | None = None)` (`_steps_generate.py`).

- Prompt: `get_skill_prompt(skill_definition)` if skill is included; otherwise `get_rag_prompt()`
- Chain: `prompt | RunnableLambda(apply_anthropic_cache_control) | llm | StrOutputParser()`, called through `_invoke_chain_with_retry` / `_invoke_chain_with_retry_multimodal` after being concurrency/circuited by the `traffic_controller` context manager (internally using `chain.invoke`, outer layer `asyncio.to_thread` wrapper to avoid blocking the event loop)
- The input parameter fields are consistent with the template (including `context` / `question` / `history` / `memory_context`, etc.); the multimodal path injects the `HumanMessage` image and text block on the document containing images (gated by `MULTIMODAL_ATTACH_GATE_ENABLED`, only attach images to visual queries to save tokens)
- Streaming paths are not within this function; see `ask_stream()` fork of `chain.astream(...)`

### 5.11 `save_messages`

Write user message + agent message in the same transaction, and update `chat_sessions.updated_at` synchronously.

### 5.12 `schedule_fact_extraction`

`schedule_fact_extraction` writes a `fact_extraction` row to `durable_jobs`
before the response completes. The dedupe key combines session and turn-content
hash. `run_fact_extraction_job` performs combined extraction (or the two-step
memory/KG fallback); the worker owns exponential retry, leasing and dead-letter.
Deleting a session cancels matching queued/running rows.

### 5.13 `flush_pending_touches`

Called after `schedule_fact_extraction` (executed at the end of the pipeline in both `_api.py` and `_api_stream.py`). Flush the accumulated `touch_memory_access` in memory to the database in batches to avoid writing DB one by one in the hot path.

### 5.14 `_common.py`

The `logger` instance and public auxiliary functions shared by the pipeline module are uniformly referenced by `_api.py`, `_api_stream.py` and each `_steps_*.py`.

## 6. Streaming entry: `ask_stream()`

`ask_stream(...)` (`_api_stream.py`) returns an **asynchronous generator** with elements **`dict`** (`StreamBus.__anext__` returns `StreamEvent.to_dict()`).

Concurrency model:

1. Create `StreamBus()` (bounded queue, default `maxsize=2000`, non-terminal events are discarded when `put_nowait` is full and logged; terminal events `done`/`error` are stored in independent slots and will never be lost).
2. `asyncio.create_task(_pipeline())` runs the complete pipeline in the background; the main coroutine **`async for event in bus:`** provides real-time `yield` events to `chat_stream`.
3. `finally`: If the task is not completed, `cancel`, `await task`, and then `bus.close()`.

`_pipeline()` is internally similar to the sync path (`classify_intent` → `_is_trivially_short` short-circuit checks → optional skill → `ensure_session` / `rewrite_query` / parallel retrieval and context / `build_context`), but does not call `_run_pipeline()`; streaming LLM uses `chain.astream(...)` (or `llm.astream(messages)` multi-modal path), the token is consumed via `_emit_stream()` and emitted through `bus.emit_token`, and at the end `bus.emit_trace` + `bus.emit_done`. `_emit_stream` has internal **heartbeat before the first token** (`_PRE_TOKEN_HEARTBEAT_TIMEOUT_S=8.0s` interval) and global heartbeat (5s interval) double keep-alive. LLM asynchronous generators are closed in `finally` via `aclose()` (timeout `_ACLOSE_TIMEOUT_S=3.0s`), falling back to non-streaming calls on failure (protected by `is_fallback_circuit_open()` circuit breaker).

For LangChain `astream` generator **`await aclose()`** (timeout 3s) in `finally` to avoid `GeneratorExit` warning when the client disconnects. Force `athrow(GeneratorExit)` to ensure shutdown after timeout.

### 6.1 Event type (`StreamBus` / `StreamEvent`)

| `type` | payload | meaning |
|---|---|---|
| `step` | `step`, `status` (`start`/`done`) and optional metadata | Pipeline stages (such as `accepted`, `session`, `pipeline`, `generate`) |
| `token` | **`content`**: string fragment | LLM streaming increment (front-end `useChatStream` reads `event.content`) |
| `sources` | `items`: list of sources | Retrieve references |
| `trace` | `trace`:dict | full `TraceContext.to_dict()` |
| `web_results` | `items` | Web results |
| `heartbeat` | no extra fields | keep alive |
| `error` | `message`, optional `code` / `detail` / `exception_type` | User-readable error |
| `done` | `session_id` | End normally; then `bus.close()` |

All events are automatically appended with **`seq`** (monotonically increasing sequence number, used for causal ordering) and optional **`parent_step`** (parent step name, used for causal chain tracking).

### 6.2 Router layer

`api/routers/chat.py::chat_stream` uses `serialize_event(event)` (`stream_bus.py`) to output the **SSE `data:` line** and in `finally` to the generator **`await stream.aclose()`** (timeout 5s):

```python
async for event in ask_stream(...):
    yield serialize_event(event)
```

The front end can be consumed with `fetch` + `ReadableStream` or a custom parser (does not have to be a browser `EventSource`).

## 7. Error handling strategy

| Scene | Behavior |
|---|---|
| `classify_intent` failed | Treated as retrieval, continue with the complete process |
| `_is_trivially_short` hit | Same as casual short-circuit: skip RAG, return to preset copy (extra processing in streaming path) |
| `rewrite` / `resolver` failed | Skip, use original query |
| `retrieve` returns 0 documents | Continue generating, LLM replies "No relevant information found" |
| `rerank` fails | fallback to unreranked results |
| `load_memories` failed | Empty context + log (isolated by `_best_effort`) |
| `load_session_context` failed | Empty context + log (isolated by `_best_effort`) |
| `load_entity_context` failed | Empty context + log (isolated by `_best_effort`) |
| `perform_web_search` failed | Empty context + log (isolated by `_best_effort`) |
| `load_history` failed | Empty context + log (isolated by `_best_effort`) |
| `generate_answer` throws an error | throws up → the router layer returns 500 (the streaming path sends error event + done event) |
| `save_messages` failed (session deleted) | log + skip (`sqlite3.IntegrityError` FK violation defense) |
| `schedule_fact_extraction` failed | request fails before claiming background work was accepted |
| Streaming LLM call failed | Automatically downgraded to non-streaming call (protected by `is_fallback_circuit_open()` circuit breaker, retried up to 2 times) |
| `skill_task` failed | log + continue on normal RAG path |

## 8. Extensibility points

1. **New context source**: Add `load_xxx(ctx)` in `_steps_context.py` → Register to `asyncio.gather`
2. **New skill**: Built-in files are in `backend/skills/builtin/`; dynamic creation through `POST /api/v1/skills` writes persistent Markdown under `data/skills/`.
3. **New intent routing**: Extend `_routing.py::_classify_intent()` (currently only `casual` / `rag` two levels)
4. **Richer trace**: `core/trace.py` is currently a memory tree and can be exported via OpenTelemetry
5. **A/B test**: switch different prompt templates with feature flag in `build_context`

## 9. Common pitfalls

- ⚠️ `StreamBus.emit*` is **synchronous** `put_nowait` - **Don't** `await bus.emit_*`; the order is guaranteed by the calling order.
- ⚠️ Do not use request-local `asyncio.create_task` for state-changing work;
  enqueue through `services/jobs.py`.
- ⚠️ Forgot to synchronize the **return body** (`PipelineResult`) with the **front-end** `StreamEvent` type after modifying the `PipelineContext` field.
- ⚠️ The streaming path forgets the async generator **`aclose()`** of `llm.astream()` → LangChain may report `GeneratorExit` when disconnecting.
- ⚠️ Injecting too much skill prompt → consuming the token budget of retrieval chunk.
- ⚠️ Put steps that depend on prestate by mistake into `gather` in parallel with `retrieve`.
