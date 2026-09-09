# Meeting Agent

[English](README.md) | 简体中文

[![CI](https://github.com/lixiuyin/meeting-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/lixiuyin/meeting-agent/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![Node 22–24](https://img.shields.io/badge/node-22--24-green.svg)](https://nodejs.org/)

Meeting Agent 是一个全栈 RAG 应用：它可以接收会议录音、文档、文本/数据文件和图像，完成转录或解析，将规范化内容写入语义与词法索引，并提供带引用的问答、对话记忆、知识图谱实体跟踪以及可选的 Web 搜索增强。准确的文件扩展名与处理器支持矩阵以[摄取管线文档](backend/docs/ingest-pipeline.md#45-support-formats-and-old-office)为准。

> **文档状态：**维护中的指南和架构参考已于 2026-09-09 根据源码树、OpenAPI 契约、Compose 拓扑和 Alembic 迁移链完成核对。带日期的审计报告仍作为历史证据保留；请从 [`docs/README.md`](docs/README.md) 进入当前文档导航。

## 演示视频

项目的完整流程和功能演示发布在 YouTube 频道：

[![在 YouTube 观看](https://img.shields.io/badge/Watch%20on-YouTube-FF0000?style=for-the-badge&logo=youtube&logoColor=white)](https://www.youtube.com/@lixiuyin)

> 点击缩略图可前往 YouTube 播放；GitHub Markdown 不支持直接嵌入可播放的视频。

| 完整演示 | 调用 Skills |
|:---:|:---:|
| [![完整演示](https://img.youtube.com/vi/IuMp47AY_Do/maxresdefault.jpg)](https://youtu.be/IuMp47AY_Do) | [![调用 Skills](https://img.youtube.com/vi/YDGAmJN0t0M/maxresdefault.jpg)](https://youtu.be/YDGAmJN0t0M) |
| 从上传、摄取到带引用问答的端到端概览 | 通过 API 注册自定义 Skill，并通过直接调用或聊天意图匹配触发 |
| **分步演示** | **Memory 与知识图谱** |
| [![分步演示](https://img.youtube.com/vi/76IJ_jyXTMU/maxresdefault.jpg)](https://youtu.be/76IJ_jyXTMU) | [![Memory 与知识图谱](https://img.youtube.com/vi/027BUwJe1lE/maxresdefault.jpg)](https://youtu.be/027BUwJe1lE) |
| 展示无范围（全部会议）、会议范围和文件范围三种聊天层级，以及检索范围如何随选择逐步收窄 | 长期记忆、知识图谱实体和跨会话召回 |

### 旧版演示与当前系统的差异

> **版本提示：**这些视频录制于较早版本，只展示当前产品的一部分。它们仍可用于理解上传、检索、引用、Skill 和 Memory 等核心概念，但部分导航、页面和工作流已经变化。实际运行的应用和维护中的[文档索引](docs/README.md)才是当前事实来源。

| 领域 | 早期视频展示的内容 | 后续新增或显著增强的能力 | 当前界面入口 |
|---|---|---|---|
| 聊天生命周期 | 基础问答、流式回答和来源引用 | 停止正在生成的回答、撤回当前轮次，以及通过创建不可变对话分支来编辑或重新生成历史消息，而不是覆写原历史 | **Chat**、**History** |
| 会话延续 | 同一对话中的普通追问 | 预览来源变化，并选择最新状态、原保存范围或已保存证据快照继续会话 | **Chat 参数**、**History** |
| Memory 工作区 | 长期记忆、实体和跨会话召回 | 七个视图：**Projects**、**Memories**、**Decisions & tasks**、**State changes**、**Meeting review**、**Entities**、**Past Sessions**；支持类型化事实、生命周期状态、证据、版本、对比和项目筛选 | **Memory** |
| 项目与会前准备 | 未作为统一工作流展示 | 项目目录与材料归属、未完成决策/任务、近期状态变化，以及按项目组织的会议复盘准备 | **Memory → Projects / Meeting review** |
| 素材与证据治理 | 上传和阅读会议文件 | 可审核的素材角色/领域/批准元数据、不可变语义历史、不合格证据拒绝、证据索引同步状态，以及带重新索引的说话人识别 | **Materials** |
| 来源导航 | 打开被引用的素材 | 直接跳转到 PDF 页、幻灯片、转录时间戳和高亮证据片段，包括原始 PDF 与解析结果同步对照 | **Chat 引用**、**Materials** |
| 检索控制 | 全会议、会议级和文件级范围 | 快速/均衡/深入检索配置，一致的 Memory 模式，本地置信度检查后的可选 Web 回退，以及生成无法安全完成时带明确标签的来源支撑降级 | **Chat 参数** |
| 可靠性与维护 | 未详细展示运维能力 | 使用 SQLite 持久化处理队列完成摄取、摘要、说话人更新和事实抽取；界面可查看向量重建进度、失败和取消状态 | **Settings → System** |

由于界面和功能集合已经发生明显变化，建议重新录制视频，以准确展示当前端到端产品。只要保留上述提示，现有视频仍可作为早期版本的概念演示继续公开。

## 核心能力

- **多模态摄取** —— 支持已注册的视频/音频、PDF/Office、文本/数据和图像类型；具备流式大小限制、文件名加固、二进制签名检查、内容哈希和持久化处理任务。
- **云原生解析级联** —— 本地内容画像会选择有序的 Marker、MinerU 和 Paddle 路由；云服务结果必须通过质量门控。纯文本在本地读取，且只有 PDF 具有最终 PyMuPDF 文本回退。所有候选路由均不支持或耗尽时会进入明确失败状态。
- **带说话人分离的 ASR** —— 使用 AssemblyAI；可编辑说话人到真实姓名的映射，并重新索引受影响文件的向量和文件摘要。
- **混合检索** —— 结合 Chroma 语义检索与 BM25 词法检索，通过 Reciprocal Rank Fusion 融合，支持按文件公平分配、会话连续性的锚点保留，以及 Cohere / BGE 重排。
- **统一引用** —— 文本块、文件摘要和会议摘要使用统一的 `[N]` 编号；Chat 界面中的引用均可点击，并跳转到对应来源页、幻灯片或时间戳。
- **受治理的长期记忆** —— 在一个有边界、虚拟化的 Memory 工作区内统一管理绑定证据且带版本的事实、生命周期审核、双时态查询、项目/任务、召回衰减、知识图谱实体和情景摘要。
- **流式响应与持久化任务** —— 聊天和摘要生成通过 SSE 推送事件；文件处理、摘要、说话人重新索引和事实抽取使用支持租约、重试和死信状态的 SQLite 持久化队列。向量重建仍作为独立受控的维护任务运行。
- **多提供商 LLM / Embedding** —— 聊天支持 OpenAI、Azure OpenAI、Anthropic、
  DeepSeek、OpenRouter、Groq、Together、Mistral、Ollama、LM Studio、vLLM 和
  llama.cpp。Embedding 支持 OpenAI、Azure OpenAI、Ollama、LM Studio、
  Hugging Face、Jina、Cohere、Google Vertex AI、OpenRouter、DeepSeek、Together、
  Groq、Mistral 和 vLLM。
- **加固 API** —— 版本化 `/api/v1`、按端点限流、使用 AES-GCM 加密响应存储的幂等键、HMAC 签名文件下载令牌，以及结构化 JSON 日志。
- **MCP 服务** —— 通过 stdio（以及可选 HTTP）暴露六个工具，使 Claude 或其他 Agent 能将本系统作为后端使用。

## 架构

```text
┌─────────────────────────────────────────────────────────────────┐
│                       前端（React 19）                            │
│  聊天 · 素材 · 记忆 · 历史 · 生成 · 设置                         │
└──────────────────────────────┬──────────────────────────────────┘
                                │ /api/v1（经 Vite/nginx 代理）
┌──────────────────────────────▼──────────────────────────────────┐
│                   FastAPI · LangChain LCEL                       │
│                                                                  │
│  上传 → 解析/转录 → 分块 → 嵌入 → Chroma                         │
│                                  ↓                               │
│                              BM25 + FTS5                         │
│                                                                  │
│  查询 → 范围路由 → 检索 → 重排 → 上下文                          │
│                                      ↓                           │
│                                     LLM                          │
│                                      ↓                           │
│                                  回答 + 引用                      │
│                                                                  │
│  持久任务：摄取 · 摘要 · 事实抽取 · 说话人更新                   │
│  Memory：类型化版本 · 证据 · 生命周期 · KG · 衰减                │
└──────────────────────────────────────────────────────────────────┘
```

**后端** —— FastAPI 0.135+ · LangChain LCEL · ChromaDB · SQLite（WAL、Alembic 管理）· slowapi · Pydantic v2。
**前端** —— React 19 · TypeScript · Vite 6 · Ant Design 6 · react-router v7 · framer-motion。
**基础设施** —— Docker Compose · Helm Chart · Prometheus + Grafana + Loki + Promtail（可选可观测性栈）。

## 快速开始

### Docker（推荐）

```bash
cp backend/.env.example backend/.env
# 编辑 backend/.env：设置 LLM_API_KEY（必需）和 ASSEMBLYAI_API_KEY（处理视频时需要）
make start
```

`make start` 会以 `restart: unless-stopped` 策略在后台启动容器。使用 `make status` 查看状态，使用 `make stop` 优雅停止。

Compose 声明的端口映射（宿主机 → 容器）：后端 `7008 → 8000`，前端 `8307 → 8080`。

如需启用鉴权的 Compose 部署，请将 `.env.compose.example` 复制为 `.env.compose`，并设置共享的 `API_KEY`、`PRINCIPAL_PEPPER`、`CORS_ORIGINS` 和非开发模式的 `ENVIRONMENT`。模型提供商密钥仍存放在 `backend/.env`；`.env.compose` 已被 Git 忽略。

- 前端：<http://localhost:8307>
- 后端 API：<http://localhost:7008>
- API 文档：<http://localhost:7008/docs>
- WebSocket：`ws://localhost:7008/api/v1/ws`

### 手动安装

```bash
# 后端（Python 3.12+）
cd backend
uv sync --dev                          # 推荐；使用 uv.lock 保证可复现安装

# 可选依赖（仅在确实使用对应提供商时安装）：
#   uv sync --dev --no-group production --extra multimodal   # 仅限开发评估；参见 SECURITY.md
#   uv sync --dev --extra google       # Vertex AI Embedding
#   uv sync --dev --extra huggingface  # 本地 HF Embedding + BGE 重排器
#   uv sync --dev --extra local        # huggingface + llama-cpp-python（完全离线）
#   uv sync --dev --extra observability # Sentry + OpenTelemetry

cp .env.example .env                   # 设置 LLM_API_KEY 和 ASSEMBLYAI_API_KEY
uv run python -m uvicorn src.main:app --reload --port 7008  # http://localhost:7008

# 前端（Node 22–24；在另一终端执行；`.node-version` 选择 Node 24）
cd frontend
npm ci
npm run dev                            # http://localhost:8307，代理 /api → :7008
```

### 项目级快捷命令

```bash
make dev            # 并行启动后端和前端
make dev-be         # 仅启动后端
make dev-fe         # 仅启动前端
make cli            # 交互式终端前端（scripts.cli_agent）
make lint           # 检查全部代码
make test           # 运行全部测试
make e2e-auth       # 隔离的生产模式 API Key 浏览器检查
make e2e-full-stack # 隔离的真实浏览器验收套件
make qa             # 完整 QA：lint + 测试 + 构建 + 隔离 Playwright E2E
make clean          # 删除生成文件
```

### Pre-commit Hooks

```bash
pip install pre-commit && pre-commit install
```

`.pre-commit-config.yaml` 中配置了 ruff、eslint、prettier、bandit、gitleaks 和 detect-secrets。

## 配置

三级覆盖顺序（越靠后优先级越高）：

1. `backend/config/main.yaml` —— 非敏感默认值（模型名称、RAG 参数、上传限制）。
2. `backend/.env` —— 密钥和环境级覆盖。
3. **环境变量** —— 用于 Docker / CI。

配置通过 pydantic-settings 合并。`backend/.env.example` 有意只包含凭据和常见覆盖；高级调优请使用 `backend/config/main.yaml` 或查阅 `backend/docs/configuration.md`。

### 关键配置

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `LLM_BINDING` | `openai` | `openai`、`azure_openai`、`anthropic`、`deepseek`、`openrouter`、`groq`、`together`、`mistral`、`ollama`、`lm_studio`、`vllm`、`llama_cpp` |
| `LLM_MODEL` | `gpt-4o-mini` | 当前 Binding 支持的任意聊天模型 |
| `LLM_API_KEY` | *必需* | 所选 LLM 提供商的 API Key |
| `LLM_BASE_URL` / `LLM_HOST` | *空* | OpenAI 兼容或本地提供商的自定义端点 |
| `EMBEDDING_BINDING` | `openai` | `openai`、`azure_openai`、`ollama`、`lm_studio`、`huggingface`、`jina`、`cohere`、`google`、`openrouter`、`deepseek`、`together`、`groq`、`mistral`、`vllm` |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding 模型 |
| `EMBEDDING_DIMENSION` | `1536` | 必须与模型的向量维度一致 |
| `ASR_PROVIDER` | `assemblyai` | 目前仅支持 `assemblyai` |
| `ASSEMBLYAI_API_KEY` | *处理音视频时必需* | 只能通过环境变量设置，不能写入 YAML |
| `OCR_PROVIDER` | `marker` | 路由提示：`marker`、`mineru`、`paddle` |
| `RAG_RETRIEVER_PROVIDER` | `hybrid` | `vector`、`hybrid`、`multimodal`、`hybrid_multimodal`（`native` 是已弃用的 `vector` 别名） |
| `RAGANYTHING_ENABLED` | `false` | 仅供开发使用的多模态分支；上游依赖修复前禁止用于生产环境 |
| `SEARCH_BINDING` | `exa` | Web 搜索：`duckduckgo`、`serpapi`、`tavily`、`bing`、`exa`；留空即禁用 |
| `MEMORY_AUTO_EXTRACT` | `true` | 从每轮问答自动抽取事实 |
| `KNOWLEDGE_GRAPH_ENABLED` | `false` | 可选研究特性：将实体和关系写入知识图谱 |
| `ENVIRONMENT` | `dev` | `dev`、`staging`、`production`（`prod` 是别名；非开发环境必须设置 `API_KEY`） |
| `API_KEY` | *空* | 空值表示开发模式（绕过鉴权）；staging/production 必须设置 |
| `PRINCIPAL_PEPPER` | *空* | 非开发环境中生成稳定、不可逆主体标识所需的密钥 |
| `PRINCIPAL_ID` | *未设置* | 可选的、已验证的现有所有者 ID，用于 API Key 轮换时保持归属；它不是多用户身份认证 |
| `LOG_FORMAT` | `text` | 设置为 `json` 可启用结构化日志 |

### Helm 部署说明（SQLite）

- 后端必须保持单副本（`backend.replicaCount=1`），因为 SQLite 无法安全共享写入。
- 通过 Kubernetes Secret 提供密钥，并设置 `backend.secretName`。
- 为保证 SQLite 安全，项目有意不提供 HPA / PDB 模板。

### 使用 Dashscope（Qwen）

```env
LLM_MODEL=qwen-plus
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_API_KEY=sk-your-dashscope-key
```

## MCP 服务

```bash
uv run python -m src.mcp                  # stdio 传输
MCP_TRANSPORT=streamable-http MCP_HTTP_PORT=9000 \
  MCP_API_KEY=replace-me uv run python -m src.mcp  # 回环地址 HTTP
```

提供的工具：`list_meetings`、`search_meetings`、`ask_about_meetings`、`manage_memory`、`list_skills`、`invoke_skill`。

## API 端点

所有应用路由都使用 `/api/v1` 版本前缀。受保护路由通过 `X-API-Key` 请求头认证（`API_KEY` 为空表示开发模式）；存活、就绪和基础健康探针有意保持无需认证。限流按端点设置：上传/聊天 20 次/分钟、设置 5 次/分钟、读取 60 次/分钟。错误统一使用 `ErrorResponse` 包装（`code`、`message`、`request_id`、`details`）。完整契约请参见 [`backend/docs/api-reference.md`](backend/docs/api-reference.md)。

### Meetings

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/meetings/upload` | 上传文件；未传 `meeting_id` 时创建新会议 |
| `POST` | `/meetings` | 创建空会议 |
| `GET` | `/meetings` | 列出会议，可按状态筛选 |
| `GET` | `/meetings/{id}` | 获取会议详情与文件列表 |
| `PUT` | `/meetings/{id}` | 更新会议元数据 |
| `DELETE` | `/meetings/{id}` | 删除会议及其全部文件 |
| `GET` | `/meetings/{id}/files` | 列出会议文件 |
| `GET` | `/meetings/{id}/files/{fid}` | 下载文件（请求头 `X-API-Key` 或 `?token=`） |
| `POST` | `/meetings/file-token` | 签发短期全局文件令牌 |
| `POST` | `/meetings/{id}/files/{fid}/signed-url` | 签发文件范围的 HMAC 签名 URL |
| `GET` | `/meetings/assets` | 按相对路径获取会议资产 |
| `GET` | `/meetings/{id}/files/{fid}/timeline` | 获取文件时间线（片段/页面/图像说明/文本） |
| `PATCH` | `/meetings/{id}/files/{fid}/semantics` | 审核素材角色/批准状态，并使用版本围栏重新索引 |
| `GET` | `/meetings/{id}/files/{fid}/semantics/history` | 获取素材语义审核历史 |
| `POST` | `/meetings/{id}/files/{fid}/evidence-location` | 将证据解析到页面/幻灯片/时间戳 |
| `GET` | `/meetings/{id}/files/{fid}/speakers` | 列出说话人映射 |
| `PUT` | `/meetings/{id}/files/{fid}/speakers` | 更新说话人到真实姓名的映射 |
| `GET` | `/meetings/{id}/files/{fid}/speakers/{code}/audio` | 获取说话人音频样本 |
| `DELETE` | `/meetings/{id}/files/{fid}` | 删除会议中的单个文件 |
| `POST` | `/meetings/{id}/summary` | 生成或获取会议摘要 |
| `POST` | `/meetings/{id}/summary/stream` | 通过 SSE 流式生成会议摘要 |
| `POST` | `/meetings/{id}/reprocess` | 重新索引会议的全部文件 |
| `POST` | `/meetings/{id}/files/{fid}/reprocess` | 重新索引单个文件 |
| `GET` | `/meetings/{id}/transcript` | 获取完整转录文本 |
| `GET` | `/meetings/{id}/transcript/timestamps` | 获取带时间戳片段的转录 |
| `GET` | `/meetings/{id}/export` | 导出会议（JSON / Markdown / TXT） |
| `GET` | `/meetings/search/content` | 在转录中执行全文搜索 |

### Chat

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/chat` | 带 Memory 的 RAG 问答；每次查询的 `rag_mode` 可为 `vector` / `hybrid` / `multimodal` / `hybrid_multimodal` / `auto` |
| `POST` | `/chat/stream` | 通过 SSE 流式回答（token / sources / status / trace / done 事件） |
| `GET` | `/chat/runs/{run_id}` / `events` | 查看持久化运行记录并重放事件 |
| `POST` | `/chat/runs/{run_id}/cancel` / `withdraw` | 取消生成或撤回该轮对话 |
| `POST` | `/chat/search` | 仅执行语义 + BM25 搜索，不调用 LLM |

### Sessions

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/sessions` | 列出聊天会话 |
| `GET` | `/sessions/{id}/messages` | 获取带来源的会话消息历史 |
| `POST` | `/sessions/{id}/branches` | 从持久化消息边界创建分支，用于编辑/重新生成 |
| `GET` | `/sessions/{id}/continuation-preview` | 校验最新状态/原保存范围/原保存快照的会话延续方式 |
| `DELETE` | `/sessions/{id}` | 删除会话及其消息 |
| `POST` | `/sessions/batch-delete` | 一次删除最多 100 个会话 |
| `POST` | `/sessions/{id}/summarize` | 生成会话摘要 |
| `GET` | `/sessions/{id}/summary` | 获取已有会话摘要 |
| `GET` | `/sessions/{id}/cite` | 获取会话引用上下文 |
| `GET` | `/sessions/summaries` | 获取跨会话摘要列表 |
| `POST` | `/sessions/search` | 对历史会话执行语义搜索 |

### Memory 与知识图谱

`/memory` 工作区用于审核、组织和复用从对话与会议材料中提取的长期知识。
记录会保留生命周期状态、版本、项目范围和来源证据，而不是被当作未经验证的自由文本模型记忆。

| 视图 | 用户可见功能 |
|---|---|
| **项目目录** | 创建项目目录、绑定会议材料、准备会议，以及查看项目范围内的任务和变化 |
| **记忆** | 浏览个人/项目记忆与参考事实；搜索、筛选、新建、编辑、确认、撤回、导入、导出、反馈、衰减、删除和修复向量索引 |
| **决策与待办** | 按项目、负责人、状态、截止时间、来源和历史时间查询决策、行动项与项目事实 |
| **状态变化** | 对比两个业务时间边界之间已经记录的事实状态 |
| **会议审核** | 审核自动抽取的候选事实及冲突、检查来源证据、编辑事实，并确认或撤回版本 |
| **实体** | 浏览抽取出的实体与关系、合并别名，以及删除错误实体 |
| **历史会话** | 浏览情景式会话摘要、主题和决策，并继续原始对话 |

关键治理行为：

- 已确认的个人或项目记忆可以参与后续语义召回；
- 参考事实可以被检查，但不会自动转化为个人记忆；
- 撤回会保留版本历史，而删除会移除记录；
- 证据链接可以返回来源会话或材料中的精确位置；
- 双时态查询会区分事实何时有效与系统何时得知该事实。

桌面端标签栏和窄屏下拉选择器提供相同的七个视图。资料库选择器、筛选器和批量操作均位于页面卡片内部；桌面端仅虚拟化记录区域滚动，窄屏设备使用有边界的响应式列表。交互与布局细节见[前端 Memory 工作区文档](frontend/docs/architecture.md#memory-workspace)，抽取、持久化、召回、生命周期和知识图谱机制见[记忆与知识图谱文档](backend/docs/memory-and-kg.md)。

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` / `POST` / `PUT` / `DELETE` | `/memory` | 长期记忆 CRUD |
| `GET` / `PUT` | `/memory/projects` | 项目目录与带版本校验的更新 |
| `POST` | `/memory/facts/query` | 确定性的类型化/双时态事实与任务查询 |
| `POST` | `/memory/facts/changes` | 对比权威事实状态 |
| `POST` | `/memory/review/query` | 稳定分页的 Meeting Review 候选查询 |
| `GET` | `/memory/versions` | 获取不可变事实版本历史 |
| `POST` | `/memory/resolve-conflict` | 原子化解决冲突事实版本 |
| `POST` | `/memory/batch` | 批量导入 |
| `POST` | `/memory/batch-delete` | 原子化删除最多 100 条记忆 |
| `GET` | `/memory/export` | 基于游标分页的 JSON 导出 |
| `POST` | `/memory/search` | 语义搜索 |
| `POST` | `/memory/decay` | 触发新鲜度衰减 |
| `POST` | `/memory/feedback` | 记录显式的记忆有用性反馈 |
| `GET` | `/memory/entities` | 列出知识图谱实体 |
| `POST` | `/memory/entities/batch-delete` | 一次删除最多 100 个知识图谱实体 |
| `GET` | `/memory/entities/{name}` | 获取实体详情和关系 |
| `DELETE` | `/memory/entities/{name}` | 删除实体 |
| `POST` | `/memory/entities/merge` | 合并重复实体 |

### Skills

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/skills` | 注册自定义 Skill |
| `GET` | `/skills` | 列出 Skills |
| `POST` | `/skills/match` | 测试意图匹配（调试） |
| `POST` | `/skills/invoke` | 直接调用 Skill |

### Settings 与系统

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` / `PUT` | `/settings` | 读取或更新运行时设置（仅内存） |
| `GET` | `/settings/bindings` | 列出可用的提供商 Binding |
| `GET` | `/settings/rebuild-status` | 查看向量/多模态重建状态 |
| `POST` | `/settings/rebuild-vectors` | 原子化重建兼容的原生索引；若来源资产需要持久化重新处理则关闭并报错 |
| `POST` | `/settings/rebuild-multimodal` | 回填多模态（RAGAnything）索引 |
| `POST` | `/settings/reload-config` | 从磁盘重新加载 `main.yaml` |
| `DELETE` | `/settings/account` | 清除当前调用用户的全部数据 |
| `GET` | `/health` | 完整依赖健康检查 |
| `GET` | `/health/live` | 存活探针 |
| `GET` | `/health/ready` | 就绪探针 |
| `GET` | `/health/traffic` | 流量控制器状态 |
| `GET` | `/health/index-consistency` | 向量/FTS 索引一致性 |
| `GET` | `/health/jobs` / `/health/capabilities` | 持久化任务和提供商能力状态 |
| `WS` | `/ws` | 实时进度/完成通知 |

## 开发

### 后端

```bash
cd backend

uv sync --dev
uv run python -m uvicorn src.main:app --reload         # 开发服务器
uv run python -m src.mcp                               # MCP 服务（stdio）
uv run python -m scripts.cli_agent                     # 交互式 CLI

uv run ruff check src/ tests/                          # lint
uv run ruff format --check src/ tests/                 # 格式检查
uv run pyright                                         # 类型检查

uv run python -m pytest                                # 完整后端测试
uv run python -m pytest tests/chain/                   # 运行一个目录
uv run python -m pytest tests/meetings/test_api.py::TestMeetingsEndpoint::test_upload_unsupported_format
```

测试标记（定义在 `pyproject.toml`）：`unit`、`integration`、`benchmark`、`property`、`chaos`。

测试隔离：`conftest.py` 会在导入任何应用模块前 monkey-patch `constants.DATA_DIR`，使测试使用临时数据库，而不会接触生产数据 `data/meetings.db`。不要在 `conftest.py` 的模块顶层导入应用模块。

CLI 使用参考：`backend/docs/cli.md`。

### 前端

```bash
cd frontend

npm install
npm run dev                    # 端口 8307，代理 /api → :7008
npm run build                  # 生产构建
npm run lint                   # eslint
npm run lint:fix
npm run format                 # prettier --write
npm run format:check
npm run type-check             # tsc --noEmit
npm run test                   # vitest watch
npm run test:run               # vitest 单次运行
npm run e2e                    # Playwright（全部浏览器）
npm run e2e:headed             # 有界面的 Chromium
npm run mutation               # Stryker 变异测试
```

运行单个前端测试：`npm run test:run -- src/test/App.test.tsx`，或使用 `-- -t "renders welcome"`。

### Benchmark

```bash
cd backend

uv run python -m scripts.benchmark chat --iterations 5     # 聊天管线延迟
uv run python -m scripts.benchmark ingest --iterations 3   # 摄取管线
uv run python -m scripts.benchmark micro                   # 组件微基准
uv run python -m scripts.benchmark rag-all                 # 检索 + 回答 + 快照
uv run python -m scripts.benchmark reranker-quality        # 受控重排器对比
uv run python -m scripts.benchmark all --iterations 5 \
  --process-report benchmark-results/e2e-smoke.json        # 全部必需套件
```

Benchmark 使用临时数据库和 `tests/fixtures/benchmark/` 中的合成 fixture，结果写入 `benchmark-results/`。完整说明参见 `backend/docs/benchmarking.md`。

#### 最新 Benchmark 结果

下表是唯一的当前得分摘要，来自 **2026-09-09 03:39 UTC** 完成的最新验证。机器可读结果、报告哈希、模型角色与限制记录在 [`docs/validation/latest-benchmark.json`](docs/validation/latest-benchmark.json) 中。

| 套件 | 最新评估范围 | 最新结果 | 状态/边界 |
|---|---:|---|---|
| 协议审计 | 9 个类别 | `valid=true`；`execution_ready=true` | 仅表示协议已具备执行条件 |
| 证据治理 | 8 个用例 | 权威性、标签可见性、版本围栏和时间范围准确率均为 **1.000** | 通过合成策略检查 |
| RAG 回答 | 10 个用例 × 3 次评审重复 | 忠实度 **0.997**；相关性 **1.000**；上下文精确率 **0.983**；上下文召回率 **1.000**；正确性 **0.997**；引用质量 **0.932** | 通过合成诊断；用例少于 30 个 |
| 多轮对话 | 6 个用例 × 1 次评审重复 | 忠实度 **0.992**；合适性 **1.000**；自然度 **0.988**；完整性/证据召回/会话连续性均为 **1.000** | 仅为诊断；评审只重复一次 |
| Memory 抽取 | 22 个事件 | **22/22** 正确；写入召回率/最新值准确率/证据率均为 **1.000** | 通过合成生产路径诊断 |
| 重排质量 | 8 个用例，每个 12 个候选 | MRR **1.000**；nDCG@10 **0.990** | 最新重排结果；评估 8 个，跳过 0 个 |
| RAG 检索 | 10 个用例 | 混合检索 Recall@10 **0.700**；文件 Recall@8 **1.000** | 重排器评估 0 个、跳过 10 个；一次向量超时回退到 BM25 |
| 视觉入口 | 1 张真实 UI 截图 | **5.44 秒**内生成图像说明、2,248 个 OCR 字符及语义 | 仅为冒烟测试，不代表视觉语料库表现 |
| 上传 → 就绪 → 带引用聊天 | 1 个隔离 fixture | 就绪 **0.94 秒**；TTFT **1.87 秒**；总耗时 **2.72 秒**；引用/事实/来源检查通过 | 功能冒烟测试通过 |
| 主模型 Chat | 20 次请求 | 20/20 完成；降级率 **45%**；TTFT P95 **4.17 秒**；总耗时 P95 **6.15 秒** | **SLO 门控失败** |

验证时的模型角色分别是：`z-ai/glm-5.3-flash` 用于主生成，`qwen/qwen3.8-flash` 用于独立评审，`deepseek/deepseek-v4-flash-vision-exp` 用于 Memory 抽取和视觉任务。这些是面向本项目合成数据的诊断，不是仓库默认配置，也不是通用模型排名。OpenRouter 提供商端点没有固定到特定后端。由于主 Chat SLO 未通过、公开质量集规模较小且为合成数据、多轮对话仅进行一次评审、视觉仅使用一张截图，以及 Embedding 路径出现过间歇性超时，系统目前仍然**未达到发布就绪状态**。

## 可观测性

一条命令启动监控栈：

```bash
export GRAFANA_ADMIN_PASSWORD="$(openssl rand -hex 24)"
docker compose -f docker-compose.yaml -f docker-compose.observability.yaml up
```

- **Prometheus**（端口 9090）抓取后端 `/metrics` 端点。
- **Grafana**（端口 3001）已预配置 Prometheus + Loki 仪表盘。
- **Loki + Promtail**（端口 3100）从 `data/logs/` 采集文件，无需暴露 Docker Socket。
- **告警规则**（`monitoring/prometheus/alerts.yaml`）覆盖请求延迟、错误率、断路器触发和磁盘使用率。

## 发布

发布由 Git Tag 触发：

```bash
git tag v0.2.0
git push origin v0.2.0
```

`.github/workflows/release.yml` 会构建多架构后端/前端镜像并推送到 GHCR，执行冒烟测试和全栈 E2E、验证 Helm Chart、通过 git-cliff 生成 Changelog，并创建 GitHub Release。手动恢复 Action 只能提升已经发布的容器镜像；它要求明确的“仅二进制”确认，以及同一仓库中带 `data-recovery-approved` 标签的 GitHub Issue；它不会降级或恢复 SQLite、上传文件或 Chroma。

镜像发布还会以失败关闭方式检查：`docs/validation/release-readiness.json` 必须存在、来源于干净运行、匹配当前数据集/测试框架/实现指纹，并明确记录生产质量、人工业务审核、性能 SLO 和安全门控均已通过。

每个门控都必须通过路径和 SHA-256 引用仓库中的常规 JSON 资产。资产必须未过期、注明审核人、绑定同一组实现指纹，并包含 [`docs/validation/release-evidence-artifact.template.json`](docs/validation/release-evidence-artifact.template.json) 中所示的门控完成字段。随后工作流会在提升前校验候选镜像的精确 Digest。请从 `docs/validation/release-readiness.template.json` 开始；历史验证文件不能满足该门控。

候选镜像冒烟测试会在启用生产验证的情况下启动后端，并使用与质量工作流相同的受保护提供商配置。发布前请配置以下仓库 Actions Secrets：`QUALITY_LLM_API_KEY`、`QUALITY_LLM_BASE_URL`、`QUALITY_LLM_MODEL`、`QUALITY_EMBEDDING_API_KEY`、`QUALITY_EMBEDDING_BASE_URL` 和 `QUALITY_EMBEDDING_MODEL`。任何值缺失都会在镜像提升前终止候选任务。

## 运维与治理

- SLO：`backend/docs/operations/slo.md`
- SLA：`backend/docs/operations/sla.md`
- 备份/恢复：`backend/docs/operations/backup.md`、`backend/docs/operations/restore.md`
- 保留策略：`backend/docs/operations/retention.md`
- 数据库迁移：`backend/docs/operations/alembic.md`
- 事故处置手册：`backend/docs/operations/runbooks/`
- 架构决策（ADR）：产品级 ADR 位于 [`docs/adr/`](docs/adr/)，技术栈级 ADR 位于 [`backend/docs/adr/`](backend/docs/adr/)
- 架构图（RAG 流程、Memory 分层、系统架构）：[`docs/diagrams/`](docs/diagrams/)
- 文档中心：[`docs/README.md`](docs/README.md)
- 快速开始：[`docs/getting-started.md`](docs/getting-started.md)；API 集成：[`docs/api-quickstart.md`](docs/api-quickstart.md)
- 数据生命周期与运维：[`docs/data-lifecycle.md`](docs/data-lifecycle.md)、[`docs/operations-guide.md`](docs/operations-guide.md)
- 开发与 Benchmark：[`docs/development-guide.md`](docs/development-guide.md)
- 前端架构与测试/CI：[`frontend/docs/architecture.md`](frontend/docs/architecture.md)、[`frontend/docs/testing.md`](frontend/docs/testing.md)
- 后端子系统文档：[`backend/docs/README.md`](backend/docs/README.md)

## 许可证

[MIT](LICENSE)
