# 应用生命周期与运行时运维

> 覆盖 FastAPI 启动 / 关闭流程、数据库升级、后台任务、故障恢复与日常运维要点。
>
> 代码位置：`backend/src/api/lifespan.py`、`backend/src/api/middleware.py`、`backend/src/services/processor/_recovery.py`。

## 1. 设计原则：关键路径 vs 尽力路径

`lifespan.py` 把启动拆成两段：

- **Critical path**（数据库升级完成后 `await _run_critical_startup()`）：任一步致命失败可中止启动  
  - OpenTelemetry `setup_tracing()`（无 endpoint 时为 no-op）  
  - 可选 Sentry（`SENTRY_DSN`）  
  - **安全守卫**：`ENVIRONMENT != "dev"` 且未配置 `API_KEY` → `RuntimeError`  
  - `get_llm()`（`asyncio.to_thread`，30s 超时；超时不终止进程，但首聊可能失败）  
  - `init_traffic_controller()`  
  - `get_embeddings()` + `embed_query("connectivity check")` 验证非空  
  - `get_vectorstore()`；best-effort 预热 memory / session summary / entity 向量库  
  - best-effort 预热 **Skill matcher** embedding；若 `RAGANYTHING_ENABLED` 则校验包并预热 RAGAnything  

- **Best-effort path**（`recover_stale_meetings` 起）：失败记指标 + 日志，进程继续服务  
  - `recover_stale_meetings()`  
  - 会话缓存、过期记忆、orphan 向量、启动期 decay  
  - **`memory_decay_loop`**、**每小时过期记忆 purge**（监督任务）  
  - `backfill_chat_messages_fts`；memory/entity **sync_missing_vectors**；session summary 启动回填 / 空闲循环（受配置开关控制）  
  - `rebuild_bm25_from_chroma` + `check_and_rebuild_bm25_if_drifted`  
  - **WAL 每小时 checkpoint**、**多模态 index reconcile 每 10 分钟**、**每日 retention purge**（`create_supervised_task`）

好处：**可观测 + 可选子系统不拖死核心启动**。

## 2. 数据库升级（启动第一步）

`lifespan.__aenter__` **最先**执行：

```text
await asyncio.to_thread(_run_alembic_upgrade)
```

行为摘要（`lifespan._run_alembic_upgrade`）：

- 能导入 Alembic 且存在 `backend/alembic.ini` → `command.upgrade(alembic_cfg, "head")`。  
- 否则 → 调用 `init_db()`（纯 `_migrations.py` 路径）。

因此文档与排障应默认 **「生产启动 = Alembic head」**，而不是仅 `init_db()`。

## 3. 安全守卫（关键路径内）

`_run_critical_startup()` 在初始化 LLM 之前：

```python
if settings.ENVIRONMENT != "dev" and not settings.API_KEY.get_secret_value():
    raise RuntimeError(
        f"API_KEY must be set when ENVIRONMENT={settings.ENVIRONMENT!r}. "
        "Set API_KEY in your .env file or environment variables."
    )
```

**`ENVIRONMENT` 非 `dev` 时必须配置 `API_KEY`**。dev 判定以 `settings.ENVIRONMENT == "dev"` 为准。

### 3.1 Sentry 敏感信息过滤

当 `SENTRY_DSN` 配置后，`lifespan.py` 会注册 `before_send` 回调，对 Sentry 事件做敏感信息脱敏：

- 移除 request headers 中的 `X-API-Key`、`Authorization` 等
- 清理 user context 中的敏感字段
- 确保 error message 不包含 API key、数据库路径等内部细节

## 4. 启动时序（简化视图）

```
uvicorn → lifespan.__aenter__
   │
   ├── [critical] await asyncio.to_thread(_run_alembic_upgrade)
   │   - Alembic upgrade head（或回退 init_db）
   │
   ├── [critical] await _run_critical_startup()
   │   - OTEL + Sentry（可选）
   │   - API_KEY 守卫（非 dev）
   │   - LLM / traffic / reranker config 校验 / embeddings ping / vectorstore + 预热
   │   - memory / session / entity / file-summary vectorstore 预热
   │   - orphan memory vector 清理
   │   - RAGAnything 校验 + 预热（条件执行）
   │
   ├── [best-effort] recover_stale_meetings
   │   - 仅处理卡死超过 **5 分钟**  grace 的记录（避免与进行中的任务竞态）
   │   - 条件：COALESCE(processing_started_at, updated_at) < now - 5 minutes
   │   - meeting_files → status='error', error_message='Processing interrupted'
   │   - meetings → status='failed'
   │
   ├── [best-effort] stale_recovery_loop（每 15 分钟）
   ├── [best-effort] bm25_drift_loop（每 6 小时）
   ├── [best-effort] recover stale generating summaries + file summary requeue
   ├── [best-effort] meeting summary reconcile + rebuild swap reconcile
   ├── [best-effort] session_cache / expired_memories / pending_vector_cleanup / startup_decay
   ├── [best-effort] memory_decay_loop + hourly expired purge
   ├── [best-effort] FTS5 backfill、memory + KG + file-summary vector sync、index-state reconcile
   ├── [best-effort] session summary startup backfill + idle summary loop
   ├── [best-effort] BM25 rebuild + drift check + legacy metadata backfill
   ├── [best-effort] WAL checkpoint / index reconcile / retention / idempotency cleanup 循环
   ├── [best-effort] skill matcher embedding 预热
   └── （完整顺序以 lifespan.py 为准）
```

## 5. 关闭时序（Graceful shutdown）

`lifespan.__aexit__` 大致顺序：

1. `_bg.cancel_all()` — 取消所有 `create_supervised_task` 注册的后台任务，等待 5s
2. 持久化会话缓存（`_persist_session_cache`）
3. `stop_memory_decay_loop`
4. `cancel_background_tasks()` — `services/chain` 登记的 fact-extraction 等
5. `persist_vectorstore()` — Chroma 持久化
6. 关闭 search / ASR / vision 共享 `httpx` 客户端
7. 关闭 **reranker** HTTP 客户端（`_close_reranker_http_client`）
8. 关闭 **parser** 云 API 客户端（`close_parser_http_client`）
9. 对其余 `asyncio` 任务 `cancel` + `asyncio.wait`（超时 5s）
10. `close_all_connections()` — SQLite
11. 关闭专用 executor（`_PARSER_LOOP_EXECUTOR`、`_VECTOR_SEARCH_EXECUTOR`）

### 5.1 后台任务两类托管

- **长跑监督任务**：`create_supervised_task`（`utils/supervised_task.py`）。  
- **链路上短时任务**：`services/chain._background_tasks`。  

**不要**假设存在单一的 `app.state.background_tasks` 集合。

## 6. 故障恢复（Stale Meetings）

**现象**：进程被 kill / OOM 后，会议或文件长时间停在 `processing`。

**机制**（`processor/_recovery.py`）：

- 仅当 `status='processing'` 且 `COALESCE(processing_started_at, updated_at) < datetime('now', '-5 minutes')` 才重置。  
- 文件行 → `error` + 固定 `error_message`；会议行 → `failed`；并清空 `processing_started_at`。

用户可 **`POST /meetings/{id}/reprocess`** 或单文件 reprocess；必要时 `delete_meeting_chunks(meeting_id, file_id=...)` 清理向量后再试。

## 7. 中间件与请求处理链

```
CORS（可选）
  ↓
RequestIdMiddleware        # X-Request-ID + X-Response-Time
  ↓
slowapi rate limiter       # 默认限额 + 多路由覆盖（见 api-reference.md）
  ↓
JSON structured logging    # LOG_FORMAT=json
  ↓
FastAPI router
```

中间件在 `setup_middleware()` 中装配，**不在**模块 import 时执行，以免干扰 pytest monkey-patch 顺序。

## 8. 后台任务清单（摘录）

| 任务 | 说明 | 位置（示例） |
|---|---|---|
| `memory_decay_loop` | 周期衰减 | `services/memory/_service/_decay_sync.py` |
| `expired_memory_purge_loop` | 每小时过期记忆清理 | `api/lifespan.py`（内联 loop） |
| `stale_recovery_loop` | 每 15 分钟恢复卡死会议 | `api/lifespan.py`（内联 loop） |
| `bm25_drift_loop` | 每 6 小时 BM25 漂移检测 | `api/lifespan.py`（内联 loop） |
| `wal_checkpoint_loop` | 每小时 WAL checkpoint | `api/lifespan.py`（内联 loop） |
| `index_reconcile_loop` | 每 10 分钟多模态索引一致性 + BM25 漂移检测 | `api/lifespan.py`（内联 loop） |
| `retention_purge_loop` | 每日数据保留清理 | `api/lifespan.py`（内联 loop） |
| `idempotency_cleanup_loop` | 每小时幂等键清理 | `api/lifespan.py`（内联 loop） |
| `idle_session_summary_loop` | 空闲会话定期摘要 | `api/lifespan.py`（内联 loop） |
| `skill_matcher_prewarm` | 启动时预热 Skill matcher embedding | `api/lifespan.py`（内联 task） |
| 上传处理 | 每次上传 BackgroundTasks | `processor/_pipeline.py` |
| 事实提取 | 对话后异步 | `chain/_steps_generate.py` |
| 会话摘要 | 启动回填 | `memory/_summary_service.py` |
| Chroma 持久化 | 关闭时 flush | `rag/_vectorstore.py` |

## 9. 运维常用命令

```bash
cd backend

uv sync --dev
uv run uvicorn src.main:app --reload

# 仅数据库：与启动等价的 Alembic
uv run alembic upgrade head

# 无 Alembic 时的遗留迁移
uv run python -c "from src.core.database import init_db; init_db()"

uv run python -m src.mcp
uv run python -m pytest -x
```

## 10. 排查指引

| 现象 | 可能原因 | 处理 |
|---|---|---|
| `API_KEY must be set` | 非 dev 未配 key | 配置 `API_KEY` |
| 阻塞在 embedding ping | 上游不可达 | 检查 `EMBEDDING_*` 与网络 |
| Alembic / `init_db` 失败 | 迁移冲突、库损坏 | 备份 DB，检查 `schema_version` / `alembic_version` |
| 会议长期 `processing` | 任务仍在跑或未过 grace | 等 5min+ 或重启触发 recover |
| 关闭时 Task destroyed 警告 | 未纳入上述两类托管 | 使用 `create_supervised_task` 或 chain 登记 |

## 11. 可观测性触点

- **日志**：`LOG_FORMAT=json`  
- **Trace**：`core/trace.py` span；摄入管线会 `logger.info("ingest_trace %s", json.dumps(trace.to_dict()))` — **非**独立 `trace` 表（除非另行扩展）  
- **Metrics**：`GET /metrics`  
- **Health**：`GET /api/v1/health`  
- **WS**：`WS /api/v1/ws` 进度与完成事件  
