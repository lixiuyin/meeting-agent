# 数据库层（SQLite）

> 持久化结构、读写分离、**Alembic + 遗留迁移**双轨、主要表与运维操作。
>
> 代码位置：`backend/src/core/database/`（`__init__.py`、`_connection.py`、`_migrations.py`、`_migration_definitions.py`、`_migration_helpers.py`、`_migration_lock.py`、`_scopes.py`、`meetings.py`、`chat.py`、`memories.py`、`knowledge_graph.py`、`bm25.py`、`idempotency.py`、`index_state.py`）。
> 应用启动时的升级入口：`backend/src/api/lifespan.py` → `_run_alembic_upgrade()`。
> Alembic：`backend/alembic/`、`backend/alembic.ini`。详细流程见 [`operations/alembic.md`](./operations/alembic.md)。

## 1. 为什么是 SQLite

- **零运维**：开箱即用，无需外部服务
- **WAL 模式**：并发读不阻塞写
- **充足容量**：典型会议助手场景（数千会议、数十万记忆）远在 SQLite 的舒适区内
- **单文件备份**：`sqlite3 meetings.db ".backup …"` 或文件级拷贝（注意 WAL 一致性）
- **可随时升级**：业务层通过 repository 封装，未来迁到 Postgres 只需换驱动与迁移工具

## 2. 读写分离 + WAL

`core/database/_connection.py` 暴露两组 API：

```python
# 读：无锁，WAL 保证并发读不互相阻塞，也不阻塞写
with get_connection() as conn:
    cur = conn.execute("SELECT * FROM meetings WHERE id = ?", (mid,))

# 写：串行化（_write_lock），保证写不交叉
with get_write_connection() as conn:
    conn.execute("UPDATE meetings SET status = ? WHERE id = ?", ("ready", mid))
```

关键实现细节：

- **线程本地连接池**：`threading.local()` 保存 connection，避免跨线程复用
- **PRAGMA**：`journal_mode=WAL`、`foreign_keys=ON`、`busy_timeout=30000`（30 秒）
- **`_write_lock`**：256 桶 `threading.RLock` 池，按 `user_id` 哈希分桶，减少不同用户的写锁竞争；无 `user_id` 时回退到全局桶（`pool[0]`）
- **上下文管理器**：`with` 正确 commit/rollback + 释放连接回池

### 2.1 在协程里如何写

所有 DB 写调用都通过 `asyncio.to_thread(...)` 离开 event loop：

```python
await asyncio.to_thread(create_meeting, title, user_id)
```

这样既保留 SQLite 同步 API 的简单，又不阻塞 FastAPI。

## 3. 迁移机制（Alembic 与 `_migrations.py`）

### 3.1 运行时路径（与 `lifespan` 一致）

1. **`lifespan.__aenter__` 首步**：`await asyncio.to_thread(_run_alembic_upgrade)`  
   - 若已安装 Alembic 且存在 `backend/alembic.ini`：执行 `alembic upgrade head`。  
   - **基线 revision** `20260414_000001` 在 `upgrade()` 内循环调用 `_MIGRATIONS` 中的 `_apply_migration`，并把每条记录写入 `schema_version`，与旧版 `init_db()` 语义对齐。  
   - 若 Alembic 不可用或缺少 `alembic.ini`：**回退** `init_db()`（仅 `_migrations.py` 路径）。

2. **`init_db()`**（`core/database/_migrations.py`）：仍可在测试、脚本、回退场景中**单独**调用；逻辑为读取 `schema_version` 当前最大 `version`，对 `_MIGRATIONS`（定义在 `_migration_definitions.py`）中 `version > current` 的项依次执行 SQL 并插入版本行。

### 3.2 `schema_version` 表

由 `init_db()` 或 Alembic 基线创建，形如：

```sql
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    description TEXT NOT NULL,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

每条成功应用的遗留迁移对应一行（版本号 + 描述）。**不要**手动删行除非明确在做恢复演练。

### 3.3 `alembic_version` 表

由 Alembic 维护，记录当前 revision id（如 `20260414_000001`）。后续若只增加 Alembic revision，应在此表推进；与 `schema_version` 并存是预期行为。

### 3.4 遗留迁移列表（`_MIGRATIONS`，共 44 条）

权威来源：`backend/src/core/database/_migration_definitions.py`（由 `_migrations.py` 导入）。下表为与代码一致的摘要（PR 改 schema 时请同步更新本表）。

| # | 描述（与代码 `description` 一致） | 要点 |
|---|-----------------------------------|------|
| 1 | Initial schema | `meetings`、`chat_sessions`、`chat_messages`（`role`: system/human/ai）、`user_memories` |
| 2 | Add error_message column to meetings | `meetings.error_message` |
| 3 | Extend memories and sessions… | `user_memories`：`importance`、`expires_at`、`last_accessed`、`access_count`、`category`、`embedding_id`；`chat_sessions`：`last_accessed`、`access_count` |
| 4 | Add memory decay state tracking… | 表 `memory_decay_state`（`last_decay_time`） |
| 5 | Add content hash for upload idempotency | `meetings.content_hash` + 索引 |
| 6 | Add BM25 index persistence tables | `bm25_index`、`bm25_stats` |
| 7 | Add meeting_files table for multi-file support | `meeting_files` + 从旧 `meetings` 迁数据 |
| 8 | Add FTS5 virtual table for full-text search | `bm25_chunks` FTS5 + 触发器同步 `bm25_index` |
| 9 | Relax NOT NULL constraints on meetings… | `meetings` 重建，文件列可空 |
| 10 | Add session_summaries table… | `session_summaries`（含 `user_id`、`topics`、`embedding_id` 等） |
| 11 | Add FTS5 over chat_messages… | `chat_messages_fts` + 触发器 |
| 12 | Add source provenance columns to user_memories | `session_id`、`turn_index` |
| 13 | Add consolidation and float decay columns… | `superseded_by`、`relevance_score` |
| 14 | Add knowledge graph entity table | `memory_entities`（`entity_type`、实体 id 等） |
| 15 | Add knowledge graph relation table | `memory_relations`（`subject_id` / `object_id` / `predicate`） |
| 16 | Change user_memories.importance from INTEGER to REAL… | `user_memories` 表重建，`importance` 为 REAL |
| 17 | Add idempotency_keys table for API idempotency | `idempotency_keys` |
| 18 | Add segments_json column and speaker_mappings table | `meeting_files.segments_json`、`speaker_mappings` |
| 19 | Add sources_json column to chat_messages for source provenance | `chat_messages.sources_json` |
| 20 | Add body hash to idempotency keys | `body_hash` + 复合索引 |
| 21 | Add per-meeting content hash uniqueness for files | 去重 + `idx_meeting_files_meeting_hash_unique` |
| 22 | Add typed file artefact columns on meeting_files | `structured_json`、`summary`、`duration_seconds` 等 |
| 23 | Add pending_vector_deletions… | 孤儿向量删除队列 |
| 24 | Add metrics_json to meeting_files artefacts | `metrics_json` |
| 25 | Add RAGAnything doc tracking columns… | `raganything_doc_id`、`raganything_indexed_at` |
| 26 | Add index_state table for cross-index consistency | `index_state`（Chroma / RAGAnything 一致性） |
| 27 | Add processing_started_at timestamps… | `meetings` / `meeting_files` 的 `processing_started_at` + 索引（卡死恢复用） |
| 28 | Add scope columns to user_memories and memory_entities | `meeting_ids` / `file_ids` scope 列 |
| 29 | Flag pre-scope memories/entities as legacy… | 标记 scope 迁移前的遗留数据 |
| 30 | Add conversational anchor columns to chat_sessions | 对话锚点列 |
| 31 | Add aliases column to memory_entities | 实体别名（canonical-name merging） |
| 32 | Migrate scope IDs from CSV columns to memory_scopes / entity_scopes junction tables | `memory_scopes`、`entity_scopes` 关联表 |
| 33 | Add file-level summary FTS5 index for hybrid routing | 文件摘要 FTS5 索引 |
| 34 | Add summary_status column to meetings table | `meetings.summary_status` |
| 35 | Add meeting_summaries table | `meeting_summaries`（会议级摘要） |
| 36 | Add summary_status column to meeting_files table | `meeting_files.summary_status` |
| 37 | Relax meetings.summary_status CHECK | 增加 `generating` + `lock_owner` |
| 38 | Relax meeting_files.status CHECK | 增加 `summarizing` |
| 39 | Relax meeting_files.summary_status CHECK | 增加 `generating` |
| 40 | Relax meetings.status CHECK | 增加 `summarizing` |
| 41 | Add user_id columns to meetings and meeting_files | 多租户数据隔离 |
| 42 | Add memory_audit_log table | 记忆生命周期审计日志 |
| 43 | Add vector_state column to user_memories | 向量同步状态追踪 |
| 44 | Add attempts column to pending_vector_deletions for retry tracking | `pending_vector_deletions.attempts`（重试次数追踪） |

### 3.5 不可回退与回滚策略

- **遗留迁移**：设计为 forward-only；生产回退需备份还原或手写逆向 SQL。  
- **Alembic**：`downgrade()` 在基线 revision 中为破坏性删表（仅适合 dev/test）；生产以**前进迁移**为主。

### 3.6 与 [`operations/alembic.md`](./operations/alembic.md) 的关系

- 已有数据库首次接入 Alembic：需 `alembic stamp` 对齐基线，见运维文档。  
- 新 schema 变更：优先新增 **Alembic revision**；若仍向 `_MIGRATIONS` 追加版本，须与团队约定避免双写冲突。
- **`schema_version` 表已锁定为 v44（legacy baseline）**：所有新增 schema 改动一律走 Alembic；`_MIGRATIONS` 中的 44 条遗留迁移为只读历史，不再追加新版本。

## 4. 主要表（逻辑模型）

以下与**当前迁移累积结果**一致；若与本地 `sqlite3 .schema` 有出入，以 `_migrations.py` 为准。

### 4.1 `meetings`

父会议聚合行：标题、描述、`meeting_date`、`status`（含 `uploading` / `processing` / `ready` / `failed` / `error` 等生命周期）、`error_message`、`content_hash`（遗留）、`processing_started_at`（v27）、时间戳。多文件场景下具体文件在 `meeting_files`。

### 4.2 `meeting_files`

每个上传文件一行：`meeting_id`、`file_type`、`file_name`、`file_path`、`content_hash`、`status`（业务上常见 `processing` → `ready` / `error`；重试逻辑亦涉及终端态）、`transcript`、结构化列（`structured_json` / `structured_kind`、`segments_json` 等）、`summary`、`duration_seconds`、`page_count`、`word_count`、`language`、`metrics_json`、RAGAnything 与 `index_state` 联动字段、`processing_started_at`、`error_message`、时间戳。  
**唯一约束**：同一 `meeting_id` 下相同 `content_hash` 去重（见 v21）。

### 4.3 `chat_sessions` / `chat_messages`

会话与消息；`chat_messages.role` 存库约束历史为 `system` / `human` / `ai`（与 API 展示层命名可能不同）。`sources_json`（v19）承载引用溯源。`chat_messages_fts`（v11）提供跨会话 FTS5。

### 4.4 `user_memories` / `memory_decay_state`

记忆 CRUD、衰减与合并；`importance` 为 REAL；`expires_at`、`superseded_by`、`relevance_score`、scope 列（`meeting_ids`/`file_ids`，v28）等见 v12–v16、v28。`vector_state`（v43）追踪向量同步状态。
`memory_decay_state` 记录每用户上次衰减时间（列名 `last_decay_time`，应用代码可能有别名映射，以仓储 SQL 为准）。

### 4.5 知识图谱：`memory_entities` / `memory_relations`

实体按 `(user_id, name, entity_type)` 唯一；`aliases` 列（v30）支持 canonical-name 合并。关系表以 **`subject_id` / `object_id` 外键** 指向实体 id，`predicate` 表示关系类型（非早期文档中的「名称对名称」手写行）。

### 4.6 BM25 / FTS5

- **`bm25_index` + `bm25_stats`**：手搓 BM25 倒排与全局统计。
- **`bm25_chunks`**：FTS5 虚拟表，`content='bm25_index'`，触发器同步。
- **`chat_messages_fts`**：聊天内容 FTS5。
- **`file_summary_bm25` + `file_summary_fts`**：文件摘要的 BM25 与 FTS5 索引（v33）。

混合检索见 [`rag.md`](./rag.md)。

### 4.7 `session_summaries`

按会话维度缓存摘要、主题、实体、轮数、`embedding_id` 等，供跨会话检索与摘要向量用（结构以 v10 及后续代码查询列为权威）。

### 4.8 其它运维相关表

- **`idempotency_keys`**：HTTP 幂等键与加密响应缓存元数据。
- **`pending_vector_deletions`**：待删除向量 id 队列，`attempts` 列（v44）追踪重试次数，超过阈值则放弃并记录警告。
- **`index_state`**：文件级索引时间戳与错误，用于多索引对账。
- **`meeting_summaries`**：会议级摘要（v33），与 `meetings.summary_status` / `meeting_files.summary_status` 联动。
- **`memory_audit_log`**：记忆生命周期审计日志（v42）。
- **`speaker_mappings`**：发言人与发言者映射（v18）。
- **`memory_scopes`**：记忆-会议/文件作用域关联表（v32）。
- **`entity_scopes`**：实体-会议/文件作用域关联表（v32）。

## 5. Repository 层

| 文件 | 负责 |
|---|---|
| `meetings.py` | meetings / meeting_files CRUD、状态、搜索 |
| `chat.py` | sessions、messages、FTS、summaries |
| `memories.py` | user_memories、decay、consolidation 支撑 |
| `knowledge_graph.py` | entities / relations |
| `bm25.py` | BM25 维护与查询 |
| `_connection.py` | 连接池、锁、PRAGMA |
| `_migrations.py` | `init_db()`、`_apply_migration()`（内部），从 `_migration_definitions.py` 导入 `_MIGRATIONS` |
| `_migration_definitions.py` | `_MIGRATIONS` 迁移列表（44 条）、`SCHEMA_SQL` |
| `_migration_helpers.py` | 迁移辅助函数（列检查、SQL 拆分等） |
| `_migration_lock.py` | 迁移锁（防止并发迁移） |
| `_scopes.py` | 记忆/实体作用域（meeting_ids / file_ids）辅助查询 |
| `idempotency.py` | 幂等键存储与加密响应缓存 |
| `index_state.py` | 多索引（Chroma / RAGAnything）一致性对账 |

所有 public DB 函数均为**同步**；路由/服务层用 `asyncio.to_thread` 包装。

## 6. 并发语义与事务

- **分桶写入锁**：`_write_lock_pool`（256 桶 `threading.RLock`）按 `user_id` 哈希分桶，减少不同用户间的写锁竞争  
- **读并发**：WAL 下多读不阻塞  
- **事务边界**：通常每个 repository 函数一个事务  
- **跨表原子**：同一 `get_write_connection()` 块内执行

## 7. 典型性能优化

- 热点列索引：`meetings(status)`、`meeting_files(meeting_id)`、`processing_started_at` 等  
- FTS5 替代 `%LIKE%` 大表扫描  
- 批量写：`executemany`  
- 避免超大 `IN` 列表（SQLite 变量上限约 999/32766 视版本而定，大批量应分片）

## 8. 常见维护操作

```bash
cd backend

# 与 uvicorn 启动等价的 schema 升级（Alembic）
uv run alembic upgrade head

# 不经过 Alembic、仅应用遗留迁移队列（测试/排障）
uv run python -c "from src.core.database import init_db; init_db()"

# FTS5 重建（损坏或不同步时）
uv run python -c "
from src.core.database._connection import get_write_connection
with get_write_connection() as c:
    c.execute(\"INSERT INTO chat_messages_fts(chat_messages_fts) VALUES('rebuild')\")
"

# VACUUM
uv run python -c "
from src.core.database._connection import get_write_connection
with get_write_connection() as c:
    c.execute('VACUUM')
"

# 热备到另一文件
sqlite3 data/meetings.db ".backup data/meetings.bak.db"
```

## 9. 陷阱

1. **写路径**：忘记 `get_write_connection()` → 锁竞争与 `database is locked`  
2. **外键**：依赖 `PRAGMA foreign_keys=ON`（`init_db` / 应用启动后会检查）  
3. **改历史迁移**：禁止改写已合并的 `_MIGRATIONS` 元组内容；应追加新版本或新 Alembic revision  
4. **文档与 DDL**：接口层 Pydantic 字段名可能与 SQLite 列名不完全一致，以 `*_migrations.py` 与 `core/database/*.py` 的 SQL 为准  
5. **时间**：业务与日志约定 UTC（`datetime.now(timezone.utc)`）
