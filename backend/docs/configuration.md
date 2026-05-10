# 配置系统

> Meeting Agent 全部运行时配置项的权威参考。
>
> 代码位置：`backend/src/core/config.py`、默认值：`backend/config/main.yaml`。

## 1. 三级覆盖顺序

配置解析优先级（**高覆盖低**）：

```
环境变量         > .env 文件         > config/main.yaml（默认值）
(os.environ)       (dotenv)             (YAML)
```

实现使用 pydantic-settings 的 `BaseSettings` + `SettingsConfigDict(env_file=".env")`。YAML 通过 `_load_yaml_config()` 读入为 `_yaml` 字典，在字段默认值中用 `_yaml.get("section", {}).get("key", default)` 注入。

```python
# 简化示例
LLM_MODEL: str = _yaml.get("llm", {}).get("model", "gpt-4o-mini")
```

这保证了：**任意字段在 YAML 中留空时可用代码级默认**，而运行时又可以用 `.env` 或环境变量覆盖。

## 2. SecretStr

敏感值（API key）统一声明为 `SecretStr`：

```python
LLM_API_KEY: SecretStr = SecretStr("")
```

使用时必须显式 `get_secret_value()`，避免被误打日志。`repr()` 输出为 `SecretStr('**********')`。

## 3. 核心配置分组

### 3.1 路径（`constants.py` 派生）

| 字段 | 默认 | 说明 |
|---|---|---|
| `BASE_DIR` | `PROJECT_ROOT` | 后端根目录 |
| `UPLOAD_DIR` | `data/uploads/` | 上传文件落盘 |
| `VECTOR_DB_DIR` | `data/vectordb/` | Chroma 持久化 |
| `DB_PATH` | `data/meetings.db` | SQLite 路径 |

`model_post_init()` 会确保 `UPLOAD_DIR` 与 `VECTOR_DB_DIR` 存在。

### 3.2 LLM

| 字段 | 默认 | 说明 |
|---|---|---|
| `LLM_BINDING` | `openai` | Provider 名称，见 [`llm-and-traffic.md`](./llm-and-traffic.md) |
| `LLM_MODEL` | `gpt-4o-mini` | 模型名 |
| `LLM_API_KEY` | `""` | **SecretStr**，provider 依赖 |
| `LLM_BASE_URL` | `""` | 自定义 base URL（OpenAI 兼容 provider） |
| `LLM_HOST` | `""` | 本地 provider 地址（ollama/lm_studio/vllm） |
| `LLM_TEMPERATURE` | `0.3` | 生成温度 |
| `LLM_MAX_TOKENS` | `2048` | 单次响应上限 |
| `LLM_CONTEXT_WINDOW` | `128000` | 上下文窗口（用于 prompt 截断策略） |
| `LLM_PROVIDER` | `""` | **Deprecated**，旧名，回退用 |
| `LLM_CACHE_ENABLED` | `True` | 响应缓存 |
| `LLM_CACHE_TTL_SECONDS` | `300` | 缓存 TTL |
| `LLM_CACHE_MAX_SIZE` | `512` | 缓存条目数 |
| `LLM_MAX_CONCURRENCY` | `10` | 并发信号量上限 |
| `LLM_RPM` | `60` | Token bucket 限速 |
| `LLM_CIRCUIT_BREAKER_THRESHOLD` | `5` | 连续错误触发 open |
| `LLM_CIRCUIT_BREAKER_RECOVERY` | `60` | open → half-open 秒数 |
| `LLM_RETRY_MAX_ATTEMPTS` | `3` | 单次调用重试次数 |
| `LLM_PROMPT_RESERVE_TOKENS` | `500` | prompt 预留 token |
| `LLM_HISTORY_BUDGET_CHARS` | `16000` | 对话历史字符预算 |
| `PROMPT_TOTAL_BUDGET_TOKENS` | `6000` | prompt 总 token 预算 |
| `ANTHROPIC_PROMPT_CACHE_ENABLED` | `True` | Anthropic prompt 缓存 |
| `ANTHROPIC_PROMPT_CACHE_MIN_CHARS` | `1024` | 缓存最小字符数 |
| `LLM_SUPPORTS_VISION` | `auto` | LLM 是否支持视觉（`auto`/`true`/`false`） |

### 3.3 Embedding

| 字段 | 默认 | 说明 |
|---|---|---|
| `EMBEDDING_BINDING` | `openai` | 见 `services/embedder.py` |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | |
| `EMBEDDING_API_KEY` | `""` | SecretStr |
| `EMBEDDING_BASE_URL` | `""` | |
| `EMBEDDING_HOST` | `""` | 本地 provider |
| `EMBEDDING_DIMENSION` | `1536` | 必须与 Chroma collection 维度一致 |
| `EMBEDDING_QUERY_CACHE_ENABLED` | `True` | 查询向量缓存 |
| `EMBEDDING_QUERY_CACHE_SIZE` | `64` | 缓存条目数 |

> ⚠️ 切换 Embedding 模型或维度后，**必须** `POST /api/v1/settings/rebuild-vectors` 重建向量库。

### 3.4 ASR / OCR / TTS

| 字段 | 默认 | 说明 |
|---|---|---|
| `ASR_PROVIDER` | `assemblyai` | 仅支持 `assemblyai`（whisper/vibevoice 已移除） |
| `ASR_LANGUAGE` | `en` | |
| `ASSEMBLYAI_API_KEY` | — | AssemblyAI API key（env-only,不放 YAML） |
| `ASSEMBLYAI_SPEECH_MODEL` | `universal-3-pro` | 语音识别模型 |
| `ASSEMBLYAI_SPEAKER_LABELS` | `True` | 启用说话人分离 |
| `ASSEMBLYAI_LANGUAGE_DETECTION` | `True` | 自动语言检测 |
| `ASSEMBLYAI_POLL_INTERVAL_SECONDS` | `3` | 轮询间隔 |
| `ASSEMBLYAI_MAX_WAIT_SECONDS` | `1800` | 最大等待时间 |
| `OCR_PROVIDER` | `marker` | **路由软提示**（`select_parsers` 的 `user_hint`）：`marker` / `mineru` / `paddle`；若出现在当次候选序列则提升到队首，**不**替代 `DocumentProfile` 主路由，详见 [`ingest-pipeline.md`](./ingest-pipeline.md#43-ocr_provider配置提示) |
| `OCR_LANGUAGE` | `en` | |
| `OCR_DPI` | `300` | |

### 3.5 Parser

| 字段 | 默认 | 说明 |
|---|---|---|
| `MARKER_BASE_URL` | `https://www.datalab.to/api/v1/marker` | Marker 云 API 端点 |
| `MARKER_API_KEY` | `""` | SecretStr |
| `MARKER_MAX_WAIT_SECONDS` | `300` | Marker 任务最大等待 |
| `MINERU_BASE_URL` | `https://mineru.net/api/v4` | MinerU 云 API 端点（v4 batch flow root） |
| `MINERU_API_KEY` | `""` | SecretStr |
| `MINERU_MAX_WAIT_SECONDS` | `600` | MinerU 任务最大等待 |
| `PADDLEOCR_BASE_URL` | `""` | PaddleOCR 云 API 端点 |
| `PADDLEOCR_API_KEY` | `""` | SecretStr |
| `PARSER_HTTP_TIMEOUT_SECONDS` | `180.0` | 解析 HTTP 请求超时 |
| `PARSER_POLL_INTERVAL_SECONDS` | `2.0` | 任务轮询间隔 |

> 超时相关字段（`PARSE_TIMEOUT_SECONDS`、`PARSE_TIMEOUT_PER_MB_SECONDS`、`PARSE_TIMEOUT_MAX_SECONDS`）见 [3.12 Parser / Upload](#312-parser--upload)。

### 3.6 Vision

| 字段 | 默认 | 说明 |
|---|---|---|
| `VISION_MODEL` | `""` | OpenAI 兼容多模态模型名 |
| `VISION_API_KEY` | `""` | SecretStr |
| `VISION_BASE_URL` | `""` | 自定义端点 |
| `VISION_RETRY_MAX_ATTEMPTS` | `3` | 重试次数 |
| `VISION_RETRY_BASE_DELAY_SECONDS` | `0.5` | 重试初始延迟 |
| `VISION_RETRY_MAX_DELAY_SECONDS` | `2.0` | 重试最大延迟 |
| `VISION_CAPTION_MIN_CHARS` | `12` | 生成描述的最小字符数 |
| `VISION_OCR_MIN_CHARS` | `6` | OCR 输出最小字符数 |

### 3.7 TTS

| 字段 | 默认 | 说明 |
|---|---|---|
| `TTS_BINDING` | `""` | 可选 |
| `TTS_MODEL` | `""` | |
| `TTS_API_KEY` | `""` | SecretStr |
| `TTS_BASE_URL` | `""` | 自定义端点 |
| `TTS_VOICE` | `""` | |
| `TTS_SPEED` | `1.0` | |

### 3.8 Web Search

| 字段 | 默认 | 说明 |
|---|---|---|
| `SEARCH_BINDING` | `""` | 空=禁用；`duckduckgo`/`tavily`/`bing`/`serpapi`/`exa` |
| `SEARCH_API_KEY` | `""` | DuckDuckGo 不需要 |
| `SEARCH_REGION` | `wt-wt` | |
| `SEARCH_MAX_RESULTS` | `5` | |
| `SEARCH_TIMEOUT` | `10` | 秒 |

### 3.9 RAG

详细解释见 [`rag.md`](./rag.md)。字段清单：

| 字段 | 默认 | 说明 |
|---|---|---|
| `DISTANCE_METRIC` | `l2` | `l2` 或 `cosine` |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | `1024` / `128` | |
| `TOP_K` | `8` | |
| `QUERY_REWRITE_ENABLED` | `True` | |
| `QUERY_REWRITE_MODEL` | `""`（=`LLM_MODEL`） | |
| `QUERY_REWRITE_TIMEOUT_SECONDS` | `10` | 查询改写超时（秒） |
| `SCORE_THRESHOLD` | `1.5` | |
| `RERANKER_BINDING` / `RERANKER_MODEL` | `""` / `cohere/rerank-4-pro` | |
| `RERANKER_API_KEY` / `RERANKER_BASE_URL` | `""` / `""` | |
| `RERANKER_TOP_N` / `RERANKER_MIN_SCORE` | `16` / `0.15` | |
| `RERANKER_TIMEOUT_SECONDS` | `30.0` | 重排超时 |
| `RERANKER_BATCH_SIZE` | `200` | 重排批大小 |
| `RERANKER_UNSCOPED_MIN_SCORE` | `0.05` | 未限定 scope 时的重排最低分数 |
| `RERANKER_SCOPED_MIN_SCORE` | `0.10` | 限定 scope 时的重排最低分数 |
| `PARENT_CHILD_ENABLED` | `False` | |
| `CHILD_CHUNK_SIZE` / `CHILD_CHUNK_OVERLAP` | `256` / `32` | |
| `HYBRID_SEARCH_ENABLED` / `HYBRID_ALPHA` | `True` / `0.5` | |
| `HYBRID_MULTIMODAL_ALPHA` | `0.5` | 多模态混合搜索权重 |
| `RAG_RERANK_FETCH_MULTIPLIER` | `6` | |
| `RAG_PERSIST_INTERVAL_SECONDS` | `30.0` | |
| `SEMANTIC_CHUNKING_ENABLED` | `False` | |
| `NON_TEXT_CHUNKING_STRATEGY` | `native` | 非文本分块策略：`native`/`text` |
| `MULTI_QUERY_ENABLED` / `MULTI_QUERY_COUNT` | `False` / `3` | |
| `RAG_RETRIEVER_PROVIDER` | `native` | 检索提供者：`native`/`hybrid`/`multimodal`/`hybrid_multimodal` |
| `RAGANYTHING_ENABLED` | `False` | RAGAnything 功能开关 |
| `RAGANYTHING_FALLBACK_TO_NATIVE` | `True` | RAGAnything 失败时回退到 native |
| `RAGANYTHING_WORKING_DIR` | `""` | 默认 `VECTOR_DB_DIR/raganything` |
| `RAGANYTHING_INDEX_TIMEOUT_SECONDS` | `120.0` | |
| `RAGANYTHING_QUERY_TIMEOUT_SECONDS` | `30.0` | |
| `RAGANYTHING_LLM_TIMEOUT_SECONDS` | `90.0` | |
| `RAG_FILE_SCOPING_MODE` | `router_and_funnel` | `router_and_funnel`/`funnel_only`/`router_pre_filter`/`router_only` |
| `COMBINED_EXTRACTION_ENABLED` | `True` | 合并事实+实体提取（单次 LLM 调用） |
| `VECTOR_SEARCH_TIMEOUT_S` | `8.0` | 向量搜索超时（秒） |
| `MULTIMODAL_ATTACH_GATE_ENABLED` | `True` | 多模态图片附件门控（按视觉查询检测） |
| `RAG_SIBLING_CORETRIEVE_ENABLED` | `True` | 兄弟 chunk 共检索 |
| `RAG_SIBLING_CORETRIEVE_PER_ANCHOR` | `1` | 每 anchor 兄弟数 |
| `RAG_SIBLING_CORETRIEVE_MAX_TOTAL` | `4` | 最大兄弟总数 |
| `RAG_CONTENT_TYPE_RERANK_ENABLED` | `True` | 内容类型偏置重排 |
| `RAG_INDEX_TABLES` | `True` | 索引表格 |
| `RAG_INDEX_IMAGE_CAPTIONS` | `True` | 索引图片描述 |
| `RAG_IMAGE_OCR_MIN_LENGTH` | `15` | OCR 最小长度 |
| `CONTEXT_LOAD_TIMEOUT_S` | `3.0` | 上下文加载超时（秒） |
| `MEMORY_CONTEXT_MAX_TOKENS` | `800` | 记忆上下文 token 上限 |
| `ENTITY_CONTEXT_MAX_TOKENS` | `600` | 实体上下文 token 上限 |
| `SESSION_CONTEXT_MAX_TOKENS` | `800` | 会话上下文 token 上限 |
| `RRF_K_PARAM` | `60` | RRF K 参数 |
| `SEMANTIC_EMBED_TIMEOUT_S` | `5.0` | 语义嵌入超时（秒） |
| `STREAM_CONCURRENT_LIMIT` | `20` | 流式并发上限 |
| `FALLBACK_BREAKER_THRESHOLD` | `3` | 回退熔断器阈值 |
| `FALLBACK_BREAKER_COOLDOWN_SECONDS` | `30.0` | 回退熔断器冷却（秒） |

#### Audio segmentation

| 字段 | 默认 | 说明 |
|---|---|---|
| `AUDIO_SEMANTIC_BOUNDARY_ENABLED` | `False` | 语义分段开关 |
| `AUDIO_SEMANTIC_BOUNDARY_THRESHOLD` | `0.5` | 语义分段阈值 |
| `AUDIO_SEMANTIC_MIN_SEGMENTS` | `2` | 最小分段数 |
| `AUDIO_SEMANTIC_MAX_SEGMENTS` | `20` | 最大分段数 |
| `AUDIO_SPEAKER_IN_CONTENT` | `True` | 内容中包含说话人标签 |
| `AUDIO_SPLIT_ON_SPEAKER_CHANGE` | `True` | 按说话人切换分块 |

#### Unscoped diversity

| 字段 | 默认 | 说明 |
|---|---|---|
| `UNSCOPED_DIVERSITY_ENABLED` | `True` | 未限定 scope 时按会议均衡 |
| `UNSCOPED_MAX_PER_MEETING` | `5` | 单会议最大 chunk 贡献 |
| `UNSCOPED_FETCH_MULTIPLIER` | `4` | 过采样倍数 |

#### Hierarchical (funnel) RAG

| 字段 | 默认 | 说明 |
|---|---|---|
| `RAG_HIERARCHICAL_ENABLED` | `True` | 主开关（False = legacy 路径） |
| `RAG_FUNNEL_FETCH_MULTIPLIER` | `10` | 宽 fetch 过采样因子 |
| `RAG_FUNNEL_TOP_MEETINGS` | `8` | 第一阶段最大会议数 |
| `RAG_FUNNEL_TOP_FILES` | `12` | 第二阶段最大文件数 |
| `RAG_FUNNEL_MIN_POOL_SIZE` | `12` | 低于此值触发 narrow 回退 |
| `RAG_FUNNEL_AGGREGATION` | `top_k_mean` | `top_k_mean`/`max`/`count` |
| `RAG_FUNNEL_AGG_TOP_K` | `3` | top_k_mean 的 k 值 |
| `RAG_FUNNEL_AGGREGATION_ALPHA` | `0.85` | 聚合混合 alpha |
| `RAG_FUNNEL_TITLE_PRIOR_ENABLED` | `True` | 标题匹配加分 |
| `RAG_FUNNEL_TITLE_PRIOR_WEIGHT` | `0.05` | 每词加分 |
| `RAG_FUNNEL_TITLE_PRIOR_CAP` | `0.15` | 标题加分上限 |
| `RAG_FUNNEL_FILE_PRIOR_ENABLED` | `True` | 文件标题匹配加分 |
| `RAG_FUNNEL_FILE_PRIOR_WEIGHT` | `0.10` | 每词加分 |
| `RAG_FUNNEL_FILE_PRIOR_CAP` | `0.30` | 文件标题加分上限 |
| `RAG_FUNNEL_FILE_PRIOR_FULL_MATCH_BONUS` | `0.20` | 完整匹配额外加分 |
| `RAG_FUNNEL_FILE_PRIOR_MODE` | `additive` | `additive`/`multiplicative` |
| `RAG_FUNNEL_WIDE_K_MIN` / `RAG_FUNNEL_WIDE_K_MAX` | `0` / `0` | 自适应 wide_k 范围（0=禁用） |
| `RAG_FUNNEL_MULTIMODAL_ENABLED` | `True` | funnel 中包含多模态存储 |
| `RAG_FUNNEL_NARROW_MIN_EVIDENCE` | `0.15` | chunk 聚合文件分数下限 |
| `RAG_FUNNEL_EVIDENCE_MODE` | `ratio` | `absolute`/`ratio`/`percentile` |
| `RAG_BROAD_RECALL_SCOPE_CAP` | `8` | 最终 scope 文件数上限 |
| `RAG_FUNNEL_MERGE_STRATEGY` | `rrf` | `rrf`/`zigzag` |
| `RAG_FUNNEL_RRF_K` | `60` | RRF K 常数 |
| `RAG_BROAD_RECALL_MQ_MERGE` | `rrf` | 多查询变体合并策略 |

#### Conversational anchor

| 字段 | 默认 | 说明 |
|---|---|---|
| `RAG_ANCHOR_ENABLED` | `True` | 主开关 |
| `RAG_ANCHOR_TTL_MINUTES` | `30` | Anchor 过期阈值（分钟） |
| `RAG_ANCHOR_TTL_MODE` | `fixed` | `fixed`/`sliding` |
| `RAG_ANCHOR_NARROW_FETCH_MULTIPLIER` | `5` | case 1 过采样 |
| `RAG_ANCHOR_NARROW_FETCH_MULTIPLIER_CASE2` | `3` | case 2 过采样 |
| `RAG_ANCHOR_NARROW_RRF_WEIGHT` | `0.5` | narrow fetch RRF 权重 |
| `RAG_ANCHOR_MAX_IDS` | `8` | 存储 ID 上限 |
| `RAG_ANCHOR_BOOST_IN_BROAD_RECALL` | `True` | 将 anchor 文件注入 broad recall |
| `RAG_ANCHOR_QUOTA_RATIO` | `0.5` | anchor 文件配额比例 |
| `RAG_ANCHOR_ONLY_SCORE_FLOOR_RATIO` | `0.8` | anchor-only 文件分数下限比例 |

#### Pre-rerank dedup / speaker filter

| 字段 | 默认 | 说明 |
|---|---|---|
| `RAG_PRE_RERANK_DEDUP_ENABLED` | `True` | 重排前 ngram 去重 |
| `RAG_PRE_RERANK_DEDUP_THRESHOLD` | `0.92` | 去重相似度阈值 |
| `RAG_SPEAKER_FILTER_PUSHDOWN` | `False` | 说话人过滤下推到 Chroma |

#### Broad recall mode

| 字段 | 默认 | 说明 |
|---|---|---|
| `RAG_MIN_CHUNKS_PER_FILE` | `3` | 最少每文件 chunk 数 |
| `RAG_FAIR_ADAPTIVE_CHUNKS` | `True` | 自适应 chunk 分配 |
| `RAG_FAIR_SIZE_FACTOR_ENABLED` | `True` | 按文件大小调整分配 |
| `RAG_FAIR_CONCURRENCY` | `8` | 并行文件检索上限 |
| `BROAD_RECALL_MAX_FILES` | `50` | legacy 枚举回退 SQL LIMIT |
| `TOP_K_MEETING_SCOPED_FLOOR` | `16` | 会议限定时 TOP_K 下限 |
| `SUMMARY_INTENT_TOP_K` | `12` | 摘要意图 top_k 下限 |
| `RAG_BROAD_RECALL_MULTI_QUERY_ENABLED` | `False` | broad recall 多查询 |

#### Summary vector router

| 字段 | 默认 | 说明 |
|---|---|---|
| `RAG_SUMMARY_ROUTER_ENABLED` | `True` | 按文件摘要嵌入预筛选 |
| `RAG_SUMMARY_ROUTER_TOP_FILES` | `12` | 路由返回文件数 |
| `RAG_SUMMARY_ROUTER_MIN_SCORE` | `0.0` | 距离/相似阈值 |
| `RAG_SUMMARY_ROUTER_FALLBACK_TO_CHUNK` | `True` | 空结果回退 |
| `RAG_SUMMARY_ROUTER_HYBRID_ENABLED` | `True` | 摘要路由混合模式 |
| `RAG_SUMMARY_ROUTER_HYBRID_ALPHA` | `0.6` | 混合权重 |
| `RAG_MEETING_SUMMARY_ROUTER_ENABLED` | `True` | 会议摘要预筛选（未限定模式） |
| `RAG_MEETING_SUMMARY_ROUTER_TOP_MEETINGS` | `10` | 路由返回会议数 |
| `RAG_MEETING_SUMMARY_ROUTER_MIN_SCORE` | `0.0` | 相似度阈值 |
| `RAG_MEETING_SUMMARY_ROUTER_MIN_HITS` | `1` | 最少命中数 |
| `RAG_MEETING_SUMMARY_ROUTER_TIMEOUT_S` | `1.5` | 路由超时（秒，0=无限） |
| `FILE_SUMMARY_CONTEXT_CHARS` | `800` | 每文件摘要注入字符数 |
| `MEETING_SUMMARY_CONTEXT_CHARS` | `1600` | 每会议摘要注入字符数 |
| `MEETING_SUMMARY_BROAD_INJECT_CAP` | `20` | ≤N 会议时注入所有摘要 |
| `FILE_SUMMARY_BROAD_INJECT_CAP` | `50` | ≤N 文件时注入所有摘要 |

#### Query resolver

| 字段 | 默认 | 说明 |
|---|---|---|
| `RESOLVER_ENABLED` | `True` | 历史感知查询解析开关 |
| `RESOLVER_HISTORY_TURNS` | `3` | 使用最近 N 轮 |
| `RESOLVER_HISTORY_TOKEN_BUDGET` | `1500` | 历史 token 硬上限 |
| `RESOLVER_TIMEOUT_S` | `4.0` | 解析超时（秒） |

#### Data retention

| 字段 | 默认 | 说明 |
|---|---|---|
| `CHAT_MESSAGE_RETENTION_DAYS` | `180` | 聊天消息保留天数 |
| `DECAY_STATE_RETENTION_DAYS` | `365` | 衰减状态保留天数 |

### 3.10 Memory

| 字段 | 默认 | 说明 |
|---|---|---|
| `MEMORY_AUTO_EXTRACT` | `True` | 每轮对话后自动提取 |
| `MEMORY_MAX_FACTS_PER_TURN` | `3` | 单轮最大事实数 |
| `MEMORY_EXTRACTION_MODEL` | `""`（=`LLM_MODEL`） | 提取用模型 |
| `MEMORY_DECAY_ENABLED` | `True` | |
| `MEMORY_DECAY_INTERVAL_HOURS` | `24` | 后台循环周期 |
| `MEMORY_DECAY_RATE_PER_DAY` | `0.01` | ~1%/天衰减率 |
| `MEMORY_TTL_DAYS` | `90` | 硬过期 |
| `MEMORY_MAX_CONTEXT_ITEMS` | `6` | 注入 prompt 的最大条数 |
| `MEMORY_MAX_PER_USER` | `500` | 单用户记忆上限（超出按重要性淘汰） |
| `MEMORY_DEDUP_THRESHOLD` | `0.75` | 去重语义相似度阈值 |
| `MEMORY_INITIAL_IMPORTANCE` | `3` | 新记忆默认重要性（1-5） |
| `MEMORY_MIN_IMPORTANCE` | `1` | 重要性下限 |
| `MEMORY_MAX_IMPORTANCE` | `5` | 重要性上限 |
| `MEMORY_SCORING_SEMANTIC_WEIGHT` | `0.3` | 语义相似度权重 |
| `MEMORY_SCORING_DECAY_WEIGHT` | `0.4` | 衰减权重 |
| `MEMORY_SCORING_IMPORTANCE_WEIGHT` | `0.3` | 重要性权重 |
| `MEMORY_CONSOLIDATION_ENABLED` | `True` | 合并相似记忆 |
| `MEMORY_PROFILE_ENABLED` | `True` | LLM 驱动画像刷新 |
| `MEMORY_PROFILE_REFRESH_INTERVAL` | `50` | 每 N 次交互刷新 |
| `MEMORY_CONSOLIDATION_MIN_CLUSTER` | `3` | 聚类最小簇 |
| `MEMORY_CONSOLIDATION_WINDOW_DAYS` | `2` | 合并种子窗口（天） |
| `MEMORY_AUTO_EXTRACT_INITIAL_IMPORTANCE` | `1.0` | 自动提取记忆的初始重要性 |
| `MEMORY_EXTRACTION_INCLUDE_EXISTING` | `True` | 提取时包含已有记忆（防重复） |
| `MEMORY_SEMANTIC_CLUSTER_ENABLED` | `True` | 用向量聚类（否则文本重叠） |
| `KNOWLEDGE_GRAPH_ENABLED` | `True` | |
| `ENTITY_ALIAS_MERGE_THRESHOLD` | `0.85` | 实体别名合并的余弦相似度阈值 |
| `MEMORY_EXTRACTION_MODE` | `balanced` | `precise`/`balanced`/`aggressive` |
| `ENTITY_RELATIONS_LIMIT` | `50` | 单实体返回关系上限 |
| `GLOBAL_MEMORY_LIMIT` | `3` | 有 scope 时全局记忆上限 |
| `SCOPED_MEMORY_STRICT` | `False` | scope 激活时排除未标记记忆 |
| `MEMORY_SEARCH_OVERSAMPLE_FACTOR` | `5` | 向量库过采样倍数（scope 后过滤） |
| `SESSION_MAX_HISTORY` | `50` | 历史消息上限 |
| `SESSION_MAX_TOKENS` | `4096` | 历史消息 token 上限 |
| `SESSION_SUMMARY_ENABLED` | `True` | |
| `SESSION_SUMMARY_MIN_TURNS` | `4` | 达到后才生成 |
| `SESSION_SUMMARY_MAX_ITEMS` | `3` | |
| `SESSION_SUMMARY_MAX_MESSAGES` | `100` | |
| `SESSION_SUMMARY_IDLE_MINUTES` | `15` | 空闲自动摘要间隔（分钟） |
| `SESSION_SUMMARY_STARTUP_BACKFILL` | `False` | 启动时批量回填摘要 |
| `SESSION_CONTEXT_SKIP_THRESHOLD` | `3` | 跳过跨会话摘要的最小选择数 |
| `SKILL_MATCHING_ENABLED` | `True` | 启用/禁用 skill 匹配 |
| `SKILL_MATCH_TIMEOUT_S` | `2.5` | skill 匹配超时 |
| `SKILL_ROUTE_TIMEOUT_S` | `8.0` | skill 路由超时（秒） |
| `SKILL_ROUTING_MIN_SIMILARITY` | `0.35` | skill 路由最低相似度 |
| `WEB_SEARCH_TIMEOUT_S` | `8.0` | Web 搜索超时（秒） |

见 [`memory-and-kg.md`](./memory-and-kg.md)。

### 3.11 Server

| 字段 | 默认 | 说明 |
|---|---|---|
| `ENVIRONMENT` | `dev` | 运行环境：`dev`/`staging`/`prod`；非 dev 必须 `API_KEY` + `CORS_ORIGINS` |
| `HOST` | `0.0.0.0` | |
| `PORT` | `8000` | |
| `CORS_ORIGINS` | `""` | 逗号分隔 |
| `TRUSTED_PROXIES` | `""` | 用于 `X-Forwarded-For` 信任链 |
| `TRUSTED_HOSTS` | `""` | 可信主机列表 |
| `API_KEY` | `""` | SecretStr，**空=dev 模式** |
| `IDEMPOTENCY_OLD_KEYS` | `""` | 密钥轮换时解密幂等载荷的旧密钥（逗号分隔） |
| `SECURITY_HEADERS_ENABLED` | `True` | 安全响应头 |
| `SECURITY_HSTS_MAX_AGE` | `31536000` | HSTS max-age |
| `SECURITY_FRAME_OPTIONS` | `DENY` | X-Frame-Options |
| `SECURITY_REFERRER_POLICY` | `strict-origin-when-cross-origin` | Referrer-Policy |
| `SECURITY_CSP` | `default-src 'self'; ...` | Content-Security-Policy |

### 3.12 Parser / Upload

| 字段 | 默认 | 说明 |
|---|---|---|
| `MAX_PARSE_PAGES` | `1000` | |
| `PARSE_TIMEOUT_SECONDS` | `900` | 单文件解析总超时 |
| `PARSE_TIMEOUT_PER_MB_SECONDS` | `2` | 按文件大小动态超时 |
| `PARSE_TIMEOUT_MAX_SECONDS` | `900` | 动态超时上限 |
| `MAX_UPLOAD_SIZE_MB` | `500` | |
| `PARSER_MAX_IMAGES_PER_PAGE` | `20` | |
| `PARSER_MAX_IMAGE_BYTES` | `8388608` | |
| `DOC_CLEAN_REPETITION_MIN_PAGES` | `3` | 重复行最少出现页数 |
| `DOC_CLEAN_REPETITION_MIN_RATIO` | `0.6` | 重复行最低页比例 |
| `DOC_CLEAN_HEADER_FOOTER_MAX_LINES` | `2` | 页眉/页脚评估行数 |
| `DOC_CLEAN_REPETITION_MAX_LINE_LENGTH` | `120` | 重复检测最大行长度 |
| `MEETING_AUTO_SUMMARIZE_FILES` | `True` | |
| `PER_FILE_SUMMARY_INPUT_MAX_TOKENS` | `8000` | |
| `EXTRACTION_INPUT_MAX_TOKENS` | `1500` | 提取 LLM 输入 token 上限 |
| `EXTRACTION_MIN_ANSWER_CHARS` | `50` | 提取最小回答字符数 |
| `MULTIMODAL_CAPTIONING_ENABLED` | `True` | |
| `VISION_COMBINED_EXTRACTION_ENABLED` | `True` | 视觉+提取合并 |
| `MULTIMODAL_CAPTION_OCR_DEDUP_ENABLED` | `True` | OCR/描述去重 |
| `MULTIMODAL_CAPTION_OCR_DEDUP_TIMEOUT_SECONDS` | `8.0` | 去重超时 |
| `VIDEO_KEYFRAMES_ENABLED` | `False` | |

## 4. 环境变量命名规则

pydantic-settings 默认按字段名大写匹配，不需要额外前缀。例如：

```bash
# .env
LLM_BINDING=anthropic
LLM_API_KEY=sk-ant-...
EMBEDDING_BINDING=ollama
EMBEDDING_HOST=http://localhost:11434
API_KEY=super-secret
LOG_FORMAT=json
```

`LOG_FORMAT` 不在 `Settings` 字段中，由日志配置直接读取 `os.environ`。

## 5. 运行时更新：`PUT /api/v1/settings`

- 只更新**内存中的** `settings` 对象，**不会写回磁盘**
- 会调用各子系统的 `reset_*()` 以触发单例重建：
  - `reset_llm()` / `reset_embeddings()` / `reset_vectorstore()` / `reset_reranker()` / `reset_query_rewriter()`
- 切换 Embedding 维度时要立即跟 `POST /settings/rebuild-vectors`
- 进程重启后失效 — 持久化配置仍需改 `.env` 或 `config/main.yaml`

## 6. 典型场景

### 6.1 本地 LLM + 云端 ASR（Ollama + AssemblyAI + BGE reranker）

> ⚠️ `RERANKER_BINDING=bge` 和 `EMBEDDING_BINDING=huggingface` 依赖可选的
> `huggingface` extra（会拉 `sentence-transformers` + `torch`，Linux 下约 3 GB）。
> 安装命令：`uv sync --extra huggingface`

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

### 6.2 云端 OpenAI + Cohere rerank

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
LLM_MODEL=gpt-4o-mini        # 部署名
LLM_API_KEY=...

EMBEDDING_BINDING=azure_openai
EMBEDDING_BASE_URL=https://your.openai.azure.com
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_API_KEY=...
```

## 7. 安全与最佳实践

- ✅ 所有 secret 都用 `SecretStr` — 不要直接 `logger.info(settings.LLM_API_KEY)`
- ✅ 非 dev 环境**必须**配 `API_KEY`，否则 lifespan 拒绝启动
- ✅ CORS 上线前从默认的宽松值收紧到显式白名单
- ✅ `.env` 加入 `.gitignore`（仓库中有 `.env.example` 模板）
- ⚠️ `LLM_CACHE_ENABLED=True` 会缓存响应 — 对强一致性场景请关闭
- ⚠️ 修改 `EMBEDDING_*` 或 `DISTANCE_METRIC` 后必须 rebuild vectors
