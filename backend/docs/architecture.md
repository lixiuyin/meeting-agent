# 系统架构总览

> Meeting Agent 后端整体架构说明。本文档作为 `backend/docs/` 的入口，其余子系统文档均从此处交叉引用。

## 1. 目标与定位

Meeting Agent 是一个**"会议记录 + 文档"知识助手**：

- 摄入视频/音频（转写）、PDF/PPTX/图像（解析+OCR）
- 将内容切块后存入向量库（Chroma） + BM25 / FTS5 倒排索引
- 提供 **RAG 问答**（同步/流式）、**长期记忆**、**知识图谱**
- 通过 **REST API**、**WebSocket**、**MCP Server** 三种协议对外暴露能力

## 2. 分层结构

```
backend/src/
├── main.py                 # FastAPI app 入口，注册中间件 + 路由 + 生命周期
├── mcp.py                  # MCP 服务（独立入口：python -m src.mcp）
│
├── api/                    # HTTP 接入层
│   ├── dependencies.py     # FastAPI 依赖注入（公共 dep）
│   ├── lifespan.py         # 启动/关闭编排（critical vs best-effort）
│   ├── middleware.py       # RequestId、限流、日志、CORS
│   ├── metrics.py          # /metrics 统计端点
│   └── routers/            # 按领域拆分的路由
│       ├── file_download.py # 会议文件下载（API Key 或短期 token；先于 meetings 注册）
│       ├── meetings/       # 会议 CRUD、上传、转写、总结、搜索（14 个子路由模块）
│       ├── chat.py         # /chat, /chat/stream, /chat/search
│       ├── sessions.py     # 会话列表 / 摘要 / cite / 跨会话搜索
│       ├── memory.py       # 记忆 CRUD / 批量 / 衰减 / 知识图谱实体
│       ├── settings/       # 运行时配置 + rebuild-vectors（__init__.py + _common + _rebuild）
│       ├── skills.py       # Skill 注册 / 匹配 / 调用
│       ├── health.py       # /health 及 live/ready/traffic/index-consistency
│       └── websocket.py    # 实时进度推送（WS /api/v1/ws）
│
├── services/               # 业务 & 领域服务
│   ├── chain/              # Ask 编排（PipelineContext/Result + 流式，25 个模块）
│   ├── rag/                # 向量库、索引、检索、重排、查询改写（27 个模块）
│   ├── processor/          # 上传 → 解析/转写 → 入库 的统一管线（含摘要生成）
│   │   └── _processors/    # 按文件类型分发：text / document / image / av
│   ├── parser/             # profile + 路由 + 云 API 级联（marker/mineru/paddle）+ local 兜底
│   │   └── providers/      # local / marker_api / mineru_api / paddle_api
│   ├── asr/                # 语音转写（AssemblyAI + 音频切片 + speaker 映射）
│   ├── vision/             # 图像描述 / OCR / 去重（_captioner / _batch / _dedupe / _client）
│   ├── files/              # 文件类型分类（_kinds）与资产管理（_assets）
│   ├── transcriber.py      # ASR 调度器（委托 asr/ 包）
│   ├── llm/                # LLM Provider 注册表、缓存、Prompts、遥测（6 个子模块）
│   ├── embedder.py         # Embedding Provider 单例
│   ├── memory/             # 事实提取 / 衰减 / 合并 / 画像 / 搜索 / 历史
│   │   └── _service/       # MemoryService mixins：extraction / consolidation / crud / search / decay_sync / profile
│   ├── knowledge_graph/    # 实体 / 关系 / 向量化（含 _parsing 实体解析）
│   ├── search.py           # Web 搜索接入（DuckDuckGo/Tavily/Bing…）
│   ├── tokenizer.py        # tiktoken 单例封装
│   ├── stream_bus.py       # SSE 事件总线（生产者-消费者）
│   ├── traffic_control.py  # 并发 / 限速 / 熔断 / 错误率
│   ├── registry.py         # 可重置服务注册表
│   ├── retention.py        # 数据保留策略
│   └── websocket.py        # WebSocketManager 单例
│
├── utils/                  # 通用工具
│   └── supervised_task.py  # 受监管后台任务注册表
│
├── core/                   # 基础设施
│   ├── config.py           # pydantic-settings 配置聚合
│   ├── _config_yaml.py     # YAML 配置加载
│   ├── _config_validation.py # 配置校验辅助
│   ├── constants.py        # 路径常量（派生自 __file__）
│   ├── settings_epoch.py   # settings epoch 追踪（缓存失效）
│   ├── security.py         # verify_api_key 依赖
│   ├── exceptions.py       # 领域异常
│   ├── tracing.py          # OpenTelemetry span 封装
│   ├── trace.py            # 请求级 trace context
│   ├── audit.py            # 结构化审计日志
│   ├── logging.py          # 结构化日志配置
│   ├── http_client.py      # 共享 httpx.AsyncClient 单例
│   ├── metrics.py          # 应用指标定义
│   ├── tz.py               # 时区工具（UTC 规范）
│   └── database/           # SQLite 封装 + 迁移 + CRUD 仓储
│
└── models/                 # Pydantic 响应/请求模型
    ├── meeting_status.py   # MeetingStatus 枚举
    └── schemas/            # 按领域拆分的 schema
```

## 3. 核心数据流

### 3.1 上传摄入（Ingest）

```
Upload API → 落盘 + meeting_files 记录
           ↓
BackgroundTasks → process_meeting_file()
           ↓
   ┌────────────┬────────────────┐
   ↓            ↓                ↓
 transcribe   parse (cascade)   fetch_metadata
 (video/audio)(PDF/PPTX/image)
   └────────────┴────────────────┘
           ↓
   index_meeting() → Chroma + BM25 索引
           ↓
   状态更新 → WebSocket 通知
```

细节见 [`ingest-pipeline.md`](./ingest-pipeline.md)。

### 3.2 问答（Query / RAG）

```
Chat API → ask() / ask_stream()
       ↓
_run_pipeline(PipelineContext)
       ↓
  1. ensure_session            # 创建或恢复会话
  2. rewrite_query             # LLM 改写 / 多查询
  3. parallel gather:
     ├ retrieve + rerank + dedup（native / raganything / hybrid_multimodal）
     ├ load memories
     ├ load session context
     ├ load entity context (KG)
     ├ web search (可选)
     └ load history
  4. build_context             # 拼装最终 prompt
  5. generate_answer           # LCEL chain → LLM
  6. save_messages             # 持久化用户/助手消息
  7. schedule_fact_extraction  # 后台提取记忆
```

> 多模态检索采用 **dual-store**：原生 Chroma/BM25 与 RAGAnything 并存。上传阶段可双写，
> 查询阶段可按请求 `rag_mode` 切换（`native` / `multimodal` / `hybrid_multimodal` / `auto`）。

细节见 [`chain-pipeline.md`](./chain-pipeline.md) 与 [`rag.md`](./rag.md)。

## 4. 跨切面关注点

| 关注点 | 实现位置 | 说明 |
|---|---|---|
| **配置** | `core/config.py` + `config/main.yaml` + `.env` | 三级覆盖：YAML → .env → env vars |
| **认证** | `core/security.py` | `X-API-Key` Header + `hmac.compare_digest` |
| **限流** | `api/middleware.py`（slowapi） | 默认读类约 60/min；上传/聊天/设置等见 [`api-reference.md`](./api-reference.md)；尊重 `X-Forwarded-For` |
| **请求追踪** | `RequestIdMiddleware` + `core/trace.py` | `X-Request-ID`、span 嵌套、event 记录 |
| **审计** | `core/audit.py` | 敏感变更结构化日志 |
| **错误** | `core/exceptions.py` + 全局异常处理 | 对外生成通用错误，内部 `exc_info=True` |
| **日志** | Python `logging` + `LOG_FORMAT=json` | 惰性格式化 `logger.error("%s", x)` |
| **并发安全** | 所有重对象均为 DCL 单例 | LLM / Embedder / Reranker / VectorStore |
| **阻塞调用** | `asyncio.to_thread()` | CPU 密集 / 同步 IO 全部离主线程 |
| **流量治理** | `services/traffic_control.py` | semaphore + token-bucket + circuit breaker |

## 5. 子系统索引

| 文档 | 主题 |
|---|---|
| [`architecture.md`](./architecture.md) | 本文：系统总览 |
| [`lifespan-and-operations.md`](./lifespan-and-operations.md) | 启动 / 关闭 / 运行态维护 |
| [`configuration.md`](./configuration.md) | 配置项与环境变量 |
| [`api-reference.md`](./api-reference.md) | REST 路由与 schema |
| [`database.md`](./database.md) | SQLite、Alembic + `_MIGRATIONS`、主要表、Repository |
| [`ingest-pipeline.md`](./ingest-pipeline.md) | 上传 → 解析 → 入库 管线 |
| [`rag.md`](./rag.md) | 检索-重排-生成 全流程 |
| [`chain-pipeline.md`](./chain-pipeline.md) | `ask()` 编排细节与流式事件 |
| [`llm-and-traffic.md`](./llm-and-traffic.md) | LLM/Embedding Provider + 流量治理 |
| [`memory-and-kg.md`](./memory-and-kg.md) | 记忆系统 + 知识图谱 |
| [`../../docs/diagrams/rag-pipeline.md`](../../docs/diagrams/rag-pipeline.md) | RAG 查询路径示意图（Mermaid，英文） |
| [`../../docs/diagrams/memory-and-kg.md`](../../docs/diagrams/memory-and-kg.md) | 记忆分层与衰减公式速览（英文） |
| [`mcp-server.md`](./mcp-server.md) | MCP Server 工具 |
| [`cli.md`](./cli.md) | 终端前端（命令、分页、导出、设置） |
| [`SKILLS.md`](./SKILLS.md) | Skill 系统（Markdown 配置、意图匹配） |
| [`benchmarking.md`](./benchmarking.md) | 基准测试工具 |
| [`operations/alembic.md`](./operations/alembic.md) | Alembic 迁移与 stamp 流程 |
| [`operations/backup.md`](./operations/backup.md) / [`restore.md`](./operations/restore.md) | 备份与恢复 |
| [`operations/runbooks/`](./operations/runbooks/) | 故障 runbook（429、Chroma、熔断等） |

## 6. 前后端边界

- 前端位于 `frontend/`（React 19 + Vite 6 + AntD 6）
- 开发环境通过 Vite 代理 `/api` → `localhost:8000`
- 容器内通过 nginx 反向代理 `/api/` → `backend:8000`
- WebSocket 地址：`/api/v1/ws`（进度 / 完成事件）
- Streaming Chat 使用 SSE：`POST /api/v1/chat/stream`

## 7. 关键设计决策

1. **单例 + 双检锁**：所有初始化代价高的资源（LLM 客户端、Chroma、Embedder…）只初始化一次，但可通过 `reset_*()` 在 `PUT /settings` 后热切换。
2. **关键路径 vs 尽力路径**：启动阶段明确区分两类任务，保证少数关键组件失败即停机，非关键组件失败仅记录。
3. **阻塞隔离**：任何可能阻塞的调用（LLM、解析、SQLite 写、Chroma 写、视频转码）统一 `asyncio.to_thread`，保证 event loop 响应性。
4. **读写分离**：SQLite WAL + `get_connection()`（无锁读） + `get_write_connection()`（串行化写）。
5. **内容哈希幂等**：上传文件按 `content_hash` 去重；相同文件重复上传跳过解析。
6. **trace-first 可观测**：每个管线步骤在 `core/trace.py` 产生带时长/事件/错误的 span，前端可直接渲染。
