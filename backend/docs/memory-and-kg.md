# 记忆系统 & 知识图谱

> 长期记忆（事实 / 偏好 / 目标）与知识图谱（实体 + 关系）的实现细节。
>
> 代码位置：
> - `backend/src/services/memory/` — 记忆服务（提取、衰减、合并、画像、搜索、历史、会话摘要）
>   - `_entry.py` — 顶层入口（`MemoryEntry` dataclass，统一记忆条目结构）
>   - `_service/` — 核心服务层（CRUD、搜索、提取、合并、衰减同步、画像）
>   - `_summary_service.py` — 会话摘要服务（跨会话记忆）
>   - `_summary_vectorstore.py` / `_vectorstore.py` — 摘要与记忆向量库封装
>   - `_history.py` — `SQLiteChatMessageHistory`（LangChain 兼容）
>   - `_extractor.py` — 底层事实提取器
>   - `_parsers.py` — 提取 / 合并 / 聚类解析
>   - `_decay.py` — 衰减分数计算
> - `backend/src/services/knowledge_graph/` — 实体 / 关系 / 向量化

## 1. 为什么需要长期记忆

会议助手面对的是**跨会话、跨时间的用户上下文**：用户偏好、组织结构、重复出现的项目……这些信息不属于任何单次会议记录，但会反复出现在对话里。系统将它们提取出来、独立存储、周期性衰减与合并，实现"AI 记得我"的体验。

## 2. 总体架构

```
 对话流（chain pipeline）
      │
      ├─► 生成答案
      │
      ▼
 schedule_fact_extraction(ctx)
      │
      ▼
 MemoryService.auto_extract_facts()
      │                           ┌──► LLM prompt: get_fact_extraction_prompt()
      │                           │
      ├─► 从最近消息里抽 fact ─────┤
      │                           └──► 解析 JSON → list[FactCandidate]
      │
      ├─► 对每条 fact：
      │     ├─ upsert user_memories (unique user_id+key)
      │     ├─ 写入 embedding id → Chroma（memories collection）
      │     └─ 更新 `last_accessed` / `access_count`
      │
      └─► 触发 KnowledgeGraphService.extract_entities()
            ├─ LLM prompt: get_entity_extraction_prompt()
            ├─ upsert memory_entities
            └─ upsert memory_relations（解析为 subject_id / object_id + predicate）
```

## 3. 数据模型回顾

（详见 [`database.md`](./database.md)）

- **`user_memories`**：`(user_id, key)` 唯一；存 `value`、`category`、`importance`（REAL）、`expires_at`、`embedding_id`、`last_accessed` / `access_count`、`session_id` / `turn_index`（溯源）、`superseded_by` / `relevance_score` 等（完整列见 [`database.md`](./database.md)）
- **`memory_decay_state`**：每用户一行，记录上次衰减时间（列名以 `_migrations.py` / 仓储 SQL 为准）
- **`memory_entities`**：`(user_id, name, entity_type)` 唯一；`description`、`embedding_id`、出现次数与会话溯源字段等
- **`memory_relations`**：外键 **`subject_id` / `object_id`** 指向 `memory_entities.id`，`predicate` 表示关系类型；`(user_id, subject_id, predicate, object_id)` 唯一。LLM 侧可能输出实体名称，入库前解析为 id（见 `knowledge_graph` 服务与 `core/database/knowledge_graph.py`）
- **`memory_scopes`**：记忆的作用域关联（meeting_id / file_id），用于限定检索范围
- **`entity_scopes`**：实体的作用域关联，与 `memory_scopes` 对称
- **`memory_audit_log`**：记忆变更审计日志，记录 CRUD 操作的上下文

## 4. 事实提取（Fact Extraction）

代码：`services/memory/_service/_extraction.py`。

### 4.1 提取时机

- **每轮对话结束**后由 `services/chain/_steps_generate.py` 调用 `schedule_fact_extraction(ctx)`
- 提取是**后台异步**的（`asyncio.create_task(...)`），不阻塞响应返回
- 失败时写入 `ctx.failed_extraction_count`，不抛给调用方

### 4.2 模式：`MEMORY_EXTRACTION_MODE`

```python
_mode_limits = {
    "precise":    1,                             # 极保守，仅关键事实
    "balanced":   settings.MEMORY_MAX_FACTS_PER_TURN,  # 默认 3
    "aggressive": 5,                             # 尽量多
}
```

- **precise**：只提取最明显的 1 条事实；并且**跳过知识图谱实体提取**以降低噪声
- **balanced**：常规生产模式
- **aggressive**：训练数据收集 / 用户画像冷启动阶段用

### 4.3 上下文注入

提取 prompt 中会附带**当前已有的 top-N 重要记忆**，让 LLM 知道哪些事实已经存在，避免反复提取相同内容：

```python
existing = memory_service.list_important(user_id, limit=10)
prompt = get_fact_extraction_prompt(
    conversation=recent_messages,
    existing_memories=existing,
    max_facts=mode_limit,
)
```

### 4.4 去重与替换

新提取的 fact 按 `key` upsert：

- **同 key 存在** → 比较 `relevance_score` / 时间戳，决定是更新 `value` 还是保留
- **合并**时把旧 id 写入新记录的 `superseded_by`（审计追踪）

## 5. 记忆衰减

代码：`services/memory/_decay.py` + `_service/_decay_sync.py`。

### 5.1 公式

```python
# 摘录自 services/memory/_decay.py（需 calendar, time, math）
def _compute_decay_score(importance, last_accessed, decay_rate=_DECAY_RATE_PER_DAY):
    if last_accessed is None:
        return float(importance)
    last_ts = calendar.timegm(time.strptime(last_accessed, "%Y-%m-%d %H:%M:%S"))
    days_elapsed = (time.time() - last_ts) / 86400
    return importance * math.exp(-decay_rate * days_elapsed)
```

指数衰减；`decay_rate` 默认 `_DECAY_RATE_PER_DAY`。列 **`last_accessed`** 在记忆被召回时更新，使用越频繁衰减越慢。

### 5.1.1 检索评分权重

记忆检索使用加权评分公式：

```python
score = (MEMORY_SCORING_SEMANTIC_WEIGHT * semantic_similarity
       + MEMORY_SCORING_DECAY_WEIGHT * decay_score
       + MEMORY_SCORING_IMPORTANCE_WEIGHT * importance)
```

默认权重：`SEMANTIC=0.3`、`DECAY=0.4`、`IMPORTANCE=0.3`，可通过配置调整。

### 5.2 执行方式

- **周期任务** `memory_decay_loop`（lifespan best-effort task）每 `MEMORY_DECAY_INTERVAL_HOURS` 执行一次
- **TTL**：硬过期，`MEMORY_TTL_DAYS` 之后直接删除
- **每用户单独跟踪** `memory_decay_state.last_decay_time` 实现增量衰减

### 5.3 低分记忆的命运

- **importance < threshold** → 不再注入 prompt（但不物理删除，保留以防历史查询）
- **过 TTL** → 物理删除 + 清 Chroma embedding

## 6. 合并（Consolidation）

代码：`services/memory/_parsers.py`。

当同一用户的若干条记忆在语义上高度相似时，合并为一条更权威的记忆：

### 6.1 聚类策略（双模式）

- **`MEMORY_SEMANTIC_CLUSTER_ENABLED=True`**（默认）：用 Chroma 查相邻向量，按相似度阈值聚类
- **`False`**：退化到纯文本重叠（token Jaccard）

### 6.2 合并规则

- 簇大小 ≥ `MEMORY_CONSOLIDATION_MIN_CLUSTER`（默认 3）才触发
- LLM 根据簇内所有记忆生成一条综合文本
- 旧记忆的 `superseded_by` 指向新记忆 id（不物理删除，可追溯）

### 6.3 触发时机

- `MEMORY_CONSOLIDATION_ENABLED=True` 时，每次画像刷新（见 §7）之后触发一次
- 也可通过 `POST /api/v1/memory/decay` 手动触发（接口名略有误导，实际包含 consolidation 步骤）

## 7. 用户画像刷新（Profile Refresh）

代码：`services/memory/_service/_profile.py`。

- **触发**：每 `MEMORY_PROFILE_REFRESH_INTERVAL` 次交互刷新一次
- **行为**：LLM 读取 top-N 重要记忆 → 生成一段"用户画像描述"存为一条特殊记忆（`category=profile`, `key=user_profile`）
- **用途**：在 chain pipeline 的 memory 注入步骤作为 system-level 上下文

## 8. 会话摘要（Episodic Memory）

代码：`services/memory/_summary_service.py`（`SessionSummaryService` 类）。

### 8.1 生成条件

- `SESSION_SUMMARY_ENABLED=True`
- 当前会话消息数 ≥ `SESSION_SUMMARY_MIN_TURNS`
- `POST /api/v1/sessions/{id}/summarize` 手动触发 **或** 后台补跑

### 8.2 写入

```sql
INSERT INTO session_summaries (session_id, summary, key_topics, message_count, generated_at)
```

### 8.3 使用

- **当前会话**：命中 `SESSION_MAX_HISTORY`/`SESSION_MAX_TOKENS` 上限时，用 summary 替换老消息进入 prompt
- **跨会话**：`POST /sessions/search` 能查出历史摘要命中，chain pipeline 可将相关历史会话摘要以 "session_context" 形式注入

### 8.4 关键子模块

| 模块 | 职责 |
|---|---|
| `_summary_service.py` | 跨会话记忆服务：管理 session summary 的生命周期、启动回填、空闲时定期摘要生成（位于 `services/memory/_summary_service.py`，不在 `_service/` 子目录内） |
| `_history.py` | `SQLiteChatMessageHistory`（LangChain 兼容）：将对话历史持久化到 SQLite，供 chain pipeline 读取 |
| `_extractor.py` | 底层事实提取器：封装 LLM 调用与 JSON 解析，供 `_service/_extraction.py` 使用 |
| `_entry.py` | 顶层入口：导出 `MemoryEntry` dataclass，统一记忆条目的结构 |

## 9. 知识图谱

代码：`services/knowledge_graph/`。

### 9.1 实体提取

```python
# _service.py::extract_entities
if settings.MEMORY_EXTRACTION_MODE == "precise":
    return  # skip KG in precise mode

prompt = get_entity_extraction_prompt(messages, existing_entities)
response = await llm.ainvoke(prompt)
entities, relations = parse_entity_response(response)

await _store_entities(entities)
await _store_relations(relations)
```

### 9.2 实体存储

- upsert `memory_entities`（`(user_id, name, entity_type)` 唯一）
- 同时 embed name+description 到 Chroma 的 `entities` collection
- 失败时**结构化日志**记录（不回滚 SQL 侧写入）

### 9.3 关系存储

- upsert `memory_relations`
- 元数据存在 `metadata` JSON 字段
- `ENTITY_RELATIONS_LIMIT` 控制单实体返回关系数量上限（避免超大名人节点爆炸）

### 9.4 查询路径

| API | 行为 |
|---|---|
| `GET /api/v1/memory/entities` | 列出所有实体（分页） |
| `GET /api/v1/memory/entities/{name}` | 返回实体 + 所有出入关系 + 相关记忆 |
| `DELETE /api/v1/memory/entities/{name}` | 删除实体、关系及向量 |
| `POST /api/v1/memory/entities/merge` | 把多个同义实体合并到一个主实体 |

### 9.5 注入到 RAG

chain pipeline 的 `load_entity_context` 步骤：

1. 对用户查询做实体识别（LLM 或正则）
2. 查 Chroma `entities` collection 找相似实体
3. 根据实体查 `memory_relations` 扩一跳 / 两跳
4. 把"实体 + 关系摘要"注入到最终 prompt 的 `entity_context` 段

## 10. 记忆注入到 Prompt 的顺序

chain pipeline 的 `build_context` 步骤按如下优先级拼装：

```
[user_profile]                        ← 画像（如存在）
[important_memories (top 6)]          ← 按 decay score 降序
[session_context]                     ← 历史会话摘要（跨会话）
[entity_context]                      ← 知识图谱扩展
[web_results]                         ← 可选
[retrieved_chunks]                    ← RAG 文档
[history]                             ← 当前会话最近 N 轮
→ 生成 prompt
```

每一段都有 token budget，超过时按相似度降序截断。

## 11. API 一览

| Method | Path | 说明 |
|---|---|---|
| `GET` | `/api/v1/memory` | 列表（支持过滤、排序） |
| `POST` | `/api/v1/memory` | 创建 |
| `PUT` | `/api/v1/memory` | 更新 |
| `DELETE` | `/api/v1/memory` | 删除 |
| `POST` | `/api/v1/memory/batch` | 批量导入 |
| `GET` | `/api/v1/memory/export` | JSON 导出 |
| `POST` | `/api/v1/memory/search` | 语义搜索 |
| `POST` | `/api/v1/memory/decay` | 手动衰减 + 合并 |
| `GET` | `/api/v1/memory/entities` | 列表实体 |
| `GET` | `/api/v1/memory/entities/{name}` | 实体详情 |
| `DELETE` | `/api/v1/memory/entities/{name}` | 删除实体 |
| `POST` | `/api/v1/memory/entities/merge` | 合并实体 |

## 12. 调优与故障排查

| 症状 | 调优方向 |
|---|---|
| 记忆提取太多噪声 | `MEMORY_EXTRACTION_MODE=precise` 或降低 `MAX_FACTS_PER_TURN` |
| 记忆不更新 | 检查 `auto_extract` 是否 True；查后台任务异常日志 |
| 旧偏好一直不消失 | 调大 `decay_rate` / 调小 `TTL_DAYS` / 手动 `DELETE` |
| 实体爆炸 | 降 `aggressive` → `balanced`；提高 entity 去重阈值 |
| Chroma entities 维度不匹配 | 切 embedding 后 `rebuild-vectors` |
| 跨会话历史无法回忆 | 确认 `SESSION_SUMMARY_ENABLED=True`；检查 `session_summaries` 是否有数据 |

## 13. 扩展方向

1. **时间衰减可配**：目前 `decay_rate` 是全局常量，可按 category 区分（如 "preference" 衰减慢于 "task"）
2. **向量-文本混合去重**：现在合并依赖纯向量聚类，加入文本规则可减少误合并
3. **Fact 置信度**：LLM 提取时要求 `confidence`，低置信走确认回合
4. **多用户隔离增强**：当前按 `user_id` 切分，可考虑加 `workspace_id` 多租户
5. **图谱 2-hop 推理**：当前只取一跳关系，可以根据查询展开更多步
