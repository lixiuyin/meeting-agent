# REST API 参考

> 所有端点挂载在 `/api/v1/` 前缀下，均需 `X-API-Key` Header（空 `API_KEY` 视为 dev 模式跳过校验）。
>
> 代码位置：`backend/src/api/routers/`，响应 schema：`backend/src/models/schemas/`。

## 1. 约定

- **Base URL**：`http://<host>:<port>/api/v1`
- **认证**：`X-API-Key: <your-key>`（见 [`configuration.md`](./configuration.md)）
- **限流**：`slowapi` 默认 **60/min**（`middleware.py` 的 `Limiter`）；显式覆盖：`POST /meetings/upload` 20/min，`POST /chat` 与 `/chat/stream` 20/min，`POST /settings/*` 与 `PUT /settings` 与 `DELETE /settings/account` 5/min，`DELETE /meetings/{id}` 10/min，`PUT /meetings/{id}/files/{fid}/speakers` 3/min，`DELETE /memory` 与 `PUT /memory` 与 `DELETE /memory/entities/{name}` 10/min，`POST /memory/decay` 1/hour，`DELETE /sessions/{id}` 10/min，`GET /meetings/{id}/files/{fid}` 60/min，`GET /meetings/search/content` 60/min，`GET /meetings/assets` 30/min；`/health/*` 豁免。开启 `API_KEY` 时按 **API Key 哈希**分桶，否则按 **客户端 IP**（可信代理下取 `X-Forwarded-For` 首个地址）。可用 `DISABLE_RATE_LIMIT=1` 仅在 dev/test 关闭。
- **响应格式**：所有成功响应都是具名 Pydantic 模型，错误使用统一的 `{"detail": "..."}`
- **请求 ID**：响应头带 `X-Request-ID`、`X-Response-Time`

## 2. Meetings（会议）

路由前缀：`/meetings`，代码：`api/routers/meetings/`。

| Method | Path | 行为 |
|---|---|---|
| `POST` | `/meetings/upload` | 上传文件（新建 meeting 或追加到已有 meeting） |
| `POST` | `/meetings` | 创建空 meeting |
| `GET` | `/meetings` | 列表（可 `?status=processing` 等过滤） |
| `GET` | `/meetings/{id}` | 详情 + 文件列表 |
| `PUT` | `/meetings/{id}` | 更新元数据（title/description/date） |
| `DELETE` | `/meetings/{id}` | 删除 meeting 与所有文件/向量 |
| `GET` | `/meetings/{id}/files` | 文件列表 |
| `GET` | `/meetings/{id}/files/{fid}` | 下载文件（`X-API-Key` 或 `?token=`；路由在 `file_download.py`，先于 meetings 注册） |
| `DELETE` | `/meetings/{id}/files/{fid}` | 删除单个文件（含其向量） |
| `GET` | `/meetings/{id}/files/{fid}/timeline` | 时间线（关键帧 / 页面） |
| `GET` | `/meetings/{id}/files/{fid}/speakers` | 说话人列表 |
| `PUT` | `/meetings/{id}/files/{fid}/speakers` | 更新说话人映射 |
| `GET` | `/meetings/{id}/files/{fid}/speakers/{code}/audio` | 说话人音频片段 |
| `GET` | `/meetings/{id}/summary` | 读取预生成的摘要及其生命周期状态 |
| `POST` | `/meetings/{id}/summary` | LLM 生成摘要（长转写走 map-reduce） |
| `POST` | `/meetings/{id}/summary/stream` | SSE 流式摘要 |
| `POST` | `/meetings/{id}/reprocess` | 重新处理（按 content_hash 跳过未变） |
| `POST` | `/meetings/{id}/files/{fid}/reprocess` | 仅重处理单个文件 |
| `POST` | `/meetings/file-token` | 签发全局短期下载 token（`file_download`） |
| `POST` | `/meetings/{id}/files/{fid}/signed-url` | 签发绑定文件的签名 URL / token |
| `GET` | `/meetings/assets` | 会议资源静态文件（`path` + 可选 `token`） |
| `GET` | `/meetings/{id}/transcript` | 完整转写文本 |
| `GET` | `/meetings/{id}/transcript/timestamps` | 含时间戳的结构化转写 |
| `GET` | `/meetings/{id}/export?format=json\|markdown\|txt` | 导出 |
| `GET` | `/meetings/search/content?q=...` | FTS5 全文搜索 |

### 2.1 Upload

```http
POST /api/v1/meetings/upload
Content-Type: multipart/form-data
X-API-Key: <key>

file=<binary>
meeting_id=42          # 可选：追加到已有 meeting
title=Q1 Review        # 可选：新 meeting 的标题
```

响应：`MeetingUploadResponse`

```json
{
  "meeting_id": 42,
  "file_id": 137,
  "file_name": "Q1-review.pdf",
  "status": "pending",
  "skipped": false      // true 表示 content_hash 命中已有文件
}
```

后续通过 **WS** 或轮询 `GET /meetings/{id}` 检查状态。

### 2.2 Upload 约束

- 最大 `MAX_UPLOAD_SIZE_MB`（默认 500 MB）
- 文件名自动 sanitize（防路径穿越）
- 支持扩展名：pdf, pptx, ppt, doc, docx, xls, xlsx, png, jpg, jpeg, bmp, tiff, mp3, wav, m4a, mp4, mov, avi, webm, mkv

### 2.3 Reprocess 语义

```http
POST /api/v1/meetings/42/reprocess
```

遍历 meeting 下所有文件：
- **content_hash 未变** → 跳过
- **变化或失败** → 重跑 `process_meeting_file`

## 3. Chat

路由前缀：`/chat`，代码：`api/routers/chat.py`。

| Method | Path | 行为 |
|---|---|---|
| `POST` | `/chat` | 同步问答（RAG + memory） |
| `POST` | `/chat/stream` | SSE 流式问答 |
| `POST` | `/chat/search` | 只检索，不生成 |

### 3.1 `POST /chat`

```json
{
  "question": "What did Alice say about the Q2 roadmap?",
  "session_id": "uuid-or-null",
  "meeting_ids": [42, 43],        // 可选，仅在这些 meeting 中检索
  "top_k": 5,                     // 可选
  "use_web_search": false,
  "file_types": ["pdf"],          // 可选过滤
  "date_from": "2026-01-01T00:00:00Z",
  "date_to": null
}
```

响应：`ChatResponse`

```json
{
  "answer": "...",
  "sources": [
    {"meeting_id": 42, "file_id": 137, "file_name": "Q1-review.pdf", "chunk_index": 5, "score": 0.83, "snippet": "..."}
  ],
  "session_id": "uuid",
  "web_results": [],
  "trace": { "spans": [...] },
  "extraction_failed": false,
  "skill_used": null
}
```

### 3.2 `POST /chat/stream`

同 schema，响应是 SSE 流：

```
data: {"type":"step","name":"retrieve","phase":"start"}

data: {"type":"sources","items":[...]}

data: {"type":"token","content":"Alice "}
data: {"type":"token","content":"said "}
...

data: {"type":"done","elapsed_ms":1842}
```

事件类型见 [`chain-pipeline.md`](./chain-pipeline.md#61-事件类型streamevent)。

### 3.3 `POST /chat/search`

只跑检索 + 重排，返回文档列表，不调用 LLM。适合做召回调试。

## 4. Sessions

路由前缀：`/sessions`，代码：`api/routers/sessions.py`。

| Method | Path | 行为 |
|---|---|---|
| `GET` | `/sessions` | 列出用户会话 |
| `GET` | `/sessions/{id}/messages` | 会话全部消息 |
| `DELETE` | `/sessions/{id}` | 删除会话（及消息、摘要） |
| `POST` | `/sessions/{id}/summarize` | 生成 / 重新生成摘要 |
| `GET` | `/sessions/{id}/summary` | 读摘要 |
| `GET` | `/sessions/{id}/cite` | 引用 / 摘要上下文 |
| `GET` | `/sessions/summaries` | 所有摘要列表（分页） |
| `POST` | `/sessions/search` | 跨会话搜索（FTS5 + 摘要语义） |

## 5. Memory

路由前缀：`/memory`，代码：`api/routers/memory.py`。详见 [`memory-and-kg.md`](./memory-and-kg.md)。

| Method | Path | 行为 |
|---|---|---|
| `GET` | `/memory` | 列表 |
| `POST` | `/memory` | 创建 |
| `PUT` | `/memory` | 更新 |
| `DELETE` | `/memory` | 删除 |
| `POST` | `/memory/batch` | 批量导入 |
| `GET` | `/memory/export` | JSON 导出 |
| `POST` | `/memory/search` | 语义搜索 |
| `POST` | `/memory/decay` | 触发衰减 + 合并 |
| `GET` | `/memory/entities` | 列实体 |
| `GET` | `/memory/entities/{name}` | 实体详情 + 关系 |
| `DELETE` | `/memory/entities/{name}` | 删除实体 |
| `POST` | `/memory/entities/merge` | 合并实体 |

## 6. Settings

路由前缀：`/settings`，代码：`api/routers/settings/`（`__init__.py` + `_rebuild.py`）。

| Method | Path | 行为 |
|---|---|---|
| `GET` | `/settings` | 读当前设置（脱敏 secret） |
| `PUT` | `/settings` | 更新设置（仅内存） |
| `GET` | `/settings/bindings` | 列出所有可选 provider |
| `POST` | `/settings/rebuild-vectors` | 重建向量索引（并发守卫） |
| `POST` | `/settings/rebuild-multimodal` | 多模态索引回填（RAGAnything） |
| `POST` | `/settings/reload-config` | 从磁盘重载 YAML 配置 |
| `DELETE` | `/settings/account` | GDPR 数据擦除（删除用户全部数据） |

### 6.1 PUT 语义

- 仅修改**内存中的 `settings` 对象**，不写回 `.env`
- 按字段差异调用 `reset_*()` 重建对应单例
- 重启进程后失效

### 6.2 `rebuild-vectors`

- **并发守卫**：全局 `asyncio.Lock`，同时只允许一次重建
- 扫描所有 `meeting_files` → 清空 Chroma collection → 重新调用 `index_meeting`
- **耗时操作**，API 立即返回 202，进度通过 WebSocket 推送

## 7. Skills

路由前缀：`/skills`，代码：`api/routers/skills.py`。

| Method | Path | 行为 |
|---|---|---|
| `POST` | `/skills` | 注册自定义 skill（201） |
| `GET` | `/skills` | 列出已注册 skill（名称、描述、examples） |
| `POST` | `/skills/invoke` | 手动调用 skill（不靠 intent 匹配） |
| `POST` | `/skills/match` | 测试 intent 匹配（debug） |

### 7.1 Invoke 请求

```json
{
  "skill_name": "meeting-summary",
  "query": "Summarize last week's retro",
  "user_id": "default",
  "meeting_ids": [42]
}
```

响应：

```json
{
  "skill_name": "meeting-summary",
  "content": "# Summary\n- ...",
  "format": "markdown",
  "sources": [...],
  "execution_time_ms": 0
}
```

## 8. Health & System

| Method | Path | 行为 |
|---|---|---|
| `GET` | `/health` | DB 连通性 + 轻量检查 |
| `GET` | `/health/live` | 存活探针 |
| `GET` | `/health/ready` | 就绪探针 |
| `GET` | `/health/traffic` | 流量控制器状态 |
| `GET` | `/health/index-consistency` | 索引一致性检查 |
| `POST` | `/health/reset-memory-cb` | 重置 memory 向量熔断器（运维） |
| `GET` | `/metrics` | Prometheus 指标（**不在** `/api/v1` 下；需 API Key） |
| `WS` | `/ws` | 实时事件（progress, complete） |

### 8.1 WebSocket 事件

`WebSocketManager` 当前载荷形状（无 `file_id` / `stage` / `ratio` 字段）：

```json
{"type":"progress","meeting_id":42,"status":"processing","progress":0.6,"message":"..."}
{"type":"complete","meeting_id":42,"status":"ready","title":"Q1 Review"}
```

失败完成时 `status` 为 `failed`。处理器管线可能扩展字段；以 `services/websocket.py` 为准。

订阅方式：

```javascript
const ws = new WebSocket("ws://host:8000/api/v1/ws");
ws.onmessage = (ev) => {
  const msg = JSON.parse(ev.data);
  // ...
};
```

## 9. 错误响应

所有错误响应统一遵循 `ErrorResponse` 信封（定义在 `src/models/schemas/_common.py`），并在每个 router 通过 `register_routers` 的 `responses=` 参数声明到 OpenAPI schema 上：

```json
{
  "code": "HTTP_404",
  "message": "Meeting not found",
  "request_id": "8a1bdfec82444a21",
  "details": null,
  "detail": "Meeting not found"
}
```

字段语义：

- `code`：机器可读错误码，格式 `HTTP_<status>` 或 `INTERNAL_ERROR`。
- `message`：人类可读错误信息（生产环境已脱敏）。
- `request_id`：请求 ID，与 `X-Request-ID` 响应头 / 服务器日志中一致，用于追踪。
- `details`：附加上下文，可空。
- `detail`：兼容老客户端的旧字段，与 `message` 同值。

常见状态码（每个 router 都把以下码声明在 OpenAPI 里，schemathesis 合约测试可校验）：

| 状态 | 语义 |
|---|---|
| 400 | 请求参数错误（含 multipart 流损坏等） |
| 401 | 缺失 / 错误的 `X-API-Key` |
| 403 | 鉴权通过但权限不足 |
| 404 | 资源不存在 |
| 409 | 并发冲突（如重建向量中） |
| 413 | 文件超限 |
| 422 | Pydantic 校验失败 |
| 429 | 限流（slowapi；返回时使用同一 ErrorResponse 信封 + `Retry-After` 头） |
| 500 | 服务器内部错误（**消息已脱敏**） |

> 所有 5xx 响应**不会泄露**内部栈信息 — 详细错误只在服务器日志（含 `request_id`）。
> 所有响应中的 `datetime` 字段使用带时区的 ISO 8601（`UTCDatetime` 类型，naive 值在序列化时自动补 UTC，输出形如 `"2026-05-09T14:28:17Z"`）。

## 10. 跨文档 schema

为避免重复维护，所有响应模型集中在 `src/models/schemas/` 下按领域拆分：

- `_common.py` — `MessageResponse`、`PaginatedResponse[T]`、共享枚举
- `meetings.py`
- `chat.py`
- `memory.py`
- `sessions.py`
- `settings.py`

新增字段时务必**同步更新前端 `src/api/` 下对应 domain client** 的类型定义（或通过 `scripts/generate-types.sh` 从 OpenAPI 重新生成 `frontend/src/api/generated.d.ts`）。

## 11. 客户端示例

### cURL

```bash
# 上传
curl -X POST http://localhost:8000/api/v1/meetings/upload \
  -H "X-API-Key: $API_KEY" \
  -F "file=@Q1-review.pdf" \
  -F "title=Q1 Review"

# 问答
curl -X POST http://localhost:8000/api/v1/chat \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"question":"What is the Q1 highlight?","meeting_ids":[42]}'

# 流式
curl -N -X POST http://localhost:8000/api/v1/chat/stream \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"question":"..."}'
```

### Python（httpx）

```python
import httpx

async with httpx.AsyncClient(base_url="http://localhost:8000/api/v1",
                             headers={"X-API-Key": "..."}) as client:
    r = await client.post("/chat", json={"question": "..."})
    r.raise_for_status()
    print(r.json()["answer"])
```

### TypeScript（axios）

```typescript
import { sendChat } from "@/api/client";

const { answer, sources } = await sendChat({
  question: "What is the action item?",
  meetingIds: [42],
});
```

## 12. 版本与向后兼容

- 目前**只有 `/api/v1/`**，未做并行版本
- 字段新增视为向后兼容
- 字段移除或语义变更需要引入 `/api/v2/`
- 向后不兼容的前端改动需要同步升级 `frontend/src/api/client.ts`
