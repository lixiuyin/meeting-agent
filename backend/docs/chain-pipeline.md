# Chain Pipeline：RAG 问答的编排层

> 从 `ask()` / `ask_stream()` 进入到生成答案的完整步骤，包括并行上下文加载、流式事件总线与 skill 集成。
>
> 代码位置：`backend/src/services/chain/`（26 个模块，含 `_api.py`、`_api_stream.py`、`_context.py`、`_routing.py`、`_formatting.py`、`_steps_*.py`、`_retrieve_*.py`、`_generate_helpers.py`、`_extraction.py`、`_common.py` 等）。

## 1. 分层

```
src/services/chain/
├── __init__.py                    # 导出 ask, ask_stream, PipelineContext, PipelineResult, _background_tasks, _extract_sources 等
├── _api.py                        # ask() 入口 + _run_pipeline 编排
├── _api_stream.py                 # ask_stream() 入口（生产者-消费者 StreamBus）
├── _context.py                    # PipelineContext / PipelineResult dataclass
├── _common.py                     # 共享 logger 与公共辅助函数
├── _routing.py                    # 意图分类（casual vs retrieval） + _casual_response
├── _formatting.py                 # 源引用抽取、答案格式化
├── _fallback.py                   # 流式 → 非流式降级的 circuit breaker
├── _skill_matching.py             # get_skill_loader / get_skill_matcher 单例
├── _anthropic_cache.py            # Anthropic prompt 缓存标记
├── _extraction.py                 # 事实 / 实体提取调度
├── _generate_helpers.py           # LLM 生成辅助函数
├── _meeting_summary_lifecycle.py  # 会议摘要生命周期管理
├── _resolver.py                   # 查询解析 / 消歧
├── _retrieve_broad.py             # 广域检索（多源）
├── _retrieve_filters.py           # 检索过滤器构造
├── _retrieve_post.py              # 检索后处理（去重 / 抑制 / 排序）
├── _retrieve_routing.py           # 检索路由决策
├── _retrieve_utils.py             # 检索辅助工具函数
├── _speaker_context.py            # 发言人上下文注入
├── _steps_session.py              # ensure_session, rewrite_query
├── _steps_retrieve.py             # retrieve + rerank + dedup branch
├── _steps_context.py              # memories / entity / session / web / history
├── _steps_generate.py             # build_context + LLM 生成 + 保存消息 + 调度事实提取
├── _judge_prompts.py              # RAG 质量评测 prompt
└── _per_file_summary.py           # 逐文件摘要
```

## 2. 数据结构

### 2.1 `PipelineContext`

输入 + 中间状态的统一容器。核心字段：

```python
@dataclass
class PipelineContext:
    # 输入
    question: str
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
    embeddings: Embeddings | None = None        # embeddings 实例，pipeline 入口注入
    settings_epoch: int = 0
    settings_snapshot: SettingsSnapshot | None = None

    # 中间结果
    rewritten_query: str = ""
    docs: list[dict] = field(default_factory=list)
    scope_file_ids: list[int] = field(default_factory=list)   # 路由阶段确定的文件范围
    query_analysis: QueryAnalysis | None = None  # 发言人名称 + 时间分析
    memory_context: str = ""
    session_context: str = ""
    entity_context: str = ""
    web_context: str = ""
    web_results: list[dict] = field(default_factory=list)
    history_messages: list[BaseMessage] = field(default_factory=list)
    meeting_context: str = ""      # 组装后的文档 context
    combined_context: str = ""     # 全部拼装好的 system context
    past_session_refs: list[dict] = field(default_factory=list)
    query_embedding: list[float] | None = None  # 查询向量（prewarm 缓存）

    # 输出
    answer: str = ""
    failed_extraction_count: int = 0

    # Skill 匹配结果（与 retrieve 并行，generate 前消费）
    skill_name: str | None = None
    skill_confidence: float | None = None

    # 检索阶段被 token budget 丢弃的 chunk 数
    dropped_chunks: int = 0
```

### 2.2 `PipelineResult`

对外返回的扁平化结构：

```python
@dataclass
class PipelineResult:
    answer: str
    sources: list[dict]
    session_id: str
    web_results: list[dict] | None = None
    past_sessions: list[dict] | None = None
    extraction_failed: bool = False
    trace: dict | None = None     # 序列化后的 span tree
    skill_used: str | None = None
    skill_confidence: float | None = None
    context_truncated: int | None = None  # token budget 截断丢弃的 chunk 数
```

## 3. 同步入口：`ask()`

```python
async def ask(
    question: str,
    session_id: str | None = None,
    user_id: str = "default",
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

内部流程：

1. 获取 `settings_epoch` + `build_settings_snapshot()` 构造 `PipelineContext`
2. `_classify_intent(question)` 做意图分类
3. Casual → 短路径（`ensure_session` + `_casual_response(question)` + `save_messages`）
4. 解析 LLM / embeddings 单例并注入 `ctx.llm` / `ctx.embeddings`（保证管线内各步骤使用同一实例，即使中途设置变更）
5. Skill 匹配以 **`asyncio.create_task()`** 并发启动，与后续 RAG 管线重叠执行
6. `_run_pipeline(ctx, None, skill_task=skill_task)` 开始管线；skill 结果在 `generate_answer` 前消费
7. 匹配成功 → 加载 skill 完整定义；无匹配 → 正常 RAG 路径
8. 装配 `PipelineResult` 返回

### 3.1 Casual 短路

`_routing.py::_classify_intent()` 用正则匹配**短问候 / 致谢 / 简短确认**（见 `_GREETING_PATTERN`、`_SMALLTALK_PATTERN`、`_CJK_GREETING_PATTERN`、`_CJK_SMALLTALK_PATTERN`，同时支持英文与中日韩文），命中则返回 `casual`，避免无意义的 RAG 调用。额外对混合 CJK 文本做长度守卫（>6 字符强制走 RAG）。短路径使用 `_casual_response(question)` 返回预置文案（**不调用 LLM**）。

### 3.2 Skill 匹配

- `_skill_matching.py` 提供 `get_skill_loader()` 和 `get_skill_matcher()` 单例（DCL + thread-safe）
- `SkillLoader.load_summaries()` 返回轻量摘要用于匹配
- `IntentMatchingService.match()` 返回最佳匹配 + 置信度（超时 `SKILL_MATCH_TIMEOUT_S` 跳过）
- 匹配以 **`asyncio.create_task()`** 启动，与 `_run_pipeline` 并行执行；skill 结果在 `generate_answer` 前消费
- 双查询匹配：若 `rewritten_query` 与原查询不同，会对两个查询分别做匹配，取更高置信度结果
- 匹配成功 → `loader.get_full(name)` 加载完整定义 → `skill.model_dump()` 传给 `_run_pipeline`

## 4. 核心编排：`_run_pipeline()`

```python
async def _run_pipeline(
    ctx: PipelineContext,
    skill_definition: dict[str, Any] | None = None,
    *,
    skill_task: asyncio.Task[Any] | None = None,  # 并行 skill 匹配任务
) -> None:
    ctx.trace.start_span("pipeline", "pipeline")
    with otel_span("chain.run_pipeline"):
        # Step 1-2: 会话准备 + 查询改写（并行）
        await asyncio.gather(
            asyncio.to_thread(ensure_session, ctx),
            rewrite_query_step(ctx),
        )

        # Step 2.5: 预热查询向量缓存（让后续并行分支共享同一次 embedding 调用）
        await _prewarm_query_embedding(ctx)

        # Step 3: 并行加载上下文（核心收益点）
        context_timeout = _context_branch_timeout(ctx)
        await asyncio.gather(
            _retrieve_branch(),                                                  # retrieve → pre_rerank_dedup → rerank → suppress
            _best_effort("memories", load_memories(ctx), context_timeout),       # 带超时隔离
            _best_effort("session", load_session_context(ctx), context_timeout), # 带超时隔离
            _best_effort("entity", load_entity_context(ctx), context_timeout),   # 带超时隔离
            _best_effort("web", perform_web_search(ctx), settings.WEB_SEARCH_TIMEOUT_S),
            _best_effort("history", load_history(ctx), context_timeout),         # 带超时隔离
        )

        # Step 3.5: 消费 skill_task 结果（通常此时已完成，0ms 等待）
        if skill_task is not None and skill_definition is None:
            try:
                match = await skill_task
            except Exception:
                match = None  # 失败不阻塞管线
            if match and match.matched:
                skill_definition = loader.get_full(match.skill.name).model_dump()
                ctx.skill_name = match.skill.name
                ctx.skill_confidence = float(match.score)

        # Step 4: 拼装最终 context（阻塞操作走 to_thread）
        await asyncio.to_thread(build_context, ctx)

        # Step 5: LLM 生成
        await generate_answer(ctx, skill_definition)

        # Step 6: 持久化（阻塞操作走 to_thread） + 后台事实提取
        await asyncio.to_thread(save_messages, ctx)
        schedule_fact_extraction(ctx)      # 后台任务

    # Step 7: 刷新内存中的 pending touch 操作
    flush_pending_touches()
    ctx.trace.finish_span("pipeline")
```

### 4.1 为什么并行

记忆、会话上下文、实体、web 搜索、历史这五件事**彼此独立**。顺序执行累计延迟 = 各项之和；并行后延迟 ≈ 最慢一项 + 少量调度开销。一次调用通常能省 400-800ms。此外会话准备（ensure_session）和查询改写（rewrite_query_step）也在 Step 1-2 并行执行。

### 4.2 失败隔离

非关键的 `load_*` 分支通过 `_best_effort(name, coro, timeout)` 包装：

- **超时保护**：`CONTEXT_LOAD_TIMEOUT_S`（web 搜索用独立的 `WEB_SEARCH_TIMEOUT_S`）
- **超时时**：记 `CONTEXT_STEP_TIMEOUT_TOTAL` 指标 + warning 日志，不抛异常
- **异常时**：记 `CONTEXT_STEP_ERROR_TOTAL` 指标 + warning 日志 + exc_info，不抛异常
- `retrieve_branch` 不隔离（核心依赖，失败应向上传播）

主 pipeline 只对致命错误抛异常（无文档就生成不出高质量回答）。

### 4.3 Trace spans

`TraceContext`（`core/trace.py`）使用 **`start_span(label, phase, ...)` + `finish_span(label, status)`** 配对，没有上下文管理器形式的 `span()`。

```python
ctx.trace.start_span("rewrite_query", "retrieve")
try:
    # ... mutate ctx ...
    ctx.trace.finish_span("rewrite_query")
except Exception:
    ctx.trace.finish_span("rewrite_query", "error")
    raise
```

`ask()` / `ask_stream()` 返回体中的 `trace` 字段来自 `ctx.trace.to_dict()`（含 `trace_id`、`total_ms`、`spans[]`）。

## 5. 关键步骤细节

### 5.1 `ensure_session`

- 若 `ctx.session_id` 为空 → 创建新 session
- 若存在但在 DB 里找不到 → 同样创建
- 更新 `last_active`

### 5.2 `rewrite_query_step`

- 优先使用**查询解析器 (resolver)** (`_resolver.py`)：先加载轻量历史窗口（最多 `MAX_RESOLVER_HISTORY_MESSAGES=8` 条），调用 `resolve_query()` 做消歧/指代解析
- 若 resolver 未改变原始查询且 `QUERY_REWRITE_ENABLED=True`，则回退到**遗留 rewrite 路径**（`rag._query.rewrite_query`）
- 若 resolver 被 `RESOLVER_ENABLED=False` 禁用，或查询被判定为简单查询（`_is_simple_query`），也回退到遗留路径
- 遗留 rewrite 使用**独立的轻量模型** `QUERY_REWRITE_MODEL`（可设为比主 LLM 更便宜的型号）
- 可产出 1 个 rewritten query 或 `MULTI_QUERY_COUNT` 个查询（多查询模式，在 `_steps_retrieve.py` 中通过 `_generate_query_variants` 生成）

### 5.3 `retrieve_branch`

详见 [`rag.md`](./rag.md)。要点：

1. **retrieve**：Chroma 语义检索（可 + BM25 hybrid RRF 融合）
2. **pre_rerank_dedup**（`_retrieve_post.py`）：在 rerank 之前用 **n-gram Jaccard 重叠率** 做廉价去重（阈值 `RAG_PRE_RERANK_DEDUP_THRESHOLD`，默认 0.92），避免为近重复文档支付 Cohere/BGE 调用成本
3. **rerank**：Cohere 或 BGE cross-encoder，保留 top_n
4. **suppress_near_duplicates**：对相邻保留文档做 **4-字符 n-gram Jaccard 式重叠率**；若 `overlap >= 0.85`（`_CONTENT_SIMILARITY_THRESHOLD`）则视为近重复并丢弃较低排名者（实现见 `_steps_retrieve.py`）

### 5.4 `load_memories`

- 先 `memory_service.get(user_id, "__profile__")` 注入用户画像（若有）
- 再 `memory_service.search_semantic(..., limit=min(MEMORY_MAX_CONTEXT_ITEMS, 8), min_importance=3, meeting_ids=..., file_ids=...)` 取与当前问题最相关的记忆
- 命中后在后台线程执行 `boost_recalled_entries` + `touch_memory_access`（不阻塞主响应）

### 5.5 `load_session_context`

- 查 `session_summaries` 表
- 向量检索历史会话摘要（跨会话 episodic memory）
- 只注入与当前 query 相关的 top K

### 5.6 `load_entity_context`

- 先对 `ctx.rewritten_query` 做轻量实体识别
- 查 Chroma `entities` collection 找相似实体
- 扩一跳 `memory_relations`
- 拼成 "[entity] is related to ..." 样式的文本

### 5.7 `perform_web_search`

- 仅在 `ctx.use_web_search=True` 且配置了 `SEARCH_BINDING`（及对应 API key，除 DuckDuckGo）时执行
- 实现：`services/search.py`（DuckDuckGo / SerpAPI / Tavily / Bing / Exa）
- 结果条数上限 `SEARCH_MAX_RESULTS`；`_run_pipeline` 里对该协程使用 **`asyncio.wait_for(..., settings.WEB_SEARCH_TIMEOUT_S)`** 作为并行分支超时（与通用 `CONTEXT_LOAD_TIMEOUT_S` 区分）
- **不替代 RAG**，仅作补充

### 5.8 `load_history`

- 取当前会话最近消息
- 超过 `SESSION_MAX_TOKENS`（且消息数 > 6）时触发 session summary 替换老消息（保留最近 4 条原文，其余做摘要压缩）
- 结果经 `sanitize_history_messages` 清洗（去除 AI 消息中的 `[N]` 引用标记、截断超长消息、按 token budget 裁剪）后得到 `list[BaseMessage]`

### 5.9 `build_context`

拼装顺序见 [`memory-and-kg.md`](./memory-and-kg.md#10-记忆注入到-prompt-的顺序)。

- 每段有独立 budget（硬上限）
- **skill 注入**：若匹配了 skill，skill 的 `prompt_sections` 会被插入到 system prompt 特定位置

### 5.10 `generate_answer`

签名：`async def generate_answer(ctx: PipelineContext, skill_definition: dict | None = None)`（`_steps_generate.py`）。

- Prompt：`get_skill_prompt(skill_definition)` 若带 skill；否则 `get_rag_prompt()`
- Chain：`prompt | RunnableLambda(apply_anthropic_cache_control) | llm | StrOutputParser()`，经 `traffic_controller` 上下文管理器做并发/熔断保护后通过 `_invoke_chain_with_retry` / `_invoke_chain_with_retry_multimodal` 调用（内部使用 `chain.invoke`，外层 `asyncio.to_thread` 包装以避免阻塞事件循环）
- 入参字段与模板一致（含 `context` / `question` / `history` / `memory_context` 等）；多模态路径在含图片的文档上注入 `HumanMessage` 图文块（受 `MULTIMODAL_ATTACH_GATE_ENABLED` 门控，仅对视觉类查询附加图片以节省 token）
- 流式路径不在此函数内；见 `ask_stream()` 对 `chain.astream(...)` 的分支

### 5.11 `save_messages`

同一事务里写入 user message + agent message，同步更新 `chat_sessions.updated_at`。

### 5.12 `schedule_fact_extraction`

```python
def schedule_fact_extraction(ctx: PipelineContext) -> None:
    # Per-session dedup: skip if this session already has an extraction in flight
    if sess and sess in _active_extraction_sessions:
        return

    async def _safe_extract():
        # Per-session circuit breaker + retry loop
        ...
    task = asyncio.create_task(_safe_extract())
    _register_background_task(task)   # uses chain/__init__.py::_register_background_task
    task.add_done_callback(_on_done)
```

任务集合为 **`services/chain/__init__.py` 模块级 `_background_tasks`**（`lifespan` 关闭时 `cancel_background_tasks()` 统一取消），**不是** `app.state.background_tasks`。内部使用 `_register_background_task()` 注册，该函数在集合满时按超时策略逐出最久未完成的任务（最多 `_MAX_BG_TASKS=64` 个）。提取逻辑优先使用**合并提取**（`_extraction.py::run_combined_extraction`，单次 LLM 调用同时提取事实+实体+关系，由 `COMBINED_EXTRACTION_ENABLED` 控制），回退到传统的 `memory_service.auto_extract_facts` → `kg_service.extract_entities` 两步调用。带重试（`_FACT_EXTRACT_MAX_RETRIES`）与连续失败熔断（`_EXTRACTION_CIRCUIT_BREAKER_THRESHOLD`，per-session）。另有 per-session 去重（`_active_extraction_sessions`）避免同一会话并发堆积。

### 5.13 `flush_pending_touches`

在 `schedule_fact_extraction` 之后调用（`_api.py` 和 `_api_stream.py` 中均在管线末尾执行）。将内存中累积的 `touch_memory_access` 延迟写入批量刷到数据库，避免在热路径中逐条写 DB。

### 5.14 `_common.py`

管线模块共享的 `logger` 实例和公共辅助函数，供 `_api.py`、`_api_stream.py` 及各 `_steps_*.py` 统一引用。

## 6. 流式入口：`ask_stream()`

`ask_stream(...)`（`_api_stream.py`）返回 **异步生成器**，元素为 **`dict`**（`StreamBus.__anext__` 返回 `StreamEvent.to_dict()`）。

并发模型：

1. 创建 `StreamBus()`（有界队列，默认 `maxsize=2000`，`put_nowait` 满时丢弃非终端事件并打日志；终端事件 `done`/`error` 存入独立 slot，永不丢失）。
2. `asyncio.create_task(_pipeline())` 在后台跑完整管线；主协程 **`async for event in bus:`** 实时 `yield` 事件给 `chat_stream`。
3. `finally`：若 task 未完成则 `cancel`，`await task`，再 `bus.close()`。

`_pipeline()` 内部与同步路径类似（`classify_intent` → `_is_trivially_short` 短路检查 → 可选 skill → `ensure_session` / `rewrite_query` / 并行检索与上下文 / `build_context`），但 **不调用** `_run_pipeline()`；流式 LLM 使用 `chain.astream(...)`（或 `llm.astream(messages)` 多模态路径），token 经 `_emit_stream()` 消费并通过 `bus.emit_token` 发出，并在结束时 `bus.emit_trace` + `bus.emit_done`。`_emit_stream` 内部有 **首 token 前心跳**（`_PRE_TOKEN_HEARTBEAT_TIMEOUT_S=8.0s` 间隔）和全局心跳（5s 间隔）双重保活。LLM 异步生成器在 `finally` 中通过 `aclose()`（超时 `_ACLOSE_TIMEOUT_S=3.0s`）关闭，失败时回退到非流式调用（经 `is_fallback_circuit_open()` 熔断保护）。

对 LangChain `astream` 生成器在 `finally` 中 **`await aclose()`**（超时 3s），避免客户端断开时出现 `GeneratorExit` 警告。超时后强制 `athrow(GeneratorExit)` 确保关闭。

### 6.1 事件类型（`StreamBus` / `StreamEvent`）

| `type` | 载荷 | 含义 |
|---|---|---|
| `step` | `step`, `status`（`start`/`done`）及可选元数据 | 管线阶段（如 `accepted`、`session`、`pipeline`、`generate`） |
| `token` | **`content`**：字符串片段 | LLM 流式增量（前端 `useChatStream` 读 `event.content`） |
| `sources` | `items`：源列表 | 检索引用 |
| `trace` | `trace`：dict | 完整 `TraceContext.to_dict()` |
| `web_results` | `items` | Web 结果 |
| `heartbeat` | 无额外字段 | 保活 |
| `error` | `message`、可选 `code` / `detail` / `exception_type` | 用户可读错误 |
| `done` | `session_id` | 正常结束；随后 `bus.close()` |

所有事件自动附加 **`seq`**（单调递增序号，用于因果排序）和可选 **`parent_step`**（父步骤名，用于因果链追踪）。

### 6.2 Router 层

`api/routers/chat.py::chat_stream` 使用 `serialize_event(event)`（`stream_bus.py`）输出 **SSE `data:` 行**，并在 `finally` 中对生成器 **`await stream.aclose()`**（超时 5s）：

```python
async for event in ask_stream(...):
    yield serialize_event(event)
```

前端可用 `fetch` + `ReadableStream` 或自定义解析器消费（不必是浏览器 `EventSource`）。

## 7. 错误处理策略

| 场景 | 行为 |
|---|---|
| `classify_intent` 失败 | 视为 retrieval，继续走完整流程 |
| `_is_trivially_short` 命中 | 与 casual 短路相同：跳过 RAG，返回预置文案（流式路径中额外处理） |
| `rewrite` / `resolver` 失败 | 跳过，使用原始 query |
| `retrieve` 返回 0 文档 | 继续生成，LLM 回答"没有找到相关资料" |
| `rerank` 失败 | fallback 到未重排结果 |
| `load_memories` 失败 | 空上下文 + 日志（被 `_best_effort` 隔离） |
| `load_session_context` 失败 | 空上下文 + 日志（被 `_best_effort` 隔离） |
| `load_entity_context` 失败 | 空上下文 + 日志（被 `_best_effort` 隔离） |
| `perform_web_search` 失败 | 空上下文 + 日志（被 `_best_effort` 隔离） |
| `load_history` 失败 | 空上下文 + 日志（被 `_best_effort` 隔离） |
| `generate_answer` 抛错 | 往上抛 → router 层返回 500（流式路径发 error event + done event） |
| `save_messages` 失败（session 已删除） | 日志 + 跳过（`sqlite3.IntegrityError` FK violation 防御） |
| `schedule_fact_extraction` 失败 | `failed_extraction_count++` + `_increment_failures`，不抛（per-session 熔断） |
| 流式 LLM 调用失败 | 自动降级为非流式调用（经 `is_fallback_circuit_open()` 熔断保护，最多重试 2 次） |
| `skill_task` 失败 | 日志 + 继续走正常 RAG 路径 |

## 8. 可扩展点

1. **新 context 源**：在 `_steps_context.py` 添加 `load_xxx(ctx)` → 注册到 `asyncio.gather`
2. **新 skill**：内置文件在 `backend/skills/builtin/`；动态创建走 `POST /api/v1/skills`（写入 `skills/builtin/` 下 Markdown）
3. **新 intent 路由**：扩展 `_routing.py::_classify_intent()`（当前仅 `casual` / `rag` 两档）
4. **更丰富的 trace**：`core/trace.py` 当前是内存 tree，可接 OpenTelemetry 导出
5. **A/B 测试**：在 `build_context` 里 feature flag 切换不同 prompt 模板

## 9. 常见陷阱

- ⚠️ `StreamBus.emit*` 是 **同步** `put_nowait` —— **不要** `await bus.emit_*`；顺序由调用顺序保证。
- ⚠️ 在 `_run_pipeline` 里用 `asyncio.create_task` 而忘跟踪 → 任务可能被 GC；事实抽取应走 `schedule_fact_extraction` 使用的 **`chain._register_background_task`**（非直接 `_background_tasks.add`）。
- ⚠️ 修改 `PipelineContext` 字段后忘了同步 **返回体**（`PipelineResult`）与 **前端** `StreamEvent` 类型。
- ⚠️ 流式路径忘记对 `llm.astream()` 的 async generator **`aclose()`** → 断开连接时 LangChain 可能报 `GeneratorExit`。
- ⚠️ skill prompt 注入过多 → 挤占检索 chunk 的 token budget。
- ⚠️ 把依赖前置状态的步骤误放入与 `retrieve` 并行的 `gather`。
