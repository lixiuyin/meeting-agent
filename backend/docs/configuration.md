# Configure the system

> The authoritative reference for all Meeting Agent runtime configuration items.
>
> Code location: `backend/src/core/config.py`, default value: `backend/config/main.yaml`.

**Verified against configuration source:** 2026-09-10.

The defaults in this document come from [`backend/config/main.yaml`](../config/main.yaml), not from the `.env` on the current machine. `.env` and operating-system environment variables override YAML. The checked-in `.env.example` is intentionally a small startup template: keep secrets and genuine deployment overrides there, and leave ordinary RAG/memory tuning in YAML. When troubleshooting runtime behavior, inspect the configuration files, environment variables, and final values produced by `Settings()`; the runtime value always wins.

Dated benchmark artifacts may therefore name models that differ from these
defaults. The roles in
[`docs/validation/latest-benchmark.json`](../../docs/validation/latest-benchmark.json)
are verification-time overrides, not a change to `main.yaml` or
`.env.example`. Their results must be interpreted using the publication rules
in [`benchmarking.md`](./benchmarking.md#publishing-benchmark-and-model-results).

## 1. Three-level coverage sequence

Configuration precedence (highest to lowest):

```
Environment variables > .env files > config/main.yaml (default)
(os.environ) (dotenv) (YAML)
```

Implemented using pydantic-settings' `BaseSettings` + `SettingsConfigDict(env_file=".env")`.
YAML is cached by `_load_yaml_config()` and field defaults read nested values
through `_yaml_get(...)`.

```python
# Simplified example
LLM_MODEL: str = _yaml_get("llm", "model", default="gpt-4o-mini")
```

This ensures that code-level defaults are available when any field is left blank in YAML, but can be overridden at runtime using `.env` or environment variables.

## 2. SecretStr

Sensitive values (API key) are uniformly declared as `SecretStr`:

```python
LLM_API_KEY: SecretStr = SecretStr("")
```

`get_secret_value()` must be explicitly used when using it to avoid being accidentally logged. The output of `repr()` is `SecretStr('************')`.

## 3. Core configuration grouping

### 3.1 Path (`constants.py` derived)

| Field               | Default            | Description                         |
| ------------------- | ------------------ | ----------------------------------- |
| `BASE_DIR`          | `PROJECT_ROOT`     | Backend root directory              |
| `UPLOAD_DIR`        | `data/uploads/`    | Upload files to disk                |
| `VECTOR_DB_DIR`     | `data/vectordb/`   | Chroma persistence                  |
| `DB_PATH`           | `data/meetings.db` | SQLite path                         |
| `CUSTOM_SKILLS_DIR` | `data/skills/`     | Persistent custom Skill definitions |

`model_post_init()` will ensure that `UPLOAD_DIR` and `VECTOR_DB_DIR` exist.
The Skill creation API creates `CUSTOM_SKILLS_DIR` lazily; built-in definitions
under `backend/skills/builtin/` remain immutable.

#### 3.1.1 Authentication identity

| Field              | Default | Description                                                                                                                                                                |
| ------------------ | ------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `API_KEY`          | `""`    | Shared backend credential; required outside development/test                                                                                                               |
| `PRINCIPAL_PEPPER` | `""`    | Secret HMAC pepper used to derive an irreversible principal from `API_KEY`; required outside development/test                                                              |
| `PRINCIPAL_ID`     | unset   | Optional stable existing principal, 8–128 alphanumeric/underscore/hyphen characters; use during API-key rotation only after identifying the owner already stored in SQLite |

`PRINCIPAL_ID` pins the authenticated deployment credential to one existing
owner. It does not add accounts, roles, or multi-user authentication. Values
`default` and `dev_*` are rejected outside development because they are shared
development identities. See
[`security-and-tenancy.md`](./security-and-tenancy.md#21-production-and-staging).

### 3.2 LLM

| Field                              | Default       | Description                                                                                                                                              |
| ---------------------------------- | ------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `LLM_BINDING`                      | `openai`      | Provider name, see [`llm-and-traffic.md`](./llm-and-traffic.md)                                                                                          |
| `LLM_MODEL`                        | `gpt-4o-mini` | Model name                                                                                                                                               |
| `LLM_API_KEY`                      | `""`          | **SecretStr**, provider dependency                                                                                                                       |
| `LLM_BASE_URL`                     | `""`          | Custom base URL (OpenAI compatible provider)                                                                                                             |
| `LLM_HOST`                         | `""`          | Local provider address (ollama/lm_studio/vllm)                                                                                                           |
| `LLM_TEMPERATURE`                  | `0.3`         | Generation temperature                                                                                                                                   |
| `LLM_MAX_TOKENS`                   | `2048`        | Single response limit                                                                                                                                    |
| `LLM_REASONING_EFFORT`             | `low`         | OpenRouter reasoning budget; bounded so visible output retains completion capacity                                                                       |
| `LLM_CONTEXT_WINDOW`               | `128000`      | Context window (for prompt truncation strategy)                                                                                                          |
| `LLM_CACHE_ENABLED`                | `True`        | Response caching                                                                                                                                         |
| `LLM_CACHE_TTL_SECONDS`            | `300`         | Cache TTL                                                                                                                                                |
| `LLM_CACHE_MAX_SIZE`               | `512`         | Number of cache entries                                                                                                                                  |
| `LLM_MAX_CONCURRENCY`              | `10`          | The upper limit of concurrent semaphores                                                                                                                 |
| `LLM_RPM`                          | `60`          | Token bucket speed limit                                                                                                                                 |
| `LLM_CIRCUIT_BREAKER_THRESHOLD`    | `5`           | Continuous error trigger open                                                                                                                            |
| `LLM_CIRCUIT_BREAKER_RECOVERY`     | `60`          | open → half-open seconds                                                                                                                                 |
| `LLM_RETRY_MAX_ATTEMPTS`           | `3`           | Number of retries for a single call                                                                                                                      |
| `LLM_GENERATION_TIMEOUT_S`         | `100.0`       | Hard 5–600 second deadline used by ordinary chat/stream calls and meeting-summary generation; fast-path guards may impose a smaller request-local budget |
| `LLM_PROMPT_RESERVE_TOKENS`        | `500`         | prompt reserved token                                                                                                                                    |
| `LLM_HISTORY_BUDGET_TOKENS`        | `4000`        | Dialogue history token budget                                                                                                                            |
| `LLM_HISTORY_BUDGET_CHARS`         | `4000`        | Deprecated compatibility alias; despite the old name, runtime treats this value as the same token budget                                                 |
| `PROMPT_TOTAL_BUDGET_TOKENS`       | `12000`       | prompt total token budget; can be adjusted downward according to the model context window                                                                |
| `ANTHROPIC_PROMPT_CACHE_ENABLED`   | `True`        | Anthropic prompt caching                                                                                                                                 |
| `ANTHROPIC_PROMPT_CACHE_MIN_CHARS` | `1024`        | Minimum number of characters to cache                                                                                                                    |
| `LLM_SUPPORTS_VISION`              | `auto`        | Whether LLM supports vision (`auto`/`true`/`false`)                                                                                                      |

### 3.3 Embedding

| Field                           | Default                  | Description                                                                                           |
| ------------------------------- | ------------------------ | ----------------------------------------------------------------------------------------------------- |
| `EMBEDDING_BINDING`             | `openai`                 | See `services/embedder.py`                                                                            |
| `EMBEDDING_MODEL`               | `text-embedding-3-small` |                                                                                                       |
| `EMBEDDING_API_KEY`             | `""`                     | SecretStr                                                                                             |
| `EMBEDDING_BASE_URL`            | `""`                     |                                                                                                       |
| `EMBEDDING_HOST`                | `""`                     | local provider                                                                                        |
| `EMBEDDING_DIMENSION`           | `1536`                   | Must be consistent with the Chroma collection dimension                                               |
| `EMBEDDING_QUERY_CACHE_ENABLED` | `True`                   | Query vector cache                                                                                    |
| `EMBEDDING_QUERY_CACHE_SIZE`    | `64`                     | Number of cache entries                                                                               |
| `EMBEDDING_STAMPEDE_WAIT_S`     | `0`                      | The upper limit of waiting for embedding cache concurrent miss; `0` uses provider-aware default value |

> ⚠️ Embedding/chunk-shape settings cannot be changed through the live settings API. Change deployment configuration, restart under operator control, and wait for startup reconciliation plus durable file reprocessing to clear `repair_pending` before considering the service ready.

In development with an empty `API_KEY`, a process-random pepper supports
short-lived internal tokens and development identities may change after a
restart. Do not log the pepper or commit it to the repository.

### 3.4 ASR/OCR/TTS

| Field                              | Default           | Description                                                                                                                                                                                                                                                                                         |
| ---------------------------------- | ----------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ASR_PROVIDER`                     | `assemblyai`      | Only supports `assemblyai` (whisper/vibevoice has been removed)                                                                                                                                                                                                                                     |
| `ASR_LANGUAGE`                     | `en`              |                                                                                                                                                                                                                                                                                                     |
| `ASSEMBLYAI_API_KEY`               | —                 | AssemblyAI API key (env-only, do not put YAML)                                                                                                                                                                                                                                                      |
| `ASSEMBLYAI_SPEECH_MODEL`          | `universal-3-pro` | Speech recognition model                                                                                                                                                                                                                                                                            |
| `ASSEMBLYAI_SPEAKER_LABELS`        | `True`            | Enable speaker separation                                                                                                                                                                                                                                                                           |
| `ASSEMBLYAI_LANGUAGE_DETECTION`    | `True`            | Automatic language detection                                                                                                                                                                                                                                                                        |
| `ASSEMBLYAI_POLL_INTERVAL_SECONDS` | `3`               | Polling interval                                                                                                                                                                                                                                                                                    |
| `ASSEMBLYAI_MAX_WAIT_SECONDS`      | `1800`            | Maximum wait time                                                                                                                                                                                                                                                                                   |
| `OCR_PROVIDER`                     | `marker`          | **Routing soft hint** (`user_hint` of `select_parsers`): `marker` / `mineru` / `paddle`; if present in the candidate sequence, it is promoted to the front rather than replacing the `DocumentProfile` route. See [`ingest-pipeline.md`](./ingest-pipeline.md#43-ocr_provider-configuration-prompt) |
| `OCR_LANGUAGE`                     | `en`              |                                                                                                                                                                                                                                                                                                     |
| `OCR_DPI`                          | `300`             |                                                                                                                                                                                                                                                                                                     |

### 3.5 Parser

| Field                          | Default                                                           | Description                                                                                         |
| ------------------------------ | ----------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| `MARKER_BASE_URL`              | `https://www.datalab.to/api/v1/marker`                            | Marker Cloud API Endpoint                                                                           |
| `MARKER_API_KEY`               | `""`                                                              | SecretStr                                                                                           |
| `MARKER_MAX_WAIT_SECONDS`      | `300`                                                             | Maximum Marker task wait                                                                            |
| `MINERU_BASE_URL`              | `https://mineru.net/api/v4`                                       | MinerU Cloud API endpoint (v4 batch flow root)                                                      |
| `MINERU_API_KEY`               | `""`                                                              | SecretStr                                                                                           |
| `MINERU_MAX_WAIT_SECONDS`      | `600`                                                             | MinerU task maximum wait                                                                            |
| `MINERU_RESULT_ALLOWED_HOSTS`  | `mineru.net,.mineru.net,cdn-mineru.openxlab.org.cn,.aliyuncs.com` | Comma-separated exact hosts or dot-prefixed suffixes allowed for result downloads (SSRF protection) |
| `PADDLEOCR_BASE_URL`           | `""`                                                              | PaddleOCR Cloud API Endpoint                                                                        |
| `PADDLEOCR_API_KEY`            | `""`                                                              | SecretStr                                                                                           |
| `PARSER_HTTP_TIMEOUT_SECONDS`  | `180.0`                                                           | Parse HTTP request timeout                                                                          |
| `PARSER_POLL_INTERVAL_SECONDS` | `2.0`                                                             | Task polling interval                                                                               |

> For timeout-related fields (`PARSE_TIMEOUT_SECONDS`, `PARSE_TIMEOUT_PER_MB_SECONDS`, `PARSE_TIMEOUT_MAX_SECONDS`), see [3.12 Parser / Upload](#312-parserupload).

### 3.6 Vision

| Field                             | Default | Description                                                                                      |
| --------------------------------- | ------- | ------------------------------------------------------------------------------------------------ |
| `VISION_MODEL`                    | `""`    | OpenAI compatible multi-modal model name                                                         |
| `VISION_API_KEY`                  | `""`    | SecretStr                                                                                        |
| `VISION_BASE_URL`                 | `""`    | Custom endpoint                                                                                  |
| `VISION_REASONING_EFFORT`         | `none`  | OpenRouter vision reasoning effort; `none` preserves the response budget for caption/OCR content |
| `VISION_COMBINED_MAX_TOKENS`      | `2048`  | Output budget for combined caption, OCR, and semantics JSON                                      |
| `VISION_RETRY_MAX_ATTEMPTS`       | `3`     | Number of retries                                                                                |
| `VISION_RETRY_BASE_DELAY_SECONDS` | `0.5`   | Retry initial delay                                                                              |
| `VISION_RETRY_MAX_DELAY_SECONDS`  | `2.0`   | Maximum retry delay                                                                              |
| `VISION_CAPTION_MIN_CHARS`        | `12`    | Minimum number of characters to generate description                                             |
| `VISION_OCR_MIN_CHARS`            | `6`     | OCR output minimum number of characters                                                          |

### 3.7 TTS

| Field          | Default | Description     |
| -------------- | ------- | --------------- |
| `TTS_BINDING`  | `""`    | Optional        |
| `TTS_MODEL`    | `""`    |                 |
| `TTS_API_KEY`  | `""`    | SecretStr       |
| `TTS_BASE_URL` | `""`    | Custom endpoint |
| `TTS_VOICE`    | `""`    |                 |
| `TTS_SPEED`    | `1.0`   |                 |

### 3.8 Web Search

| Field                | Default | Description                                                    |
| -------------------- | ------- | -------------------------------------------------------------- |
| `SEARCH_BINDING`     | `exa`   | empty = disabled; `duckduckgo`/`tavily`/`bing`/`serpapi`/`exa` |
| `SEARCH_API_KEY`     | `""`    | Not required by DuckDuckGo                                     |
| `SEARCH_REGION`      | `wt-wt` |                                                                |
| `SEARCH_MAX_RESULTS` | `5`     |                                                                |
| `SEARCH_TIMEOUT`     | `10`    | seconds                                                        |

### 3.9 RAG

See [`rag.md`](./rag.md) for detailed explanation. Field list:

| Field                                                       | Default                               | Description                                                                                                                                                                                           |
| ----------------------------------------------------------- | ------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `DISTANCE_METRIC`                                           | `l2`                                  | The configuration value is `l2` or `cosine`; the current vector filtering logic is processed according to the "lower is better" semantics of distance-type scores                                     |
| `CHUNK_SIZE_TOKENS` / `CHUNK_OVERLAP_TOKENS`                | `384` / `64`                          | Active language-neutral index granularity; changing it requires reindexing                                                                                                                            |
| `CHUNK_SIZE` / `CHUNK_OVERLAP`                              | `1024` / `128`                        | Legacy compatibility values                                                                                                                                                                           |
| `TOP_K`                                                     | `8`                                   |                                                                                                                                                                                                       |
| `QUERY_REWRITE_ENABLED`                                     | `True`                                |                                                                                                                                                                                                       |
| `QUERY_REWRITE_MODEL`                                       | `""` (=`LLM_MODEL`)                   |                                                                                                                                                                                                       |
| `QUERY_REWRITE_TIMEOUT_SECONDS`                             | `10`                                  | Query rewrite timeout (seconds)                                                                                                                                                                       |
| `RAG_FAST_PATH_ENABLED` / `RAG_FAST_PATH_MAX_WORDS`         | `True` / `12`                         | Short standalone fact lookups skip remote rewrite and resolver work                                                                                                                                   |
| `RAG_FAST_PATH_RETRIEVAL_MODE`                              | `bm25`                                | Local lexical retrieval for the fast path; explicit request `rag_mode` still wins                                                                                                                     |
| `RAG_FAST_PATH_MAX_OUTPUT_TOKENS`                           | `256`                                 | Per-request completion cap for the fast path                                                                                                                                                          |
| `RAG_FAST_PATH_FIRST_TOKEN_TIMEOUT_S`                       | `10.0`                                | Maximum wait for the first user-visible token on a validated fast-path stream                                                                                                                         |
| `RAG_FAST_PATH_STREAM_STALL_TIMEOUT_S`                      | `15.0`                                | Maximum gap between user-visible tokens on a validated fast-path stream                                                                                                                               |
| `RAG_FAST_PATH_TOTAL_TIMEOUT_S`                             | `30.0`                                | Safety ceiling for validated fast-path generation; the 2.5s latency target is measured as an SLO, not used as a cancellation deadline                                                                 |
| `CHAT_STREAM_LATENCY_GUARD_ENABLED`                         | `True`                                | Enables atomic-fact probing. A post-retrieval evidence filter promotes weak or cross-source results to the full path; only validated probes may skip reranking or return labelled excerpts on timeout |
| `SCORE_THRESHOLD`                                           | `1.5`                                 |                                                                                                                                                                                                       |
| `RERANKER_BINDING` / `RERANKER_MODEL`                       | disabled / `cohere/rerank-4-pro`      | `cohere` SDK, generic `http`, local `bge`, or disabled                                                                                                                                                |
| `RERANKER_API_KEY` / `RERANKER_BASE_URL`                    | `""` / `https://openrouter.ai/api/v1` | Fall back to `LLM_API_KEY` when the API key is empty                                                                                                                                                  |
| `RERANKER_TOP_N`                                            | `20`                                  | broad recall will dynamically enlarge `top_n` according to candidate size and file coverage                                                                                                           |
| `RERANKER_MIN_SCORE`                                        | `0.15`                                | legacy configuration field; current rerank postprocessing uses the following two scope-specific thresholds                                                                                            |
| `RERANKER_UNSCOPED_MIN_SCORE` / `RERANKER_SCOPED_MIN_SCORE` | `0.05` / `0.10`                       | Broad / Scoped rerank lowest score                                                                                                                                                                    |
| `RERANKER_TIMEOUT_SECONDS`                                  | `30.0`                                | Reorder timeout                                                                                                                                                                                       |
| `RERANKER_BATCH_SIZE`                                       | `200`                                 | Reorder batch size                                                                                                                                                                                    |
| `PARENT_CHILD_ENABLED`                                      | `False`                               |                                                                                                                                                                                                       |
| `CHILD_CHUNK_SIZE_TOKENS` / `CHILD_CHUNK_OVERLAP_TOKENS`    | `160` / `24`                          | Active parent-child retrieval granularity                                                                                                                                                             |
| `CHILD_CHUNK_SIZE` / `CHILD_CHUNK_OVERLAP`                  | `256` / `32`                          | Legacy compatibility values                                                                                                                                                                           |
| `HYBRID_SEARCH_ENABLED` / `HYBRID_ALPHA`                    | `True` / `0.5`                        |                                                                                                                                                                                                       |
| `HYBRID_MULTIMODAL_ALPHA`                                   | `0.5`                                 | Multi-modal hybrid search weight                                                                                                                                                                      |
| `RAG_RERANK_FETCH_MULTIPLIER`                               | `6`                                   |                                                                                                                                                                                                       |
| `RAG_PERSIST_INTERVAL_SECONDS`                              | `30.0`                                |                                                                                                                                                                                                       |
| `SEMANTIC_CHUNKING_ENABLED`                                 | `False`                               |                                                                                                                                                                                                       |
| `NON_TEXT_CHUNKING_STRATEGY`                                | `native`                              | Non-text chunking strategy: `native`/`text`                                                                                                                                                           |
| `MULTI_QUERY_ENABLED` / `MULTI_QUERY_COUNT`                 | `False` / `3`                         |                                                                                                                                                                                                       |
| `RAG_RETRIEVER_PROVIDER`                                    | `hybrid`                              | Explicit strategy: `vector`/`hybrid`/`multimodal`/`hybrid_multimodal`; `native` is a deprecated alias for `vector`                                                                                    |
| `RAGANYTHING_ENABLED`                                       | `False`                               | RAGAnything development switch; non-dev startup is blocked pending upstream security fixes                                                                                                            |
| `RAGANYTHING_FALLBACK_TO_NATIVE`                            | `True`                                | Fall back to native when RAGAnything fails                                                                                                                                                            |
| `RAGANYTHING_WORKING_DIR`                                   | `""`                                  | Default `VECTOR_DB_DIR/raganything`                                                                                                                                                                   |
| `RAGANYTHING_INDEX_TIMEOUT_SECONDS`                         | `120.0`                               |                                                                                                                                                                                                       |
| `RAGANYTHING_QUERY_TIMEOUT_SECONDS`                         | `30.0`                                |                                                                                                                                                                                                       |
| `RAGANYTHING_LLM_TIMEOUT_SECONDS`                           | `90.0`                                |                                                                                                                                                                                                       |
| `RAG_FILE_SCOPING_MODE`                                     | `router_and_funnel`                   | `router_and_funnel`/`funnel_only`/`router_pre_filter`/`router_only`                                                                                                                                   |
| `COMBINED_EXTRACTION_ENABLED`                               | `True`                                | Combine fact + entity extraction (single LLM call)                                                                                                                                                    |
| `VECTOR_SEARCH_TIMEOUT_S`                                   | `8.0`                                 | Vector search timeout (seconds)                                                                                                                                                                       |
| `CHROMA_REMOTE_ENABLED` / `CHROMA_TRUST_REMOTE_CODE`        | `False` / `False`                     | Must remain false; production uses local `PersistentClient` only                                                                                                                                      |
| `MULTIMODAL_ATTACH_GATE_ENABLED`                            | `True`                                | Multimodal image attachment gating (detected by visual query)                                                                                                                                         |
| `RAG_SIBLING_CORETRIEVE_ENABLED`                            | `True`                                | Sibling chunks searched together                                                                                                                                                                      |
| `RAG_SIBLING_CORETRIEVE_PER_ANCHOR`                         | `1`                                   | Number of brothers per anchor                                                                                                                                                                         |
| `RAG_SIBLING_CORETRIEVE_MAX_TOTAL`                          | `4`                                   | Maximum total number of brothers                                                                                                                                                                      |
| `RAG_CONTENT_TYPE_RERANK_ENABLED`                           | `True`                                | Content type bias reranking                                                                                                                                                                           |
| `RAG_INDEX_TABLES`                                          | `True`                                | Index tables                                                                                                                                                                                          |
| `RAG_INDEX_IMAGE_CAPTIONS`                                  | `True`                                | Index image description                                                                                                                                                                               |
| `RAG_IMAGE_OCR_MIN_LENGTH`                                  | `15`                                  | OCR minimum length                                                                                                                                                                                    |
| `CONTEXT_LOAD_TIMEOUT_S`                                    | `8.0`                                 | Context load timeout (seconds)                                                                                                                                                                        |
| `MEMORY_CONTEXT_MAX_TOKENS`                                 | `800`                                 | Memory-context token limit                                                                                                                                                                            |
| `ENTITY_CONTEXT_MAX_TOKENS`                                 | `600`                                 | Entity-context token limit                                                                                                                                                                            |
| `SESSION_CONTEXT_MAX_TOKENS`                                | `800`                                 | Session context token upper limit                                                                                                                                                                     |
| `RRF_K_PARAM`                                               | `60`                                  | RRF K parameter                                                                                                                                                                                       |
| `SEMANTIC_EMBED_TIMEOUT_S`                                  | `15.0`                                | Semantic embedding timeout (seconds)                                                                                                                                                                  |
| `STREAM_CONCURRENT_LIMIT`                                   | `20`                                  | Streaming concurrency upper limit                                                                                                                                                                     |
| `FALLBACK_BREAKER_THRESHOLD`                                | `3`                                   | Fallback breaker threshold                                                                                                                                                                            |
| `FALLBACK_BREAKER_COOLDOWN_SECONDS`                         | `30.0`                                | Fallback fuse cooling (seconds)                                                                                                                                                                       |

#### Audio segmentation

| Field                               | Default | Description                     |
| ----------------------------------- | ------- | ------------------------------- |
| `AUDIO_SEMANTIC_BOUNDARY_ENABLED`   | `False` | Semantic segmentation switch    |
| `AUDIO_SEMANTIC_BOUNDARY_THRESHOLD` | `0.5`   | Semantic segmentation threshold |
| `AUDIO_SEMANTIC_MIN_SEGMENTS`       | `2`     | Minimum number of segments      |
| `AUDIO_SEMANTIC_MAX_SEGMENTS`       | `20`    | Maximum number of segments      |
| `AUDIO_SPEAKER_IN_CONTENT`          | `True`  | Content contains speaker tag    |
| `AUDIO_SPLIT_ON_SPEAKER_CHANGE`     | `True`  | Switch chunking by speaker      |

#### Unscoped diversity

| Field                        | Default | Description                                       |
| ---------------------------- | ------- | ------------------------------------------------- |
| `UNSCOPED_DIVERSITY_ENABLED` | `True`  | Balance by conference when scope is not defined   |
| `UNSCOPED_MAX_PER_MEETING`   | `5`     | Maximum chunk contribution in a single conference |
| `UNSCOPED_FETCH_MULTIPLIER`  | `4`     | Oversampling multiple                             |

#### Hierarchical (funnel) RAG

| Field                                             | Default      | Description                                   |
| ------------------------------------------------- | ------------ | --------------------------------------------- |
| `RAG_FUNNEL_FETCH_MULTIPLIER`                     | `10`         | wide fetch oversampling factor                |
| `RAG_FUNNEL_TOP_MEETINGS`                         | `12`         | Maximum number of meetings in the first phase |
| `RAG_FUNNEL_TOP_FILES`                            | `12`         | Maximum number of files in the second stage   |
| `RAG_FUNNEL_MIN_POOL_SIZE`                        | `12`         | Below this value triggers narrow fallback     |
| `RAG_FUNNEL_AGGREGATION`                          | `top_k_mean` | `top_k_mean`/`max`/`count`                    |
| `RAG_FUNNEL_AGG_TOP_K`                            | `3`          | k value of top_k_mean                         |
| `RAG_FUNNEL_AGGREGATION_ALPHA`                    | `0.85`       | Aggregate blend alpha                         |
| `RAG_FUNNEL_TITLE_PRIOR_ENABLED`                  | `True`       | Bonus points for title matching               |
| `RAG_FUNNEL_TITLE_PRIOR_WEIGHT`                   | `0.05`       | Bonus points per word                         |
| `RAG_FUNNEL_TITLE_PRIOR_CAP`                      | `0.15`       | Title bonus limit                             |
| `RAG_FUNNEL_FILE_PRIOR_ENABLED`                   | `True`       | Bonus points for file title matching          |
| `RAG_FUNNEL_FILE_PRIOR_WEIGHT`                    | `0.10`       | Bonus points per word                         |
| `RAG_FUNNEL_FILE_PRIOR_CAP`                       | `0.30`       | Maximum bonus points for file title           |
| `RAG_FUNNEL_FILE_PRIOR_FULL_MATCH_BONUS`          | `0.20`       | Bonus points for full match                   |
| `RAG_FUNNEL_FILE_PRIOR_MODE`                      | `additive`   | `additive`/`multiplicative`                   |
| `RAG_FUNNEL_WIDE_K_MIN` / `RAG_FUNNEL_WIDE_K_MAX` | `0` / `0`    | Adaptive wide_k range (0=disabled)            |
| `RAG_FUNNEL_MULTIMODAL_ENABLED`                   | `True`       | Multimodal storage included in funnel         |
| `RAG_FUNNEL_NARROW_MIN_EVIDENCE`                  | `0.15`       | Chunk aggregate file score lower limit        |
| `RAG_FUNNEL_EVIDENCE_MODE`                        | `ratio`      | `absolute`/`ratio`/`percentile`               |
| `RAG_BROAD_RECALL_SCOPE_CAP`                      | `10`         | The maximum number of final scope files       |
| `RAG_FUNNEL_MERGE_STRATEGY`                       | `rrf`        | `rrf`/`zigzag`                                |
| `RAG_FUNNEL_RRF_K`                                | `60`         | RRF K constant                                |
| `RAG_BROAD_RECALL_MQ_MERGE`                       | `rrf`        | Multiple query variant merging strategy       |

#### Conversational anchor

| Field                                      | Default | Description                              |
| ------------------------------------------ | ------- | ---------------------------------------- |
| `RAG_ANCHOR_ENABLED`                       | `True`  | Main switch                              |
| `RAG_ANCHOR_TTL_MINUTES`                   | `30`    | Anchor expiration threshold (minutes)    |
| `RAG_ANCHOR_TTL_MODE`                      | `fixed` | `fixed`/`sliding`                        |
| `RAG_ANCHOR_NARROW_FETCH_MULTIPLIER`       | `5`     | case 1 oversampling                      |
| `RAG_ANCHOR_NARROW_FETCH_MULTIPLIER_CASE2` | `3`     | case 2 oversampling                      |
| `RAG_ANCHOR_NARROW_RRF_WEIGHT`             | `0.5`   | narrow fetch RRF weight                  |
| `RAG_ANCHOR_MAX_IDS`                       | `8`     | Storage ID limit                         |
| `RAG_ANCHOR_BOOST_IN_BROAD_RECALL`         | `True`  | Inject anchor files into broad recall    |
| `RAG_ANCHOR_QUOTA_RATIO`                   | `0.5`   | anchor file quota ratio                  |
| `RAG_ANCHOR_ONLY_SCORE_FLOOR_RATIO`        | `0.8`   | anchor-only file score lower limit ratio |

#### Pre-rerank dedup / speaker filter

| Field                            | Default | Description                           |
| -------------------------------- | ------- | ------------------------------------- |
| `RAG_PRE_RERANK_DEDUP_ENABLED`   | `True`  | n-gram deduplication before reranking |
| `RAG_PRE_RERANK_DEDUP_THRESHOLD` | `0.92`  | Deduplication similarity threshold    |
| `RAG_SPEAKER_FILTER_PUSHDOWN`    | `False` | Speaker filter pushdown to Chroma     |

#### Broad recall mode

| Field                                  | Default | Description                               |
| -------------------------------------- | ------- | ----------------------------------------- |
| `RAG_MIN_CHUNKS_PER_FILE`              | `3`     | Minimum number of chunks per file         |
| `RAG_FAIR_ADAPTIVE_CHUNKS`             | `True`  | Adaptive chunk allocation                 |
| `RAG_FAIR_SIZE_FACTOR_ENABLED`         | `True`  | Adjust allocation by file size            |
| `RAG_FAIR_CONCURRENCY`                 | `8`     | Maximum parallel file retrieval           |
| `BROAD_RECALL_MAX_FILES`               | `50`    | legacy enumeration fallback SQL LIMIT     |
| `TOP_K_MEETING_SCOPED_FLOOR`           | `16`    | TOP_K lower limit when meeting is limited |
| `SUMMARY_INTENT_TOP_K`                 | `16`    | Summary intent top_k lower limit          |
| `RAG_BROAD_RECALL_MULTI_QUERY_ENABLED` | `False` | broad recall multi query                  |

#### Summary vector router

| Field                                          | Default | Description                                                                                                                                                |
| ---------------------------------------------- | ------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `RAG_SUMMARY_ROUTER_ENABLED`                   | `True`  | Embed pre-filter by file summary                                                                                                                           |
| `RAG_SUMMARY_ROUTER_TOP_FILES`                 | `12`    | Number of files returned by the route                                                                                                                      |
| `RAG_SUMMARY_ROUTER_MIN_SCORE`                 | `0.0`   | Distance/similarity threshold                                                                                                                              |
| `RAG_SUMMARY_ROUTER_FALLBACK_TO_CHUNK`         | `True`  | Empty result fallback                                                                                                                                      |
| `RAG_SUMMARY_ROUTER_HYBRID_ENABLED`            | `True`  | Summary routing hybrid mode                                                                                                                                |
| `RAG_SUMMARY_ROUTER_HYBRID_ALPHA`              | `0.6`   | Hybrid weight                                                                                                                                              |
| `RAG_MEETING_SUMMARY_ROUTER_ENABLED`           | `True`  | Meeting-summary pre-filtering in unscoped mode                                                                                                             |
| `RAG_MEETING_SUMMARY_ROUTER_TOP_MEETINGS`      | `15`    | Number of meetings returned by routing                                                                                                                     |
| `RAG_MEETING_SUMMARY_ROUTER_MIN_SCORE`         | `0.4`   | Similarity threshold                                                                                                                                       |
| `RAG_MEETING_SUMMARY_ROUTER_MIN_HITS`          | `1`     | Minimum number of hits                                                                                                                                     |
| `RAG_MEETING_SUMMARY_ROUTER_EXPLORATION_RATIO` | `0.2`   | In unscoped recall, reserve this share of the final file-selection budget for globally selected files outside meeting-summary priors; constrained to 0–0.5 |
| `RAG_MEETING_SUMMARY_ROUTER_TIMEOUT_S`         | `1.5`   | Routing timeout (seconds, 0=infinite)                                                                                                                      |
| `FILE_SUMMARY_CONTEXT_CHARS`                   | `1400`  | Number of characters injected per file summary                                                                                                             |
| `MEETING_SUMMARY_CONTEXT_CHARS`                | `2800`  | Number of characters injected into each meeting summary                                                                                                    |
| `MEETING_SUMMARY_BROAD_INJECT_CAP`             | `20`    | Inject all summaries when ≤N meetings                                                                                                                      |
| `FILE_SUMMARY_BROAD_INJECT_CAP`                | `50`    | Inject all summaries when ≤N files                                                                                                                         |

#### Query resolver

| Field                           | Default | Description                        |
| ------------------------------- | ------- | ---------------------------------- |
| `RESOLVER_ENABLED`              | `True`  | History-aware query parsing switch |
| `RESOLVER_HISTORY_TURNS`        | `3`     | Use the last N rounds              |
| `RESOLVER_HISTORY_TOKEN_BUDGET` | `1500`  | Historical token hard cap          |
| `RESOLVER_TIMEOUT_S`            | `4.0`   | Parsing timeout (seconds)          |

#### Data retention

| Field                         | Default | Description                          |
| ----------------------------- | ------- | ------------------------------------ |
| `CHAT_MESSAGE_RETENTION_DAYS` | `180`   | Number of days to keep chat messages |
| `DECAY_STATE_RETENTION_DAYS`  | `365`   | Number of days to retain decay state |

### 3.10 Memory

| Field                                    | Default             | Description                                                                                     |
| ---------------------------------------- | ------------------- | ----------------------------------------------------------------------------------------------- |
| `MEMORY_AUTO_EXTRACT`                    | `True`              | Automatically extracted after each round of dialogue                                            |
| `MEMORY_MAX_FACTS_PER_TURN`              | `3`                 | Maximum number of facts in a single round                                                       |
| `MEMORY_EXTRACTION_MODEL`                | `""` (=`LLM_MODEL`) | Model for extraction                                                                            |
| `MEMORY_INGEST_CHUNK_CHARS`              | `5000`              | Source-window size for post-ingest memory extraction                                            |
| `MEMORY_INGEST_CHUNK_OVERLAP`            | `300`               | Character overlap between adjacent evidence windows                                             |
| `MEMORY_INGEST_MAX_CHUNKS_PER_FILE`      | `20`                | Per-file scheduling burst size; all source windows are still enqueued                           |
| `MEMORY_DECAY_ENABLED`                   | `True`              |                                                                                                 |
| `MEMORY_DECAY_INTERVAL_HOURS`            | `24`                | Background loop period                                                                          |
| `MEMORY_DECAY_RATE_PER_DAY`              | `0.01`              | ~1%/day decay rate                                                                              |
| `MEMORY_TTL_DAYS`                        | `90`                | Hard expiry                                                                                     |
| `MEMORY_MAX_CONTEXT_ITEMS`               | `6`                 | Maximum number of prompts to be injected                                                        |
| `MEMORY_MAX_PER_USER`                    | `500`               | Single user memory limit (exceeded and eliminated according to importance)                      |
| `MEMORY_DEDUP_THRESHOLD`                 | `0.75`              | Deduplication semantic similarity threshold                                                     |
| `MEMORY_CLUSTER_THRESHOLD`               | `0.75`              | Similarity threshold for semantic clustering union-find                                         |
| `MEMORY_INITIAL_IMPORTANCE`              | `3`                 | Default importance of new memory (1-5)                                                          |
| `MEMORY_MIN_IMPORTANCE`                  | `1`                 | Lower limit of importance                                                                       |
| `MEMORY_MAX_IMPORTANCE`                  | `5`                 | Maximum importance                                                                              |
| `MEMORY_SCORING_SEMANTIC_WEIGHT`         | `0.35`              | Semantic query relevance weight                                                                 |
| `MEMORY_SCORING_DECAY_WEIGHT`            | `0.15`              | Independently decayed freshness weight                                                          |
| `MEMORY_SCORING_IMPORTANCE_WEIGHT`       | `0.25`              | Stable salience weight (legacy key name)                                                        |
| `MEMORY_SCORING_CONFIDENCE_WEIGHT`       | `0.15`              | Evidence/extraction confidence weight                                                           |
| `MEMORY_SCORING_USEFULNESS_WEIGHT`       | `0.10`              | Explicit downstream usefulness weight                                                           |
| `MEMORY_MULTI_HOP_ENABLED`               | `True`              | Bounded second-hop recall with multilingual bridge selection                                    |
| `MEMORY_MULTI_HOP_SEED_COUNT`            | `3`                 | Maximum grounded bridge facts reserved per search                                               |
| `MEMORY_CONSOLIDATION_ENABLED`           | `False`             | Opt-in LLM merge of similar memories                                                            |
| `MEMORY_PROFILE_ENABLED`                 | `False`             | Opt-in LLM-driven profile refresh                                                               |
| `MEMORY_PROFILE_REFRESH_INTERVAL`        | `50`                | Refresh every N interactions                                                                    |
| `MEMORY_CONSOLIDATION_MIN_CLUSTER`       | `3`                 | Clustering minimum cluster                                                                      |
| `MEMORY_CONSOLIDATION_WINDOW_DAYS`       | `2`                 | Merge seed window (days)                                                                        |
| `MEMORY_AUTO_EXTRACT_INITIAL_IMPORTANCE` | `1.0`               | Automatically extract the initial importance of memory                                          |
| `MEMORY_EXTRACTION_INCLUDE_EXISTING`     | `True`              | Include existing memory when extracting (anti-duplication)                                      |
| `MEMORY_SEMANTIC_CLUSTER_ENABLED`        | `False`             | Opt-in vector clustering (otherwise text overlap)                                               |
| `KNOWLEDGE_GRAPH_ENABLED`                | `False`             | Opt-in entity/relation extraction                                                               |
| `ENTITY_ALIAS_MERGE_THRESHOLD`           | `0.85`              | Cosine similarity threshold for entity alias merging                                            |
| `MEMORY_EXTRACTION_MODE`                 | `balanced`          | `precise`/`balanced`/`aggressive`                                                               |
| `ENTITY_RELATIONS_LIMIT`                 | `50`                | The upper limit of the relationship returned by a single entity                                 |
| `GLOBAL_MEMORY_LIMIT`                    | `3`                 | The upper limit of global memory when there is scope                                            |
| `SCOPED_MEMORY_STRICT`                   | `True`              | Exclude unmarked memories when scope is activated; explicit user-profile memories remain global |
| `MEMORY_SEARCH_OVERSAMPLE_FACTOR`        | `5`                 | Vector library oversampling multiple (scope post-filtering)                                     |
| `SESSION_MAX_HISTORY`                    | `50`                | Historical message upper limit                                                                  |

| `SESSION_MAX_TOKENS` | `4096` | Historical message token upper limit |
| `SESSION_SUMMARY_ENABLED` | `True` | |
| `SESSION_SUMMARY_MIN_TURNS` | `4` | Generated after reaching |
| `SESSION_SUMMARY_MAX_ITEMS` | `3` | |
| `SESSION_SUMMARY_MAX_MESSAGES` | `100` | |
| `SESSION_SUMMARY_IDLE_MINUTES` | `15` | Idle auto-summary interval (minutes) |
| `SESSION_SUMMARY_STARTUP_BACKFILL` | `False` | Batch backfill summary on startup |
| `SESSION_CONTEXT_SKIP_THRESHOLD` | `3` | Minimum number of choices to skip across session summaries |
| `SKILL_MATCHING_ENABLED` | `True` | Enable/disable skill matching |
| `SKILL_MATCH_TIMEOUT_S` | `15.0` | skill match timeout |
| `SKILL_ROUTE_TIMEOUT_S` | `15.0` | skill routing timeout (seconds) |
| `SKILL_ROUTING_MIN_SIMILARITY` | `0.35` | Minimum similarity of skill routing |
| `WEB_SEARCH_TIMEOUT_S` | `8.0` | Web search timeout (seconds) |

Vector deletion uses a database-backed retry queue.
`VECTOR_DELETION_MAX_ATTEMPTS` defaults to `10` (range 1–100); exhausted work
enters dead-letter state instead of being silently discarded.

See [`memory-and-kg.md`](./memory-and-kg.md).

### 3.11 Server

| Field                        | Default                           | Description                                                                                                     |
| ---------------------------- | --------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `ENVIRONMENT`                | `dev`                             | `dev`/`staging`/`production`; `prod` is normalized to `production`; non-dev requires `API_KEY` + `CORS_ORIGINS` |
| `HOST`                       | `0.0.0.0`                         |                                                                                                                 |
| `PORT`                       | `7008`                            | Host-side development port; containers override it to `8000`                                                    |
| `DURABLE_JOB_EXECUTION_MODE` | `embedded`                        | `embedded` starts the in-process worker pool; `off` leaves committed jobs queued                                |
| `DURABLE_JOB_WORKERS`        | `2`                               | Embedded claim/execution slots (1–8); not additional Uvicorn processes                                          |
| `DURABLE_JOB_LEASE_SECONDS`  | `300`                             | Renewable ownership lease for claimed jobs                                                                      |
| `DURABLE_JOB_POLL_SECONDS`   | `1.0`                             | Idle polling interval; producers also wake workers after commit                                                 |
| `CORS_ORIGINS`               | `""`                              | Comma separated                                                                                                 |
| `TRUSTED_PROXIES`            | `""`                              | For `X-Forwarded-For` trust chain                                                                               |
| `TRUSTED_HOSTS`              | `""`                              | Trusted hosts list                                                                                              |
| `API_KEY`                    | `""`                              | SecretStr, **empty=dev mode**                                                                                   |
| `MCP_API_URL`                | `http://127.0.0.1:7008/api/v1`    | Canonical API target used by the thin MCP adapter                                                               |
| `MCP_API_KEY`                | `""`                              | Downstream API key; falls back to `API_KEY`                                                                     |
| `MCP_TRANSPORT`              | `stdio`                           | `stdio`, loopback `sse`, or loopback `streamable-http`                                                          |
| `MCP_HOST` / `MCP_HTTP_PORT` | `127.0.0.1` / `9000`              | MCP listener; non-stdio transport refuses non-loopback binding                                                  |
| `IDEMPOTENCY_OLD_KEYS`       | `""`                              | Old keys for decrypting idempotent payloads during key rotation (comma separated)                               |
| `SECURITY_HEADERS_ENABLED`   | `True`                            | Security response headers                                                                                       |
| `SECURITY_HSTS_MAX_AGE`      | `31536000`                        | HSTS max-age                                                                                                    |
| `SECURITY_FRAME_OPTIONS`     | `DENY`                            | X-Frame-Options                                                                                                 |
| `SECURITY_REFERRER_POLICY`   | `strict-origin-when-cross-origin` | Referrer-Policy                                                                                                 |
| `SECURITY_CSP`               | `default-src 'self'; ...`         | Content-Security-Policy                                                                                         |

### 3.12 Parser/Upload

| Field                                          | Default   | Description                                     |
| ---------------------------------------------- | --------- | ----------------------------------------------- |
| `MAX_PARSE_PAGES`                              | `1000`    |                                                 |
| `PARSE_TIMEOUT_SECONDS`                        | `300`     | Total single file parsing timeout               |
| `PARSE_TIMEOUT_PER_MB_SECONDS`                 | `2`       | Dynamic timeout by file size                    |
| `PARSE_TIMEOUT_MAX_SECONDS`                    | `900`     | Dynamic timeout limit                           |
| `MAX_UPLOAD_SIZE_MB`                           | `500`     |                                                 |
| `PARSER_MAX_IMAGES_PER_PAGE`                   | `20`      |                                                 |
| `PARSER_MAX_IMAGE_BYTES`                       | `8388608` |                                                 |
| `DOC_CLEAN_REPETITION_MIN_PAGES`               | `3`       | Minimum number of pages for repeated rows       |
| `DOC_CLEAN_REPETITION_MIN_RATIO`               | `0.6`     | Minimum page ratio for repeated rows            |
| `DOC_CLEAN_HEADER_FOOTER_MAX_LINES`            | `2`       | Number of header/footer evaluation lines        |
| `DOC_CLEAN_REPETITION_MAX_LINE_LENGTH`         | `120`     | Maximum line length for repeat detection        |
| `MEETING_AUTO_SUMMARIZE_FILES`                 | `True`    |                                                 |
| `PER_FILE_SUMMARY_INPUT_MAX_TOKENS`            | `8000`    |                                                 |
| `EXTRACTION_INPUT_MAX_TOKENS`                  | `1500`    | Extract LLM input token upper limit             |
| `EXTRACTION_MIN_ANSWER_CHARS`                  | `50`      | Extract the minimum number of answer characters |
| `MULTIMODAL_CAPTIONING_ENABLED`                | `True`    |                                                 |
| `VISION_COMBINED_EXTRACTION_ENABLED`           | `True`    | Vision + Extract Merge                          |
| `MULTIMODAL_CAPTION_OCR_DEDUP_ENABLED`         | `True`    | OCR/Description deduplication                   |
| `MULTIMODAL_CAPTION_OCR_DEDUP_TIMEOUT_SECONDS` | `8.0`     | Deduplication timeout                           |
| `VIDEO_KEYFRAMES_ENABLED`                      | `False`   |                                                 |

## 4. Environment variable naming rules

pydantic-settings matches field names in uppercase by default, no additional prefix is required. For example:

```bash
# .env
LLM_BINDING=anthropic
LLM_API_KEY=sk-ant-...
EMBEDDING_BINDING=ollama
EMBEDDING_HOST=http://localhost:11434
API_KEY=super-secret
LOG_FORMAT=json
```

`LOG_FORMAT` and `LOG_LEVEL` are not `Settings` fields. Logging reads them
directly from `os.environ`; their defaults are `text` and `INFO` respectively.

## 5. Runtime update: `PUT /api/v1/settings`

- Updates affect the in-memory `settings` object only and are not written back to disk.
- `reset_*()` of each subsystem will be called to trigger singleton reconstruction:
  - `reset_llm()` / `reset_embeddings()` / `reset_vectorstore()` / `reset_reranker()` / `reset_query_rewriter()`
- `GET /settings` exposes `activation_policy` groups for every backend field. Embedding model/dimension and chunk-shape changes are rejected with structured `SETTINGS_REINDEX_REQUIRED` or `SETTINGS_RESTART_REQUIRED` errors. Apply them through a controlled restart; startup reconciliation verifies the committed generation/count/checksum manifest and queues incompatible files for durable reprocessing without recertifying partial physical state.
- Invalid after process restart - persistence configuration still needs to be changed `.env` or `config/main.yaml`

## 6. Typical scenario

### 6.1 Local LLM + Cloud ASR (Ollama + AssemblyAI + BGE reranker)

> ⚠️ `RERANKER_BINDING=bge` and `EMBEDDING_BINDING=huggingface` dependencies optional
> `huggingface` extra (pulls `sentence-transformers` + `torch`, about 3 GB under Linux).
> Installation command: `uv sync --extra huggingface`

```bash
LLM_BINDING=ollama
LLM_HOST=http://localhost:11434
LLM_MODEL=qwen2.5:14b

EMBEDDING_BINDING=ollama
EMBEDDING_HOST=http://localhost:11434
EMBEDDING_MODEL=bge-m3
EMBEDDING_DIMENSION=1024

ASR_PROVIDER=assemblyai
ASSEMBLYAI_API_KEY=...

RERANKER_BINDING=bge
RERANKER_MODEL=BAAI/bge-reranker-v2-m3
```

### 6.2 Cloud OpenAI + Cohere rerank

```bash
LLM_BINDING=openai
LLM_MODEL=gpt-4o-mini
LLM_API_KEY=sk-...

EMBEDDING_BINDING=openai
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_API_KEY=sk-...

RERANKER_BINDING=cohere
RERANKER_API_KEY=...
```

### 6.3 Azure OpenAI

```bash
LLM_BINDING=azure_openai
LLM_BASE_URL=https://your.openai.azure.com
LLM_MODEL=gpt-4o-mini # Deployment name
LLM_API_KEY=...

EMBEDDING_BINDING=azure_openai
EMBEDDING_BASE_URL=https://your.openai.azure.com
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_API_KEY=...
```

## 7. Security and Best Practices

- ✅ Use `SecretStr` for all secrets — do not use `logger.info(settings.LLM_API_KEY)` directly
- ✅ Non-dev environment **must** be configured with `API_KEY`, otherwise lifespan refuses to start
- ✅ Staging and production startup require an explicit `CORS_ORIGINS`
  allowlist; development alone may use the loopback default
- ✅ `.env` added `.gitignore` (there is `.env.example` template in the warehouse)
- ⚠️ `LLM_CACHE_ENABLED=True` will cache the response - please turn it off for strong consistency scenarios
- ⚠️ You must rebuild vectors after modifying `EMBEDDING_*` or `DISTANCE_METRIC`
