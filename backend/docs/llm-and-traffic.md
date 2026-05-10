# LLM / Embedding Provider 与流量治理

> 模型调用层的设计：provider 注册表、单例生命周期、缓存、Prompt 模板、LLM 输出解析、遥测、并发限速与熔断。
>
> 代码位置：
> - `backend/src/services/llm/` — LLM provider 注册表（`_providers.py`）、缓存（`_cache.py`）、Prompt 模板（`_prompts.py`）、LLM 输出解析（`_parsing.py`）、Embedding 适配（`_embeddings_adapter.py`）、遥测（`_telemetry.py`）
> - `backend/src/services/embedder.py` — Embedding 单例
> - `backend/src/services/traffic_control.py` — `TrafficController` + `CircuitBreaker` + `ErrorRateTracker`

## 1. 设计目标

- **provider-agnostic**：一套配置即可切换 12+ 模型供应商
- **低初始化代价**：重对象（客户端、tokenizer、模型）全局单例
- **运行时可切换**：`PUT /settings` 后通过 `reset_*()` 热切换
- **稳定性**：并发上限 + 限速 + 熔断 + 重试，防止下游故障拖垮整个服务
- **可观测性**：每次调用都会被 `traffic_controller` 记账，失败率触发熔断

## 2. LLM Provider 注册表

代码：`services/llm/_providers.py`。

### 2.1 注册机制

```python
_LLM_CREATORS: dict[str, Callable[..., BaseChatModel]] = {}

def register_llm_provider(name: str) -> Callable:
    """Decorator to register an LLM provider creator function."""
    def decorator(fn):
        _LLM_CREATORS[name] = fn
        return fn
    return decorator

# 实际注册方式（函数调用，非装饰器语法）：
register_llm_provider("openai")(_create_openai_llm)
register_llm_provider("azure_openai")(_create_azure_openai_llm)
register_llm_provider("anthropic")(_create_anthropic_llm)
register_llm_provider("ollama")(_create_ollama_llm)
register_llm_provider("llama_cpp")(_create_llama_cpp_llm)
```

注册函数同时支持装饰器语法和直接调用；代码库使用直接调用风格。

### 2.2 OpenAI 兼容 provider 表

```python
_OPENAI_COMPATIBLE_PROVIDERS = {
    # name:        (default_base_url,                 requires_api_key)
    "deepseek":    ("https://api.deepseek.com/v1",    True),
    "openrouter":  ("https://openrouter.ai/api/v1",   True),
    "groq":        ("https://api.groq.com/openai/v1", True),
    "together":    ("https://api.together.xyz/v1",    True),
    "mistral":     ("https://api.mistral.ai/v1",      True),
    "lm_studio":   ("http://localhost:1234/v1",       False),
    "vllm":        ("http://localhost:8000/v1",       False),
}
```

这些 provider 共用 `_create_openai_compatible_llm()` 工厂，只是填不同的 base_url + key 策略，注册循环自动批量生成：

```python
for name, (default_url, requires_key) in _OPENAI_COMPATIBLE_PROVIDERS.items():
    _LLM_CREATORS[name] = partial(_create_openai_compatible_llm, name, default_url, requires_key)
```

### 2.3 全部支持的 `LLM_BINDING`

`openai` · `azure_openai` · `anthropic` · `deepseek` · `openrouter` · `groq` · `together` · `mistral` · `ollama` · `lm_studio` · `vllm` · `llama_cpp`

每种都有独立的工厂，对应 LangChain 的 `ChatOpenAI` / `AzureChatOpenAI` / `ChatAnthropic` / `ChatOllama` / `LlamaCpp` 等。

### 2.4 单例双检锁

```python
_llm: BaseLanguageModel | None = None
_llm_lock = threading.Lock()

def get_llm() -> BaseLanguageModel:
    global _llm
    if _llm is None:
        with _llm_lock:
            if _llm is None:
                binding = _get_effective_binding()
                creator = _LLM_CREATORS[binding]
                _llm = creator()
    return _llm

def reset_llm() -> None:
    global _llm
    with _llm_lock:
        _llm = None
```

`_get_effective_binding()` 兼容旧字段：若 `LLM_BINDING` 为空则回退 `LLM_PROVIDER`，再回退 `"openai"`。

### 2.4.1 Extraction LLM 单例

`_providers.py` 额外维护一个 `get_extraction_llm()` 单例，用于后台事实 / 实体提取：

- 使用 `MEMORY_EXTRACTION_MODEL` 配置独立模型名（未配置时回退到主 LLM）
- 与主 LLM 共享同一 binding，但 `model_name` 参数独立
- `reset_extraction_llm()` 在 settings 变更时调用

### 2.4.2 Vision 能力检测

`_providers.py` 导出 `supports_vision()` 函数，用于判断当前 LLM 是否支持图片输入：

- 显式设置 `LLM_SUPPORTS_VISION`（`true` / `false`）优先
- `"auto"` + `MULTIMODAL_CAPTIONING_ENABLED=True` 视为支持
- 自动检测基于模型名前缀（`gpt-4o`、`claude-3`、`gemini`、`qwen-vl` 等）和 binding（`anthropic` 默认支持）

### 2.5 响应缓存

`services/llm/_cache.py`（TTL + LRU）：

- 只缓存 **纯生成**（无流式 / 无工具调用）
- key = `(model, messages_hash, temperature, max_tokens, stop)`
- 通过 `LLM_CACHE_ENABLED` / `LLM_CACHE_TTL_SECONDS` / `LLM_CACHE_MAX_SIZE` 控制
- 在本地开发或 query-rewrite 这种可重复的短调用上收益明显
- ⚠️ 强一致性 / 时间相关的 prompt 请关闭

### 2.6 其他 LLM 子模块

| 模块 | 职责 |
|---|---|
| `_embeddings_adapter.py` | 批量 Embedding 适配：将 LLM 的 embedding 接口封装为 LangChain `Embeddings` |
| `_parsing.py` | LLM 输出解析：`StreamingThinkingFilter`（流式场景剥离 thinking 块）、JSON 解析 |
| `_prompts.py` | Prompt 模板：`get_rag_prompt()`、`get_skill_prompt(skill_def)`、`get_fact_extraction_prompt()` 等 |
| `_telemetry.py` | LLM 调用遥测：记录每次调用的模型、token 数、延迟等指标 |

## 3. Embedding Provider

代码：`services/embedder.py`。

结构与 LLM 对称：`EMBEDDING_BINDING` → `_EMBED_CREATORS[binding]()` → 单例。

支持：`openai` · `azure_openai` · `ollama` · `lm_studio` · `huggingface` · `jina` · `cohere` · `google` · `openrouter` · `deepseek` · `together` · `groq` · `mistral` · `vllm`。

启动时 `lifespan` 会调用一次 `get_embeddings().embed_query("ping")` 做**连通性校验**，失败即启动失败。

### 3.1 维度一致性

`EMBEDDING_DIMENSION` 必须匹配 Chroma collection 的实际维度。切换 model 后务必：

```bash
curl -X POST http://localhost:8000/api/v1/settings/rebuild-vectors \
  -H "X-API-Key: $API_KEY"
```

## 4. Reranker

代码：`services/rag/_reranker.py`。两类：

- **Cohere Rerank API**（`RERANKER_BINDING=cohere`）— 托管
- **BGE Cross-Encoder**（`RERANKER_BINDING=bge`）— 本地 HuggingFace 模型,需要可选 extra：`uv sync --extra huggingface`

都通过 `get_reranker()` 单例暴露，`reset_reranker()` 在 settings 变化时调用。

## 5. 流量治理：`TrafficController`

代码：`services/traffic_control.py`。

### 5.1 组成

```python
class TrafficController:
    def __init__(self, *, max_concurrency: int, rpm: int, ...):
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._tokens = float(rpm)       # token bucket
        self._rpm = rpm
        self._breaker = CircuitBreaker(threshold=..., recovery_seconds=...)
        self._error_tracker = ErrorRateTracker(window_seconds=...)
```

三个组件叠加：

| 组件 | 作用 | 失败后果 |
|---|---|---|
| `asyncio.Semaphore` | 并发上限（`LLM_MAX_CONCURRENCY`） | 超过即排队 |
| Token bucket | 每分钟请求数（`LLM_RPM`） | 超过即 `await` 补发 token |
| `CircuitBreaker` | 连续错误 ≥ 阈值 → open | open 时直接抛 `BreakerOpenError` |
| `ErrorRateTracker` | 滑动窗口错误率 | 可配置告警 / 自动打开 breaker |

### 5.2 使用方式

```python
async with traffic_controller:
    response = await llm.ainvoke(messages)
```

`__aenter__` 流程：
1. 检查 breaker 状态（open 且未到 recovery → 抛错；半开则放 1 个请求探测）
2. `await semaphore.acquire()`
3. `await _acquire_token()` — token bucket 补发 / 阻塞等待

`__aexit__` 根据 exception 情况上报 breaker / error tracker。

### 5.3 Token bucket 实现

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
    # 计算等待时长后 sleep 再重试
    wait = (1 - self._tokens) * (60 / self._rpm)
    await asyncio.sleep(wait)
    return await self._acquire_token()
```

特点：
- **平滑节流**：不是在整分钟边界集中放行
- **公平性有限**：严格 FIFO 需要队列化，当前实现对大多数场景够用

### 5.4 Circuit Breaker 状态机

```
         ┌──── success ────┐
         ▼                 │
closed ─── err ≥ threshold ─► open
                              │
               recovery timer │
                              ▼
                         half-open ── success ──► closed
                              │
                              └── fail ──► open
```

- **closed**：正常放行，统计错误
- **open**：直接拒绝（`BreakerOpenError`），保护下游
- **half-open**：放 1 个探测请求，成功则恢复，失败继续 open

全部状态变化带 `threading.Lock`，可在多线程环境下使用。

### 5.5 ErrorRateTracker

```python
class ErrorRateTracker:
    """Sliding window of (timestamp, is_error)."""
    def record(self, is_error: bool): ...
    def error_rate(self) -> float: ...
```

用于报表 / metrics，也可作为 breaker 的辅助决策。

## 6. 重试策略

`LLM_RETRY_MAX_ATTEMPTS` 控制单次调用的重试次数。重试在 `TrafficController` **外层**：每次重试都独立占用 semaphore + token，以便与限速协同。

推荐：
- 短重试间隔 + 指数退避
- 只对**瞬态错误**（429 / 5xx / network）重试，**不要**重试 4xx client error
- 与 breaker 配合：breaker open 时直接放弃，不要跑完所有重试

## 7. 热切换流程（Settings API）

`PUT /api/v1/settings` 修改模型相关字段后：

```python
if binding_changed:
    reset_llm()
    reset_embeddings()
    reset_reranker()
    reset_query_rewriter()

if traffic_changed:
    init_traffic_controller()  # 重建 semaphore / breaker
```

**注意**：切换不会重启进程，也不会迁移已有向量。变更 embedding 维度必须紧跟 `rebuild-vectors`。

## 8. 调优指南

| 场景 | 建议 |
|---|---|
| 本地小模型（Ollama） | `LLM_MAX_CONCURRENCY=2`，`LLM_RPM` 拉高（本地无 quota） |
| OpenAI tier 1 | `LLM_RPM=500`，`LLM_MAX_CONCURRENCY=20` |
| 高并发批处理 | 开 cache；把 `LLM_CACHE_MAX_SIZE` 放大到 2048+ |
| 下游抖动严重 | 降 `LLM_CIRCUIT_BREAKER_THRESHOLD` 到 3，缩短 recovery |
| 冷启动慢 | 在部署前预热：启动后立即发一条 warmup query |
| 记忆提取场景 | 独立 `QUERY_REWRITE_MODEL`（轻量模型）减轻主 LLM 压力 |

## 9. 典型错误与处理

| 错误 | 含义 | 处理 |
|---|---|---|
| `BreakerOpenError` | 熔断器打开 | 等待 recovery 或检查下游健康 |
| 429 | 被 provider 限速 | 降 `LLM_RPM` 或升级 tier |
| `asyncio.TimeoutError` 在 embed | 网络 / 模型加载慢 | 调 http timeout / 预热 |
| `dimension mismatch` in Chroma | embedding 维度变化 | rebuild-vectors |
| `Unknown LLM binding: xxx` | `LLM_BINDING` 写错 | 检查拼写 / 是否在 `_LLM_CREATORS` |
| 看似"卡住" | semaphore 被占满 | 升 `LLM_MAX_CONCURRENCY` / 查慢调用 |

## 10. 扩展：添加新 provider

1. 在 `services/llm/_providers.py` 写一个工厂函数
2. 用 `@register_llm_provider("myprovider")` 装饰
3. 若是 OpenAI 兼容直接加进 `_OPENAI_COMPATIBLE_PROVIDERS` 字典即可
4. 在 `backend/config/main.yaml` 添加示例配置
5. 补单元测试：`tests/test_llm_providers.py`（mock 网络，断言 client kwargs）
6. 更新 [`configuration.md`](./configuration.md) 的 provider 列表
