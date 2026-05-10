# MCP Server

> Meeting Agent 通过 **Model Context Protocol (MCP)** 对外暴露一组工具，供外部 agent / IDE / Claude Desktop 等调用。
>
> 代码位置：`backend/src/mcp.py`。

## 1. 概念与协议

MCP 是 Anthropic 提出的**模型-工具交互标准**：

- **Server** 声明一组 `tools`（函数签名 + schema）
- **Client**（Claude Desktop、Claude Code、自研 agent）通过 **stdio** / **SSE** 传输连接
- Client 让 LLM 挑选工具、填参数、收结果

Meeting Agent MCP Server 使用 `fastmcp` 库（FastAPI 风格装饰器）实现。

## 2. 运行方式

```bash
# 从 backend/ 目录
uv run python -m src.mcp
```

- **传输**：stdio（默认）或 HTTP/SSE（需设置 `MCP_HTTP_PORT` / `MCP_HOST` / `FASTMCP_HOST` / `FASTMCP_PORT` 环境变量）
- **依赖**：与 HTTP API 共享同一份 `src/` 代码库、同一个 SQLite、同一个 Chroma collection
- **独立进程**：与 `uvicorn src.main:app` 并行运行

### 2.1 Claude Desktop 集成

`~/Library/Application Support/Claude/claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "meeting-agent": {
      "command": "uv",
      "args": ["run", "python", "-m", "src.mcp"],
      "cwd": "/absolute/path/to/meeting-agent/backend",
      "env": {
        "LLM_API_KEY": "sk-...",
        "EMBEDDING_API_KEY": "sk-..."
      }
    }
  }
}
```

重启 Claude Desktop 后即可在对话中看到工具。

### 2.2 HTTP/SSE 传输

设置以下任一环境变量即可启用 HTTP/SSE 传输：

```bash
MCP_HTTP_PORT=9000   # 或 MCP_HOST / FASTMCP_HOST / FASTMCP_PORT
```

**安全要求**：当检测到 HTTP 传输环境变量时，MCP Server **强制要求** `API_KEY` 已配置（否则启动时报 `RuntimeError`）。这是 fail-closed 策略——stdio 传输信任宿主进程，HTTP 传输暴露到网络后必须鉴权。

### 2.3 Claude Code 集成

```bash
claude mcp add meeting-agent -- uv run python -m src.mcp
```

## 3. 已注册工具

### 3.1 `list_meetings`

```python
@mcp.tool()
def list_meetings(status: str | None = None, limit: int = 20) -> str:
    """List all uploaded meetings with their processing status."""
```

同步函数。返回格式化文本，每行一条记录：
```
[1] Meeting Title | video | ready | 2025-01-01T00:00:00Z
[2] Another Meeting | pdf | processing | 2025-01-02T00:00:00Z
```
无结果时返回 `"No meetings found."`。

### 3.2 `search_meetings`

```python
@mcp.tool()
def search_meetings(query: str, meeting_ids: list[int] | None = None, top_k: int = 5) -> str:
    """Search meeting content by semantic similarity."""
```

同步函数。调用完整语义检索管线（`services.rag.retrieve`），支持通过 `meeting_ids` 限定范围。返回格式化文本，每条结果包含分数、来源标题和内容摘要。

### 3.3 `ask_about_meetings`

```python
@mcp.tool()
async def ask_about_meetings(
    question: str,
    session_id: str | None = None,
    user_id: str = "default",
    meeting_ids: list[int] | None = None,
) -> str:
    """Ask a question about meeting content. Uses RAG with conversation memory."""
```

内部调用与 `POST /chat` 一致的 `ask()`。`session_id` 支持多轮对话。返回 JSON 字符串：
```json
{
  "answer": "...",
  "session_id": "...",
  "sources": ["Meeting Title 1", "Meeting Title 2"]
}
```
`sources` 仅包含会议标题（不含完整元数据）。`user_id` 参数始终被忽略，使用 `_MCP_USER_ID = "default"`。

### 3.4 `manage_memory`

```python
@mcp.tool()
def manage_memory(
    action: str,                   # "set" | "get" | "list" | "delete"
    key: str | None = None,
    value: str | None = None,
    user_id: str = "default",
    page: int = 1,
    page_size: int = 50,
) -> str:
    """Manage long-term user memory (preferences, key facts)."""
```

同步函数。桥接 `MemoryService` 的 CRUD。`user_id` 参数始终被忽略，使用 `_MCP_USER_ID = "default"`。`page` / `page_size` 用于 `list` 操作的分页。

### 3.5 `list_skills`

```python
@mcp.tool()
def list_skills() -> str:
    """List all available skills with their descriptions."""
```

同步函数。返回 Markdown 格式的 Skill 列表。

### 3.6 `invoke_skill`

```python
@mcp.tool()
async def invoke_skill(
    skill_name: str,
    query: str,
    user_id: str = "default",
    meeting_ids: list[int] | None = None,
) -> str:
    """Manually invoke a specific skill by name."""
```

内部构造 `PipelineContext`，调用 `_run_pipeline(ctx, skill.model_dump())`，复用 HTTP 路径的 chain pipeline。返回 JSON 字符串，包含 `skill`、`output`、`sources`。`user_id` 参数始终被忽略，使用 `_MCP_USER_ID = "default"`。

## 4. 启动行为

MCP Server 的 `__main__` 入口只执行两步：

```python
if __name__ == "__main__":
    init_db()
    mcp.run()
```

- `init_db()` — 根据 `schema_version` 应用 `core/database/_migrations.py` 中**尚未执行**的遗留迁移（与 uvicorn 侧 Alembic 基线写入的 `schema_version` 兼容；MCP 入口**不**调用 Alembic，仅 `init_db`）
- `mcp.run()` — 启动 stdio 传输

LLM、Embeddings、VectorStore 等重型单例**不在启动时预初始化**，而是在各工具函数内部按需延迟加载（首次调用时初始化）。这意味着 **HTTP API 与 MCP Server 跑在同机器时共享同一个 SQLite 和 Chroma 目录**，可以一边上传文件、一边在 Claude Desktop 里直接问该文件。

## 5. 与 HTTP API 的差异

| 能力 | HTTP API | MCP Server |
|---|---|---|
| 上传文件 | ✅ | ❌（MCP 不处理二进制） |
| 列表 / 搜索 | ✅ | ✅ |
| RAG 问答 | ✅ | ✅ |
| 流式响应 | ✅（SSE） | ❌（stdio 返回一次） |
| WebSocket 进度 | ✅ | ❌ |
| 记忆管理 | ✅ | ✅（部分） |
| Skills | ✅ | ✅ |
| 认证 | `X-API-Key` | stdio: 无（进程级隔离）；HTTP/SSE: 必须 `API_KEY` |

## 6. 安全考量

- MCP Server 默认通过 **stdio** 与 client 通信，**没有网络暴露面**
- 启用 HTTP/SSE 传输时，**必须配置 `API_KEY`**（启动时检测，否则拒绝启动）
- 运行 MCP Server 的用户能做的操作 = 能读写的 DB/Chroma/文件系统
- **不要**把 MCP Server 暴露到公网 SSE transport，除非前面加了鉴权代理
- 敏感 secret 通过 env / `.env` 传入，**不**写进 MCP config JSON 之外的明文

## 7. 调试

### 7.1 手动发送 request

MCP stdio 协议是换行分隔的 JSON-RPC：

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | uv run python -m src.mcp
```

### 7.2 用 `mcp-inspector`

Anthropic 官方的 `mcp-inspector` 可用于交互式调试：

```bash
npx @modelcontextprotocol/inspector uv run python -m src.mcp
```

### 7.3 日志

MCP Server 使用与 HTTP API 相同的 logging 配置（读 `LOG_FORMAT` env var）。`stderr` 用于日志，`stdout` 专用于协议传输 — **切勿 `print()` 到 stdout**。

## 8. 扩展：添加新工具

1. 在 `src/mcp.py` 编写函数
2. 用 `@mcp.tool()` 装饰
3. 写清楚 **docstring**（MCP client 用它生成 schema 描述）
4. 参数优先用基础类型（str / int / list[int] / dict / Optional）
5. 返回 JSON 可序列化的对象
6. 添加单元测试：`tests/test_mcp_server.py`（模拟 `mcp.tool` 调用）

```python
@mcp.tool()
async def get_meeting_summary(meeting_id: int) -> dict:
    """Get LLM-generated summary for a specific meeting.

    Args:
        meeting_id: ID of the meeting to summarize.

    Returns:
        {"meeting_id": int, "summary": str, "generated_at": str}
    """
    summary = await meeting_service.get_or_generate_summary(meeting_id)
    return {
        "meeting_id": meeting_id,
        "summary": summary.text,
        "generated_at": summary.generated_at.isoformat(),
    }
```

## 9. 常见问题

| 问题 | 处理 |
|---|---|
| Claude Desktop 看不到工具 | 检查 `claude_desktop_config.json` 路径 + 重启 Desktop |
| 启动报 `DB locked` | HTTP API 并发写了同一张表；重试或降低 MCP Server 的并发 |
| LLM_API_KEY 没有生效 | 检查 `env` 字段是否放在 MCP config 里（Desktop 不会继承 shell env） |
| stdout 出现非 JSON 文本 | 某处误用 `print()` → 改 `logger.info(...)` |
| 返回 `Unknown LLM binding` | Desktop 加载时没读到 `.env` → 在 config `env` 里显式声明 |
