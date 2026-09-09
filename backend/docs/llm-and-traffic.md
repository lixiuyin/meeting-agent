# LLM / Embedding Provider and traffic management

> Design of the model calling layer: provider registry, singleton life cycle, cache, prompt template, LLM output parsing, telemetry, concurrency rate limit and circuit breaker.
>
> Code location:
> - `backend/src/services/llm/` — LLM provider registry (`_providers.py`), cache (`_cache.py`), Prompt template (`_prompts.py`), LLM output parsing (`_parsing.py`), Embedding adaptation (`_embeddings_adapter.py`), telemetry (`_telemetry.py`)
> - `backend/src/services/embedder.py` — Embedding singleton
> - `backend/src/services/traffic_control.py` — `TrafficController` + `CircuitBreaker` + `ErrorRateTracker`

## 1. Design goals

- **provider-agnostic**: One set of configurations can switch 12+ model providers
- **Low initialization cost**: heavy object (client, tokenizer, model) global singleton
- **Switchable during runtime**: hot switch through `reset_*()` after `PUT /settings`
- **Stability**: Concurrency upper limit + speed limit + circuit breaker + retry to prevent downstream failures from bringing down the entire service
- **Observability**: Each call will be accounted for by `traffic_controller`, and the failure rate triggers a circuit breaker.

## 2. LLM Provider Registry

Code: `services/llm/_providers.py`.

### 2.1 Registration mechanism

```python
_LLM_CREATORS: dict[str, Callable[..., BaseChatModel]] = {}

def register_llm_provider(name: str) -> Callable:
    """Decorator to register an LLM provider creator function."""
    def decorator(fn):
        _LLM_CREATORS[name] = fn
        return fn
    return decorator

# Actual registration method (function call, non-decorator syntax):
register_llm_provider("openai")(_create_openai_llm)
register_llm_provider("azure_openai")(_create_azure_openai_llm)
register_llm_provider("anthropic")(_create_anthropic_llm)
register_llm_provider("ollama")(_create_ollama_llm)
register_llm_provider("llama_cpp")(_create_llama_cpp_llm)
```

Registered functions support both decorator syntax and direct calling; the code base uses the direct calling style.

For OpenRouter, `LLM_REASONING_EFFORT` defaults to `low` and reasoning blocks
are excluded from the returned payload. Reasoning still counts against the
completion budget, so bounding it prevents reasoning-capable models from using
all `LLM_MAX_TOKENS` before emitting user-visible text.

Model identities in dated validation artifacts describe the effective
verification environment, not compiled repository defaults. In particular,
[`docs/validation/latest-benchmark.json`](../../docs/validation/latest-benchmark.json)
records one OpenRouter-routed run. Because its underlying provider endpoint was
not pinned, its latency and route-error observations are scoped diagnostics,
not universal claims about the named models. Publication and comparison rules
are defined in [`benchmarking.md`](./benchmarking.md#publishing-benchmark-and-model-results).

### 2.2 OpenAI compatible provider table

```python
_OPENAI_COMPATIBLE_PROVIDERS = {
    # name: (default_base_url, requires_api_key)
    "deepseek": ("https://api.deepseek.com/v1", True),
    "openrouter": ("https://openrouter.ai/api/v1", True),
    "groq": ("https://api.groq.com/openai/v1", True),
    "together": ("https://api.together.xyz/v1", True),
    "mistral": ("https://api.mistral.ai/v1", True),
    "lm_studio": ("http://localhost:1234/v1", False),
    "vllm": ("http://localhost:8000/v1", False),
}
```

These providers share the `_create_openai_compatible_llm()` factory, but fill in different base_url + key strategies, and the registration cycle is automatically generated in batches:

```python
for name, (default_url, requires_key) in _OPENAI_COMPATIBLE_PROVIDERS.items():
    _LLM_CREATORS[name] = partial(_create_openai_compatible_llm, name, default_url, requires_key)
```

### 2.3 All supported `LLM_BINDING`

`openai` · `azure_openai` · `anthropic` · `deepseek` · `openrouter` · `groq` · `together` · `mistral` · `ollama` · `lm_studio` · `vllm` · `llama_cpp`

Each has an independent factory, corresponding to LangChain's `ChatOpenAI` / `AzureChatOpenAI` / `ChatAnthropic` / `ChatOllama` / `LlamaCpp`, etc.

### 2.4 Thread-safe configuration-keyed singleton

```python
_llm: BaseChatModel | None = None
_llm_key: tuple[Any, ...] | None = None
_lock = threading.Lock()

def get_llm() -> BaseChatModel:
    global _llm, _llm_key
    config_key = _llm_config_key()
    if _llm is None or _llm_key != config_key:
        with _lock:
            if _llm is None or _llm_key != config_key:
                _llm = create_llm()
                _llm_key = config_key
    return _llm

def reset_llm() -> None:
    global _llm, _llm_key
    with _lock:
        _llm = None
        _llm_key = None
```

`_get_effective_binding()` lowercases `LLM_BINDING` and falls back to `openai`
only when that current field is empty. The cache key includes the binding,
model, generation parameters, endpoint, reasoning effort, and a one-way API-key
fingerprint, so a changed configuration reconstructs the client even before an
explicit reset.

### 2.4.1 Extraction LLM singleton

`_providers.py` maintains an additional `get_extraction_llm()` singleton used by durable fact/entity extraction jobs:

- Use `MEMORY_EXTRACTION_MODEL` to configure the independent model name (fallback to the main LLM if not configured)
- Shares the same binding as the main LLM, but the `model_name` parameter is independent
- `reset_extraction_llm()` is called when settings change

### 2.4.2 Vision capability detection

`_providers.py` exports the `supports_vision()` function, which is used to determine whether the current LLM supports image input:

- Explicitly setting `LLM_SUPPORTS_VISION` (`true` / `false`) takes precedence
- `"auto"` + `MULTIMODAL_CAPTIONING_ENABLED=True` is considered supported
- Automatic detection based on model name prefix (`gpt-4o`, `claude-3`, `gemini`, `qwen-vl`, etc.) and binding (`anthropic` is supported by default)

### 2.5 Response caching

`services/llm/_cache.py` (TTL + LRU):

- Cache only **Pure generation** (no streaming / no tool calls)
- key = `(model, messages_hash, temperature, max_tokens, stop)`
- Controlled by `LLM_CACHE_ENABLED` / `LLM_CACHE_TTL_SECONDS` / `LLM_CACHE_MAX_SIZE`
- Significant gains in local development or repeatable short calls like query-rewrite
- ⚠️ Strong consistency / time-related prompts please close

### 2.6 Other LLM submodules

| Modules | Responsibilities |
|---|---|
| `_embeddings_adapter.py` | Batch Embedding adaptation: encapsulate the embedding interface of LLM into LangChain `Embeddings` |
| `_parsing.py` | LLM output parsing: `StreamingThinkingFilter` (streaming scenario strips thinking blocks), JSON parsing |
| `_prompts.py` | Prompt templates: `get_rag_prompt()`, `get_skill_prompt(skill_def)`, `get_fact_extraction_prompt()`, etc. |
| `_telemetry.py` | LLM call telemetry: records the model, number of tokens, latency and other indicators of each call |## 3. Embedding Provider

Code: `services/embedder.py`.

Structure symmetrical to LLM: `EMBEDDING_BINDING` → `_EMBED_CREATORS[binding]()` → singleton.

Supported: `openai` · `azure_openai` · `ollama` · `lm_studio` · `huggingface` · `jina` · `cohere` · `google` · `openrouter` · `deepseek` · `together` · `groq` · `mistral` · `vllm`.

During startup, `lifespan` will call `get_embeddings().embed_query("ping")` once to do **connectivity verification**. If it fails, the startup will fail.

### 3.1 Dimension consistency

`EMBEDDING_DIMENSION` must match the actual embedding output. The live settings API rejects model, dimension, and chunk-shape changes before publication. Apply those values through deployment configuration, restart under operator control, and wait until `/health/ready` reports no manifest mismatch or pending index repair. Startup reconciliation queues durable per-file reprocessing against the authoritative source artefacts.

## 4. Reranker

Code: `services/rag/_reranker.py`. Two categories:

- **Cohere Rerank API** (`RERANKER_BINDING=cohere`) - Hosting
- **BGE Cross-Encoder** (`RERANKER_BINDING=bge`) - local HuggingFace model, requires optional extra: `uv sync --extra huggingface`

All are exposed through the `get_reranker()` singleton, and `reset_reranker()` is called when settings change.

## 5. Traffic management: `TrafficController`

Code: `services/traffic_control.py`.

### 5.1 Composition

```python
class TrafficController:
    def __init__(self, *, max_concurrency: int, rpm: int, ...):
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._tokens = float(rpm) # token bucket
        self._rpm = rpm
        self._breaker = CircuitBreaker(threshold=..., recovery_seconds=...)
        self._error_tracker = ErrorRateTracker(window_seconds=...)
```

Three components superimposed:

| Component | Function | Consequences of failure |
|---|---|---|
| `asyncio.Semaphore` | Concurrency upper limit (`LLM_MAX_CONCURRENCY`) | Queue if exceeded |
| Token bucket | Number of requests per minute (`LLM_RPM`) | If it exceeds, `await` will reissue the token |
| `CircuitBreaker` | Continuous error ≥ threshold → open | Throw `BreakerOpenError` directly when open |
| `ErrorRateTracker` | Sliding window error rate | Configurable alarm / automatically open breaker |

### 5.2 How to use

```python
async with traffic_controller:
    response = await llm.ainvoke(messages)
```

`__aenter__` process:
1. Check the breaker status (open and not yet in recovery → throw error; if half open, put 1 request detection)
2. `await semaphore.acquire()`
3. `await _acquire_token()` — token bucket reissue/blocking wait

`__aexit__` reports to the breaker / error tracker based on exception conditions.

### 5.3 Token bucket implementation

```python
async def _acquire_token(self):
    async with self._lock:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._rpm, self._tokens + elapsed * (self._rpm / 60))
        self._last_refill = now
        if self._tokens >= 1:
            self._tokens -= 1
            return
    # Calculate the waiting time and then sleep again and try again
    wait = (1 - self._tokens) * (60 / self._rpm)
    await asyncio.sleep(wait)
    return await self._acquire_token()
```

Features:
- **Smooth Throttling**: Not focused on full-minute boundaries
- **Limited fairness**: Strict FIFO requires queuing, and the current implementation is sufficient for most scenarios

### 5.4 Circuit Breaker state machine

```
         ┌──── success ────┐
         ▼ │
closed ─── err ≥ threshold ─► open
                              │
               recovery timer │
                              ▼
                         half-open ── success ──► closed
                              │
                              └── fail ──► open
```

- **closed**: Normal release, statistical error
- **open**: Directly reject (`BreakerOpenError`) to protect downstream
- **half-open**: Put 1 detection request, restore if successful, continue to open if failed

All state changes have `threading.Lock` and can be used in multi-threaded environments.

### 5.5 ErrorRateTracker

```python
classErrorRateTracker:
    """Sliding window of (timestamp, is_error)."""
    def record(self, is_error: bool): ...
    def error_rate(self) -> float: ...
```

Used for reports/metrics, and can also be used as auxiliary decision-making for breaker.

## 6. Retry strategy

`LLM_RETRY_MAX_ATTEMPTS` controls the number of retries for a single call. Retry in `TrafficController` **Outer layer**: Each retry occupies semaphore + token independently to coordinate with speed limiting.

Recommended:
- Short retry interval + exponential backoff
- Only retry for **transient errors** (429/5xx/network), **Do not** retry 4xx client errors
- Cooperate with breaker: give up directly when breaker opens, don't run through all retries

## 7. Hot switching process (Settings API)

`PUT /api/v1/settings` After modifying model related fields:

```python
if binding_changed:
    reset_llm()
    reset_embeddings()
    reset_reranker()
    reset_query_rewriter()

if traffic_changed:
    init_traffic_controller() # Rebuild semaphore / breaker
```

**Note**: LLM-only provider changes can be applied live. Embedding model/dimension changes are rejected by live settings endpoints and require a controlled restart followed by manifest-driven durable reprocessing.

## 8. Tuning Guide

| Scenario | Suggestions |
|---|---|
| Local small model (Ollama) | `LLM_MAX_CONCURRENCY=2`, `LLM_RPM` is raised (no quota locally) |
| OpenAI tier 1 | `LLM_RPM=500`, `LLM_MAX_CONCURRENCY=20` |
| High concurrent batch processing | Turn on cache; enlarge `LLM_CACHE_MAX_SIZE` to 2048+ |
| Severe downstream jitter | Reduce `LLM_CIRCUIT_BREAKER_THRESHOLD` to 3, shorten recovery; runtime controller initialization applies both settings |
| Cold start is slow | Warm up before deployment: send a warmup query immediately after startup |
| Memory retrieval scenario | Independent `QUERY_REWRITE_MODEL` (lightweight model) reduces the pressure on the main LLM |

## 9. Typical errors and handling

| Error | Meaning | Processing |
|---|---|---|
| `BreakerOpenError` | Circuit breaker open | Wait for recovery or check downstream health |
| `429` | Provider rate limited the request | Lower `LLM_RPM` or upgrade the provider tier |
| `asyncio.TimeoutError` in embed | Network / slow model loading | Adjust http timeout / warm-up |
| `dimension mismatch` in Chroma | embedding dimension changed outside the live API | controlled restart; monitor `repair_pending` until durable reprocessing completes |
| `Unknown LLM binding: xxx` | `LLM_BINDING` is written incorrectly | Check spelling / to see if / is in `_LLM_CREATORS` |
| Looks like "stuck" | semaphore is full | l `LLM_MAX_CONCURRENCY` / check for slow calls |

## 10. Extension: Add new provider

1. Write a factory function in `services/llm/_providers.py`
2. Decorate with `@register_llm_provider("myprovider")`
3. If OpenAI is compatible, just add it to the `_OPENAI_COMPATIBLE_PROVIDERS` dictionary.
4. Add sample configuration in `backend/config/main.yaml`
5. Supplement unit test: `tests/config/test_llm_parsing.py` (currently there is already a provider output parsing test; if a new provider is added, the corresponding mock test should be added in this directory)
6. Update the provider list of [`configuration.md`](./configuration.md)
