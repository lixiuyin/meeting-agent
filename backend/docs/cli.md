# CLI 使用指南

> Meeting Agent 提供了一个终端交互式前端，适合本地运维、快速排障和无浏览器场景。
>
> 代码位置：`backend/scripts/cli_agent.py`。

## 1. 启动

```bash
cd backend
uv run python -m scripts.cli_agent
```

进入后输入 `/help` 查看命令。

---

## 2. 命令速查

### 2.1 会议与文件

- `/meetings [--limit n] [--offset n]`：会议列表（分页）
- `/meeting <meeting_id>`：会议详情
- `/files <meeting_id> [--limit n] [--offset n]`：会议文件列表（分页）
- `/upload <path> [--meeting id] [--title t] [--description d] [--wait]`：上传本地文件
- `/reprocess <meeting_id> [--wait]`：重处理会议下所有文件
- `/transcript <meeting_id> [--file file_id]`：查看 transcript
- `/summary <meeting_id>`：生成会议摘要
- `/export <meeting_id> --format markdown|json|txt [--output path]`：导出会议

### 2.2 检索与问答

- `/search [query]`：基于 MCP 工具的搜索
- `/retrieve <query> [--meeting 1,2] [--top-k n]`：仅检索，不走 LLM 生成
- `/chat_stream <question> [--meeting 1,2] [--top-k n] [--web]`：流式回答
- 直接输入普通文本：走标准 `ask()` 问答

### 2.3 会话

- `/sessions [--limit n] [--offset n]`：会话列表（分页）
- `/session <session_id>`：查看会话消息
- `/session_use <session_id>`：将当前问答绑定到指定会话
- `/session_delete <session_id>`：删除会话

### 2.4 Settings

- `/settings get [dotted.path]`：读取设置（全量或单项）
- `/settings set <dotted.path> <value>`：更新设置
- `/settings keys [prefix]`：列出可设置 key
- `/settings bindings`：查看可用 provider 绑定
- `/settings reload`：从 `config/main.yaml` 重新加载
- `/settings rebuild`：触发向量重建（后台）
- `/settings wizard <section>`：交互式向导（`llm|embedding|rag|memory|search|upload`）

兼容别名：

- `/settings_get`
- `/settings_set`

### 2.5 诊断与辅助

- `/status`：DB/向量库/LLM/Embedding/MCP 工具诊断
- `/skills`、`/skill_match`、`/skill_invoke`：技能系统调试
- `/memory`：记忆 CRUD 交互模式
- `/clear`：清理当前 CLI 会话上下文
- `/quit`：退出

---

## 3. 典型操作流

### 3.1 上传并等待处理

```bash
/upload "~/Downloads/spec.pdf" --title "Spec Review" --wait
/meeting 12
/files 12
```

### 3.2 检索与流式问答

```bash
/retrieve "action items" --meeting 12 --top-k 8
/chat_stream "总结这次评审的风险项" --meeting 12 --top-k 6
```

### 3.3 导出到本地文件

```bash
/export 12 --format json --output ./exports/meeting-12.json
```

---

## 4. 交互式参数引导

多数命令缺参数时会进入提示输入，而不是直接失败。例如：

- 输入 `/meeting` 会提示输入 Meeting ID
- 输入 `/settings set` 会提示输入 key/value
- 输入 `/upload` 会提示输入文件路径与是否等待处理

---

## 5. 注意事项

- CLI 与 HTTP API 共享同一数据库和向量库；CLI 的上传/删除/重处理会影响网页端可见数据。
- `/settings set` 是**运行时内存更新**，进程重启后会回到配置文件/环境变量值。
- 导出默认写入当前工作目录下的 `exports/`；可通过 `--output` 覆盖。

---

## 6. 测试覆盖

CLI 相关自动化测试位于：

- `backend/tests/test_cli_agent_parser.py`（命令解析）
- `backend/tests/test_cli_agent_e2e.py`（子进程 e2e，含交互与导出）

