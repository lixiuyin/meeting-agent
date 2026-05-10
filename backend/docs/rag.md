# RAG 架构、原理与优化指南

> 本文档是 Meeting Agent RAG 子系统的单一权威文档，覆盖 **架构总览 → 索引(Chunking) → 检索(Retrieval) → 重排(Rerank) → 后处理 → 配置参考 → 优化方向**。
>
> 代码位置：
> - `backend/src/services/rag/` — RAG 基础设施（向量库、索引、检索、重排、查询改写、漏斗筛选、作用域路由等，27 个功能模块 + `__init__.py`）
> - `backend/src/services/chain/` — 编排层（路由、上下文装配、LCEL 生成、流式事件、trace，共 25 个功能模块）
> - `backend/src/core/config.py` — 配置聚合（YAML + env）
> - `backend/config/main.yaml` — 默认 YAML 配置

---

## 目录

1. [总体架构](#1-总体架构)
2. [配置体系与开关矩阵](#2-配置体系与开关矩阵)
3. [索引流程 (Chunking)](#3-索引流程-chunking)
4. [检索流程 (Retrieval)](#4-检索流程-retrieval)
5. [重排流程 (Rerank)](#5-重排流程-rerank)
6. [后处理：去重 & 上下文装配](#6-后处理去重--上下文装配)
7. [查询改写与自适应 top-k](#7-查询改写与自适应-top-k)
8. [配置参考表](#8-配置参考表)
9. [场景配置模板](#9-场景配置模板)
10. [性能特征](#10-性能特征)
11. [故障排查](#11-故障排查)
12. [优化方向（持续演进）](#12-优化方向持续演进)

---

## 1. 总体架构

RAG 管线被拆分为两层：

| 层 | 位置 | 职责 |
| --- | --- | --- |
| **RAG 基础设施** | `services/rag/` | 向量库单例、索引、检索、重排序、查询改写 |
| **Chain 编排层** | `services/chain/` | 路由、上下文装配、LCEL 生成、流式事件、trace |

两层通过 `PipelineContext` / `PipelineResult` 传递状态（`chain/_context.py`）。Chain 层以 `_steps_*` 的形式把 “改写 → 检索 → 重排 → 去重 → 上下文装配 → 生成” 拆成可独立 trace 的步骤：

- `chain/_steps_session.py` — 会话/查询改写
- `chain/_steps_retrieve.py` — 检索 + 重排 + 近重复抑制
- `chain/_steps_context.py` — 长期记忆 / 知识图谱 / Session 摘要 / Web 搜索 / 对话历史装配
- `chain/_steps_generate.py` — 构造 prompt、LCEL chain、保存消息、后台事实抽取
- `chain/_routing.py` — 意图分类（`casual` vs `rag`）
- `chain/_formatting.py` — sources 提取
- `chain/_api.py` — 顶层 `ask()` / `ask_stream()` 入口

### 1.1 端到端流程（以 `ask_stream` 为例）

```
User Query
   │
   ▼
[Routing] `_routing.py` 判断 `casual` / `rag`（文档里口语化的 “retrieval” 即代码中的 `rag`）
   │
   ▼
[Query Rewrite] rag/_query.py
   - 短问题 & 无代词 → 跳过
   - 否则调用 lightweight LLM (QUERY_REWRITE_MODEL) 改写
   │
   ▼
[Retrieve] chain/_steps_retrieve.py → rag/_retriever.py
   - 自适应 top_k (determine_adaptive_top_k)
   - Reranker 启用时 over-fetch = top_k * RAG_RERANK_FETCH_MULTIPLIER
   - 可选 Multi-Query: LLM 生成 N 个变体并行检索后合并
   - 单路或 Hybrid (向量 + BM25/FTS5) + RRF 融合
   │
   ▼
[Rerank] rag/_reranker.py
   - Cohere API / 本地 BGE Cross-Encoder
   - RERANKER_MIN_SCORE 截断
   │
   ▼
[Dedup] chain/_steps_retrieve.py suppress_near_duplicates()
   - 4-gram 重叠率 ≥ 0.85 视为重复
   │
   ▼
[Context Assembly] _steps_context.py
   - 注入长期记忆、知识图谱实体、Session 摘要、Web 搜索结果、对话历史
   │
   ▼
[Generate] _steps_generate.py
   - LCEL chain → LLM 生成 / 流式
   - StreamBus 推送 step/token/sources/trace/done 事件
   - 背景任务：抽事实到 memory
```

### 1.2 核心设计原则

1. **Best-effort 降级** — 任何可选组件（reranker、query rewrite、hybrid、multi-query、memory、KG、web search）失败都不应让主流程崩溃，改走 `logger.warning(..., exc_info=True)` + 原序返回。
2. **线程安全单例** — 所有重对象（Chroma、LLM、Embeddings、Reranker、Cohere Client、Rewrite LLM）使用 **double-checked locking**，并在 settings 变更时可重置。
3. **确定性 ID & 幂等 Upsert** — 默认前缀 `meeting_{meeting_id}_file_{file_id}_chunk_{i}`（无 `file_id` 时为 `meeting_{meeting_id}_chunk_{i}`）；另有按 chunking 策略区分的 parent/child id（见 `_indexer.py`）。支持 `delete_meeting_chunks(meeting_id, file_id=...)` 精确删除。
4. **非阻塞** — 向量检索、BM25、LLM 调用、rerank 全部走 `asyncio.to_thread()`，保护 FastAPI 事件循环。
5. **Trace 可观测** — `TraceContext` 按 step（`chunk`、`embed`、`vectorstore_upsert`、`retrieve`、`rerank`、`suppress_near_duplicates` …）开启/关闭 span，支持 benchmark 与线上诊断。

---

## 2. 配置体系与开关矩阵

### 2.1 三级优先级

```
config/main.yaml          (非秘密默认：模型名、RAG 参数、上传上限)
        ↓ merged by
.env                      (secrets, env overrides)
        ↓ merged by
OS environment variables  (最高优先级)
        ↓
src/core/config.py        (pydantic-settings 聚合)
```

### 2.2 RAG 关键开关（默认大多 `False`）

| 开关 | 默认 | 主要影响 |
| --- | --- | --- |
| `SEMANTIC_CHUNKING_ENABLED` | false | 启用结构感知切分 |
| `PARENT_CHILD_ENABLED` | false | 启用父子双层切片（small-to-big） |
| `HYBRID_SEARCH_ENABLED` | false | 启用向量+BM25 + RRF |
| `MULTI_QUERY_ENABLED` | false | 生成 query 变体多路检索 |
| `QUERY_REWRITE_ENABLED` | false | LLM 改写 query |
| `RERANKER_BINDING` | `""` | `cohere` / `bge` / `""`(禁用) |
| `DISTANCE_METRIC` | l2 | `l2` / `cosine` |
| `RAG_RETRIEVER_PROVIDER` | `native` | `native` / `hybrid` / `multimodal` / `hybrid_multimodal` |
| `RAGANYTHING_ENABLED` | false | 启用 RAGAnything 多模态检索 |
| `RAGANYTHING_FALLBACK_TO_NATIVE` | true | RAGAnything 失败时降级到 native |

可见 **默认配置是极简 flat vector 检索**，适合开发调试；生产建议至少开启 Cohere/BGE rerank。

---

## 3. 索引流程 (Chunking)

入口：`rag/_indexer.py:index_meeting(meeting_id, text, metadata, trace)`

根据 `PARENT_CHILD_ENABLED` 分流到 `_index_flat()` 或 `_index_parent_child()`；随后如果 `HYBRID_SEARCH_ENABLED` 为真则追加 `_add_to_bm25()`。

### 3.1 分隔符层级

`_SEPARATORS` 按 “高语义边界 → 低语义兜底” 级联（`RecursiveCharacterTextSplitter` 从左到右尝试）：

```python
_SEPARATORS = [
    "\n\n---\n\n",   # 段落分隔线（Markdown 横线）
    "\n\n",          # 段落
    "\n",            # 行
    ". ",            # 英文句号
    "。",            # 中文句号
    "；",            # 中文分号
    " ",             # 词边界
    "",              # 字符级兜底
]
```

覆盖中英文写作惯例，保证分块优先落在语义分界而非句子中段。

### 3.2 Flat Chunking（`_index_flat`）

**默认参数**：`CHUNK_SIZE=1024`，`CHUNK_OVERLAP=128`（**字符级**，不是 token）。

流程：

1. 若 `SEMANTIC_CHUNKING_ENABLED=True`：
   - 先 `_split_by_structure(text, max_chunk_size=CHUNK_SIZE)` 按主题边界粗切
   - 仍超过 `CHUNK_SIZE` 的段再用 `RecursiveCharacterTextSplitter` 二次切分
2. 否则直接走 `RecursiveCharacterTextSplitter(chunk_size=1024, overlap=128)`。
3. 构造 `Document(page_content, metadata)`：
   ```python
   metadata = {
       "meeting_id": meeting_id,
       "chunk_index": i,
       "file_id": metadata.get("file_id"),
       "file_type": metadata.get("file_type"),
       ...其余 metadata,
   }
   ```
4. `ids = [f"meeting_{meeting_id}_chunk_{i}" for i in range(len(docs))]` —— 确定性 ID 是幂等 upsert 与 per-meeting 删除的基石。
5. 交给 `_dedup_existing_chunks()` 过滤未变更块，再走 `_upsert_with_trace()`。

#### 3.2.1 结构感知切分 `_split_by_structure`

位置：`rag/_chunkers.py`

正则 `_TOPIC_BREAK_PATTERNS` 识别会议纪要的天然 topic 边界：

| 类型 | 正则片段 | 示例 |
| --- | --- | --- |
| Markdown 标题 | `^#{1,4}\s+` | `## 技术讨论` |
| 有序列表 | `^\d+[\.\)]\s+` | `1. 引言`、`2) 方案` |
| Bullet 大写 | `^[-*]\s+[A-Z]` | `- Action items` |
| 水平线 | `^-{3,}$`, `^={3,}$` | `---` / `===` |
| Speaker 标签 | `^Speaker\s+\d` | `Speaker 1` |
| 中文小节 | `^【.*?】` | `【会议纪要】` |
| 中文会议头 | `^(会议\|讨论\|总结\|决议\|议题)[:]` | `议题：Q2 规划` |

算法：

```python
lines = text.split("\n")
segments: list[str] = []
current: list[str] = []

for line in lines:
    if _TOPIC_BREAK_PATTERNS.match(line) and current:
        segments.append("\n".join(current))
        current = [line]
    else:
        current.append(line)
segments.append("\n".join(current))

# 贪心合并小段到 max_chunk_size
merged, buffer, buffer_len = [], [], 0
for seg in segments:
    if buffer and buffer_len + len(seg) + 1 > max_chunk_size:
        merged.append("\n".join(buffer))
        buffer, buffer_len = [seg], len(seg)
    else:
        buffer.append(seg)
        buffer_len += len(seg) + 1
if buffer:
    merged.append("\n".join(buffer))

return merged if merged else [text]
```

**优点**：保留 topic 内聚性；**局限**：正则依赖文档格式，自由文本收益有限，需要针对特定领域调正则。

### 3.3 Parent-Child Chunking（`_index_parent_child`）

启用条件：`PARENT_CHILD_ENABLED=True`

参数：

- 父块：`CHUNK_SIZE=1024`，`CHUNK_OVERLAP=128`
- 子块：`CHILD_CHUNK_SIZE=256`，`CHILD_CHUNK_OVERLAP=32`

结构（Chroma 中的存储）：

```
Parent: meeting_1_parent_0  chunk_type="parent"
  ├─ Child: meeting_1_child_0_0  chunk_type="child", parent_id="meeting_1_parent_0"
  ├─ Child: meeting_1_child_0_1  chunk_type="child", parent_id="meeting_1_parent_0"
  └─ ...
Parent: meeting_1_parent_1
  └─ ...
```

**检索行为**：

- `_build_filters` 自动追加 `{"chunk_type": "child"}`，保证向量搜索只打中子块（更精）。
- 命中子块后由 `_resolve_parent_chunks()` 按 `parent_id` 去重、保留最佳分、批量拉取父块（更完整），返给 LLM。
- 这是经典的 **small-to-big** 策略：小块精检索、大块给上下文。
- 代价：embedding 量近似翻倍（parent 也要 embed，因为 `_upsert_with_trace` 对全部 docs 统一 embed）。

### 3.4 去重与增量索引 `_dedup_existing_chunks`

位置：`_indexer.py:37`

```python
def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]

existing = vectorstore.get(ids=ids, include=["documents"])
existing_map = {eid: _content_hash(doc) for eid, doc in zip(existing["ids"], existing["documents"])}

new_docs, new_ids = [], []
for doc, chunk_id in zip(docs, ids):
    if existing_map.get(chunk_id) == _content_hash(doc.page_content):
        continue
    new_docs.append(doc)
    new_ids.append(chunk_id)
```

收益：
- **重复摄入同一文件不会重新 embed**（省 API 费用 / GPU 时间）。
- **只对变更 chunk 写入**，降低写放大。

**已知限制**：
- `_dedup_existing_chunks` 用 `(chunk_id, sha256(page_content)[:16])` 判断是否跳过；`chunk_id` 已包含 `meeting_id` + `file_id`（见 `_chunk_id_prefix`），**不同文件的相同文本不会共享 chunk id**。
- 哈希**不包含**其它 metadata（页码、标题变更若未反映到正文，仍可能沿用旧向量）；优化讨论见 §12。

### 3.5 Upsert + Trace `_upsert_with_trace`

```python
# Step 1: embed
trace.start_span("embed", "index")
embeddings = get_embeddings().embed_documents([d.page_content for d in docs])
trace.finish_span("embed")

# Step 2: upsert (直接用 Chroma 底层 collection 避免 LangChain 包装开销)
trace.start_span("vectorstore_upsert", "index")
vectorstore._collection.upsert(
    ids=ids,
    documents=[d.page_content for d in docs],
    embeddings=embeddings,
    metadatas=[d.metadata for d in docs],
)
trace.finish_span("vectorstore_upsert")
```

两个 span 分别计时，便于 benchmark 区分 embed 与写库瓶颈。

### 3.6 BM25/FTS5 镜像索引 `_add_to_bm25`

`HYBRID_SEARCH_ENABLED=True` 时，同一文本再走一次 flat `RecursiveCharacterTextSplitter`，写入 SQLite 的 `bm25_index` 表：

```python
add_bm25_chunk(
    conn,
    chunk_id=f"meeting_{meeting_id}_chunk_{i}",
    meeting_id=meeting_id,
    content=chunk,
    tokenized="[]",          # 实际 tokenize 交给 FTS5
    metadata=json.dumps({"meeting_id": ..., "chunk_index": i, ...}),
)
```

SQLite 触发器自动把 `bm25_index` 表的插入/删除同步到 `chat_messages_fts`/`bm25_fts` FTS5 虚表（见 `core/database/bm25.py`）。

**注意**：
- BM25 侧固定使用 flat 切分，**不支持 parent-child，也不过结构感知切分的 `_split_by_structure` 路径**。
- `tokenized="[]"` 是占位符，FTS5 自己用默认 tokenizer（`unicode61` 或 `porter`）切词。

### 3.7 删除 `delete_meeting_chunks`

```python
def delete_meeting_chunks(meeting_id: int, file_id: int | None = None) -> None:
    where = {"meeting_id": meeting_id}
    if file_id is not None:
        where["file_id"] = file_id
    vectorstore.delete(where=where)
    _remove_from_bm25(meeting_id)  # ← per-file 语义缺失，见 §12
```

- 向量侧支持 `(meeting_id, file_id)` 精确删除。
- BM25 侧目前只按 `meeting_id` 清空，per-file 语义残留是已知待优化点。

---

## 4. 检索流程 (Retrieval)

入口：`rag/_retriever.py:retrieve(query, meeting_ids, file_ids, top_k, fetch_multiplier, file_types, date_from, date_to, rag_mode)`

### 4.1 策略模式 `_strategies.py`

检索层使用策略模式，由 `RAG_RETRIEVER_PROVIDER`（值：`native` / `hybrid` / `multimodal` / `hybrid_multimodal`）选择具体策略：

| 策略类 | provider 值 | 行为 |
| --- | --- | --- |
| `NativeStrategy` | `native` | 纯向量（或 `HYBRID_SEARCH_ENABLED` 时走 `_hybrid_retrieve`） |
| `HybridStrategy` | `hybrid` | 向量 + BM25 RRF 融合 |
| `MultimodalStrategy` | `multimodal` | RAGAnything 多模态检索，失败可降级到 native |
| `HybridMultimodalStrategy` | `hybrid_multimodal` | native 向量 + RAGAnything 双路 RRF 融合 |

`select_strategy()` 根据归一化的 provider 字符串返回对应策略实例。每个策略都实现 `RetrievalStrategy` 协议（`name` + `retrieve()` 方法）。

`MultimodalStrategy` 和 `HybridMultimodalStrategy` 在 scoped 查询（有 `file_ids` 或 `meeting_ids`）时自动降级到 native，因为 RAGAnything 不支持精确 scope 过滤。

### 4.2 过滤器构造 `_build_filters`

```python
clauses = []
has_scope_ids = bool(meeting_ids or file_ids)
if settings.PARENT_CHILD_ENABLED and not has_scope_ids:
    clauses.append({"chunk_type": "child"})
if meeting_ids:
    clauses.append({"meeting_id": {"$in": meeting_ids}})
if file_ids:
    clauses.append({"file_id": {"$in": file_ids}})
if file_types:
    clauses.append({"file_type": {"$in": file_types}})
if date_from:
    clauses.append({"meeting_date": {"$gte": int(date_from.strftime("%Y%m%d"))}})
if date_to:
    clauses.append({"meeting_date": {"$lte": int(date_to.strftime("%Y%m%d"))}})
```

关键点：
- 接受 `file_ids` 参数，支持按文件精确过滤。
- 日期用 `YYYYMMDD` int 存储以支持 `$gte / $lte`（Chroma 对字符串不支持数值比较）。
- 单子句直接返回，无需 `$and` 包裹。
- parent-child 过滤仅在无 scope ids 时追加，有 scope 时不强制 `chunk_type=child`。

### 4.3 向量检索 `_vector_retrieve`

```python
results = vectorstore.similarity_search_with_score(query, k=k, filter=filters)
is_cosine = settings.DISTANCE_METRIC == "cosine"
```

**距离度量语义**：

| 度量 | 分数方向 | 阈值含义 |
| --- | --- | --- |
| `l2`（默认） | 越低越好 | `score > threshold` 被过滤 |
| `cosine` | 越高越好 | `score < threshold` 被过滤 |

`threshold=None` 时跳过过滤（hybrid 融合路径会这样做）。

**Parent-Child 解析** `_resolve_parent_chunks`：

```python
seen_parents: dict[str, float] = {}  # parent_id -> 最佳 score
for doc, score in child_results:
    parent_id = doc.metadata.get("parent_id")
    if not parent_id:
        continue
    if parent_id not in seen_parents or _better(score, seen_parents[parent_id], is_cosine):
        seen_parents[parent_id] = score

parent_data = vectorstore.get(ids=list(seen_parents.keys()), include=["documents", "metadatas"])
return [{"content": c, "metadata": m, "score": seen_parents[pid]} for c, m, pid in ...]
```

### 4.4 Hybrid Retrieve + RRF `_hybrid_retrieve`

```python
def _hybrid_retrieve(query, filters, fetch_k, top_k, threshold):
    meeting_ids = _extract_in_filter(filters, "meeting_id")
    file_ids = _extract_in_filter(filters, "file_id")
    vector_results = _vector_retrieve(query, filters, fetch_k, threshold=None)  # 不过滤
    bm25_results = _bm25_retrieve(query, meeting_ids, file_ids, fetch_k)
    return _rrf_merge(vector_results, bm25_results, top_k)
```

- 融合前 **向量侧不做阈值过滤**，保证 BM25-only 命中的文档在 RRF 中获得公平 rank。
- BM25 侧接受 `meeting_ids` 和 `file_ids` 过滤。

**RRF 公式** (`_rrf_merge`)：

```python
score(doc) = α / (k + rank_vec + 1) + (1-α) / (k + rank_bm25 + 1)
```

其中：
- `k = 60`（硬编码，行业惯例）
- `α = HYBRID_ALPHA`（默认 0.5，等权）
- `rank_*` 是文档在各路径中的 0-based 排名

**分数归一化**：`_rrf_merge` 将最终 RRF 分数归一化到 [0, 1] 范围：

```python
max_score = merged[0][1]
min_score = merged[-1][1]
score_range = max_score - min_score or 1.0
return [{**doc_map[key], "score": (score - min_score) / score_range} for key, score in merged]
```

**`_rrf_merge_multi`** 用于合并两个以上的结果列表（如 `hybrid_multimodal` 模式下合并向量 + RAGAnything）：

```python
def _rrf_merge_multi(result_lists: list[tuple[list[dict], float]], top_k, k=60):
    # result_lists: [(results, weight), ...]
    for results, weight in result_lists:
        for rank, doc in enumerate(results):
            key = _rrf_dedup_key(doc)
            rrf_scores[key] = rrf_scores.get(key, 0.0) + weight / (k + rank + 1)
```

同样归一化到 [0, 1] 范围。

**去重键 `_rrf_dedup_key`**：

```python
meta = doc.get("metadata") or {}
if meta.get("chunk_id"):
    return str(meta["chunk_id"])
if meta.get("meeting_id") is not None and meta.get("chunk_index") is not None:
    return f"m{meta['meeting_id']}_c{meta['chunk_index']}"
return hashlib.md5(doc["content"].encode()).hexdigest()
```

三级 fallback：chunk_id → meeting+index → 内容 MD5，保证跨路径的同一文档能被正确合并。

### 4.5 BM25 检索 `_bm25_retrieve`

```python
def _bm25_retrieve(query, meeting_ids, file_ids, k):
    with get_connection() as conn:
        results = fts5_search(conn, query, meeting_ids=meeting_ids, file_ids=file_ids, limit=k)

for r in results:
    meta = json.loads(r[“metadata”]) if r[“metadata”] else {“meeting_id”: r[“meeting_id”]}
    # FTS5 rank 是负 BM25 分数；取负让 “越大越好”
    out.append({“content”: r[“content”], “metadata”: meta, “score”: float(-r[“rank”])})
```

- 接受 `file_ids` 参数，支持按文件精确过滤。
- FTS5 内建 BM25，`rank` 字段本身是 **负** BM25 得分，要显式取负。
- 失败降级返回 `[]`，整条 hybrid 链路变成纯向量。

### 4.6 Sibling Co-Retrieval `retrieve_sibling_chunks`

位置：`rag/_retriever.py`

```python
def retrieve_sibling_chunks(docs, *, max_per_anchor=1, max_total=4):
    “””Fetch sibling multimodal chunks from the same file/page as top hits.”””
```

在主检索完成后，从数据库中拉取与命中 chunks 同页/同文件的多模态兄弟 chunks（表格、图片描述、OCR 等）。由 `RAG_SIBLING_CORETRIEVE_ENABLED`（默认 `True`）控制开关。

参数：
- `max_per_anchor`：每个锚点 chunk 最多拉取几个兄弟（`RAG_SIBLING_CORETRIEVE_PER_ANCHOR`，默认 1）
- `max_total`：总兄弟数量上限（`RAG_SIBLING_CORETRIEVE_MAX_TOTAL`，默认 4）

兄弟查找使用 `get_page_sibling_chunks()` 从数据库查询，如果数据库无结果则通过 `_vector_sibling_fallback()` 直接从 Chroma 元数据匹配。

### 4.7 BM25 漂移检测与重建

位置：`rag/_retriever.py`

**`check_and_rebuild_bm25_if_drifted()`**：在启动时检测 FTS5 与 Chroma 之间的数据漂移。当行数差异超过可配置阈值（`BM25_DRIFT_THRESHOLD`，默认 10%）时自动触发重建。

**`rebuild_bm25_from_chroma(force=False)`**：从 Chroma 现有数据重建 FTS5 索引。默认仅在 FTS5 为空时重建；`force=True` 时总是重建（用于漂移检测场景）。跳过 `chunk_type=parent` 的块。

### 4.8 Multi-Query（仅非 Hybrid 场景）

位置：`chain/_steps_retrieve.py:_generate_query_variants` + `retrieve_documents`

触发条件：
- `MULTI_QUERY_ENABLED=True`
- **非 hybrid**（hybrid 已通过 RRF 提供多样性）
- **非 simple query**（短问题不值得多路展开）

流程：

1. 用主 LLM 生成 `MULTI_QUERY_COUNT=3` 个变体：
   ```
   "Generate {n} alternative phrasings of the following question for search purposes.
    Each variant should capture the same intent but use different words or angles.
    Return ONLY a JSON array of strings, no explanation."
   ```
2. 原 query + 变体（共 4 个）并行检索，每个 `per_query_k = max(effective_k // len(queries), 3) * fetch_multiplier`。
3. `_dedup_docs` 按 `content[:200]` 合并，保留 **更好的分数**（L2 取小、Cosine 取大）。
4. 按分数排序后截 `effective_k * fetch_multiplier`，交给 reranker。

**为什么 hybrid 下不用 multi-query**：RRF 已通过向量+BM25 两个维度提供多样性，再叠加 multi-query 收益递减、成本线性增。

### 4.9 Fair Per-File Retrieval `_fair_retriever.py`

位置：`rag/_fair_retriever.py`

当使用 broad recall（无显式 file_ids）时，`fair_retrieve_per_file()` 保证作用域内每个文件都能贡献 chunk：

```python
async def fair_retrieve_per_file(
    query: str,
    scope_file_ids: list[int],
    *,
    chunks_per_file: int | dict[int, int] = 2,
    cached_docs: dict[int, list[dict]] | None = None,
) -> list[dict]:
```

- 对 `scope_file_ids` 中每个文件，调用 `retrieve()` 并限定 `file_ids=[file_id]`，`top_k=chunks_per_file`
- `chunks_per_file` 可以是统一 int，也可以是 `dict[int, int]`（按文件 ID 分配不同预算）
- `cached_docs` 支持从 wide-fetch 缓存直接取，跳过 Chroma 调用
- 并发受 `settings.RAG_FAIR_CONCURRENCY` 信号量控制
- 结果按 `chunk_id → (meeting_id, file_id, chunk_index) → content sha1` 三级去重

### 4.10 Funnel Score Aggregation `_funnel.py`

位置：`rag/_funnel.py`

提供 chunk → file → meeting 的分数聚合，是 funnel narrow 的核心：

| 函数 | 用途 |
| --- | --- |
| `aggregate_by_meeting()` | chunk 分数按 meeting_id 聚合，返回 top-N 会议 |
| `aggregate_by_file_scored()` | chunk 分数按 file_id 聚合，返回 `(file_id, score)` 对；支持 title prior、chunk-count fairness 因子 |
| `normalize_scores()` | 将原始分数归一化到 [0, 1]（L2 距离用 `1/(1+s)` 转换） |
| `fetch_title_priors()` | SQL 查询会议标题/描述与 query token 的匹配度，返回 boosting |
| `fetch_file_title_priors()` | 文件级标题 prior，含 full-match bonus |
| `restrict_pool()` | 按 meeting_ids / file_ids 过滤文档池（immutable pattern） |

聚合方法由 `RAG_FUNNEL_AGGREGATION` 控制：`top_k_mean`（默认，取 top-K 均值）、`max`、`count`。

### 4.11 Funnel Narrow `_funnel_narrow.py`

位置：`rag/_funnel_narrow.py`

`narrow_scope_via_funnel()` 是 broad recall 模式下文件选择的完整流程：

1. **Wide fetch**：一次大量 Chroma 检索覆盖 meeting + anchor 作用域（`wide_k` 由 `RAG_FUNNEL_WIDE_K_MIN/MAX` 和 log-scaling 动态计算）
2. **Aggregate**：`aggregate_by_file_scored()` 将 chunk 分数卷积到文件级
3. **Evidence floor**：按 `RAG_FUNNEL_EVIDENCE_MODE`（`absolute` / `ratio` / `percentile`）过滤弱文件；router top-K 文件受保护（不因弱 chunk 证据被淘汰）
4. **Merge**：router 与 funnel 文件列表合并（`rrf` 策略或 legacy `zigzag`）
5. **Anchor injection**：`apply_anchor_evict()` 确保会话锚点文件出现
6. 返回 `ScopeSelection`（含 file_scores + docs_by_file 缓存）

M4 优化：summary router 和 wide-fetch 并行执行（`asyncio.create_task`），消除串行等待。

### 4.12 Anchor Injection `_anchor_inject.py`

位置：`rag/_anchor_inject.py`

`apply_anchor_evict()` 将"必须包含"的锚点文件注入候选 scope，在超出 cap 时从尾部淘汰非锚点文件：

- `cap`：最大文件数
- `quota_ratio`：锚点文件可占的最大比例（`RAG_ANCHOR_QUOTA_RATIO`）
- 返回 `(new_scope, evicted_count)`
- 被两个消费者共用：`_funnel_narrow.py` 和 `RouterOnlyStrategy`

### 4.13 Query Analysis `_query_analysis.py`

位置：`rag/_query_analysis.py`

`analyze_query()` 提供纯正则（无 LLM 调用）的轻量级查询分析：

**Speaker name extraction** — 三层策略：
1. 已知 speakers（会议元数据 / speaker_mappings）→ 精确子串匹配（word-boundary-aware，LRU 缓存编译的正则）
2. 英文名 → 大写词正则（排除 `What/How/Why` 等疑问词）
3. 中文 → speaker-query pattern 触发的 2-4 字 CJK 匹配（排除 `会议/讨论` 等常见词）

**Temporal hint detection** — 支持：
- 绝对时间："前2分钟"、"最后5分钟"、"first 3 minutes"（存储为 `absolute_seconds` 元组）
- 相对区域："前期/中期/后期/中后期/前半/后半" 等（映射为 `ratio_min/ratio_max`）
- 中文数字解析（`_parse_zh_number`）

返回 `QueryAnalysis`（`speaker_names` + `temporal_hint` + `topic_query`）。

---

## 4B. 作用域路由 (Scoping)

当用户未显式指定 `file_ids` 时，RAG 管线需要决定"检索哪些文件"。作用域路由模块负责这一步。

### 4B.1 数据类型 `_scope_types.py`

`ScopeSelection`（frozen dataclass）：策略调用的结果，包含：
- `scope_file_ids` — 有序文件 ID 列表
- `file_scores` — 文件级相关性分数 `[0, 1]`，用于下游 adaptive chunk allocation
- `docs_by_file` — wide-fetch 缓存，按 file_id 分组，供 `fair_retrieve_per_file` 复用

`BroadRecallContext`：请求级 memoization，多 query variant 共享同一 meeting scope 的 wide-fetch 结果（double-checked locking + `asyncio.Lock`）。

### 4B.2 File Scoping Strategies `_scoping_strategies.py`

`FileScopingStrategy` 协议定义了 `select_scope()` 接口。四种实现由 `RAG_FILE_SCOPING_MODE` 选择：

| 策略 | mode 值 | 行为 |
| --- | --- | --- |
| `RouterAndFunnelStrategy` | `router_and_funnel`（默认） | summary router + funnel 并行，RRF merge |
| `FunnelOnlyStrategy` | `funnel_only` | 跳过 router，funnel 全权负责文件选择 |
| `RouterPreFilterStrategy` | `router_pre_filter` | router 先缩小 meeting scope，funnel 在其内操作 |
| `RouterOnlyStrategy` | `router_only` | router 直接选文件，不做 funnel narrow |

### 4B.3 Scope Routing `_routing.py`

`_enumerate_scope_files()` — 从数据库枚举所有 ready file IDs（broad recall 的 fallback 基线）。

`_route_scope_files_via_summary()` — 调用 summary router 预缩窄文件，返回 file_id 列表；disabled/空时返回 `None`。

`_route_scope_files_with_scores()` — 同上但返回 `(file_id, score)` 对。

`router_prefilter_meetings()` — 用 router 的 top file selections 反推 meeting IDs，供 `router_pre_filter` 策略使用。

所有函数带 Prometheus 指标（`SUMMARY_ROUTER_REQUEST_TOTAL`、`SUMMARY_ROUTER_FILES_ROUTED`）和 trace span。

### 4B.4 Summary Router `_summary_router.py`

文件级路由，基于 per-file summary embeddings + BM25 + RRF：

`route_files_by_summary(query, meeting_ids)`：
1. 向量检索：summary vectorstore 中找相似文件摘要
2. BM25 检索（当 `RAG_SUMMARY_ROUTER_HYBRID_ENABLED=True`）：`fts5_search_file_summaries` 关键词匹配
3. RRF 融合：`_rrf_fuse_file_lists()` 以 `RAG_SUMMARY_ROUTER_HYBRID_ALPHA` 权重合并
4. 降级：vector-only fallback 当 BM25 不可用

`route_files_with_scores()` — 同上但保留分数（供 trace 使用）。

关键配置：
- `RAG_SUMMARY_ROUTER_ENABLED` — 总开关
- `RAG_SUMMARY_ROUTER_TOP_FILES` — 最多选几个文件
- `RAG_SUMMARY_ROUTER_MIN_SCORE` — 最低分数阈值
- `RAG_SUMMARY_ROUTER_FALLBACK_TO_CHUNK` — router 无命中时是否回退到全量

---

## 5. 重排流程 (Rerank)

入口：`rag/_reranker.py:rerank(query, docs, top_n)`

```python
binding = settings.RERANKER_BINDING.lower()
if not binding or not docs:
    return docs
top_n = top_n or settings.RERANKER_TOP_N

if binding == "cohere":
    ranked = _rerank_cohere(query, docs, top_n)
elif binding == "bge":
    ranked = _rerank_bge(query, docs, top_n)
else:
    return docs

# 最低分过滤
if settings.RERANKER_MIN_SCORE > 0:
    ranked = [d for d in ranked if d.get("score", 0) >= settings.RERANKER_MIN_SCORE]
return ranked
```

### 5.1 Cohere 路径

**API Key 选择**：`RERANKER_API_KEY` 优先，fallback 到 `LLM_API_KEY`。

**两种调用方式**：

#### (a) HTTP 直连 `_rerank_cohere_http`

当 `RERANKER_BASE_URL` 非空（例如 OpenRouter 兼容端点）：

```python
url = base_url.rstrip("/") + "/rerank"
response = httpx.post(
    url,
    headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    },
    json={
        "model": model,                           # e.g. "cohere/rerank-4-pro"
        "query": query,
        "documents": [d["content"] for d in docs],
        "top_n": top_n,
    },
    timeout=30.0,
    trust_env=False,  # ← 避免系统代理污染
)
data = response.json()
return [{**docs[r["index"]], "score": r["relevance_score"]} for r in data["results"]]
```

#### (b) 官方 SDK `_rerank_cohere`

无 `RERANKER_BASE_URL` 时：

```python
client = _get_cohere_client(api_key)  # cohere.ClientV2 单例
response = client.rerank(model=model, query=query, documents=[...], top_n=top_n)
return [{**docs[r.index], "score": r.relevance_score} for r in response.results]
```

**单例管理**：`_cohere_client` + `_cohere_client_key`，API key 变更时通过 DCL 重建。

**返回语义**：Cohere `relevance_score ∈ [0, 1]`，可直接与 `RERANKER_MIN_SCORE=0.15` 比较。

### 5.2 BGE 本地路径

```python
from sentence_transformers import CrossEncoder
_reranker_model = CrossEncoder("BAAI/bge-reranker-v2-m3")  # 单例

pairs = [(query, doc["content"]) for doc in docs]
scores = model.predict(pairs)
ranked = sorted(zip(scores, docs), key=lambda x: x[0], reverse=True)
return [{**doc, "score": float(score)} for score, doc in ranked[:top_n]]
```

**特征**：
- Cross-Encoder（不是 bi-encoder）：对每个 `(query, doc)` 对做一次前向，精度高于纯向量相似度。
- 单例模型线程安全加载。
- **分数是 logit（未归一化）**，范围远超 [0,1]，与 Cohere **不可直接比较**。`RERANKER_MIN_SCORE` 对 BGE 通常需要重新校准（见 §12）。

### 5.3 降级行为

所有异常场景（库未装、密钥缺失、网络错误、API 限流）：

```python
except Exception:
    logger.warning("... rerank failed", exc_info=True)
    return docs  # 原序返回
```

**没有熔断** —— 主流程继续，但 LLM 拿到的是未重排的 over-fetched 结果（通常质量下降）。

**可观测性缺口**：`PipelineResult` 目前不携带 `"reranker_used": true/false` 字段，调用方无法感知降级。优化建议见 §12。

### 5.4 与检索层的配合

| 参数 | 默认 | 作用 |
| --- | --- | --- |
| `RAG_RERANK_FETCH_MULTIPLIER` | 3 | 向量库取 `top_k * 3` 候选给 reranker |
| `RERANKER_TOP_N` | 5 | rerank 后保留的文档数 |
| `RERANKER_MIN_SCORE` | 0.15 | 分数下限（对 BGE 无意义） |

典型效果：`top_k=5, multiplier=3 → fetch 15 → rerank 保留 5 → 近重复抑制`。

---

## 6. 后处理：去重 & 上下文装配

### 6.1 近重复抑制 `suppress_near_duplicates`

位置：`chain/_steps_retrieve.py`（函数 `suppress_near_duplicates`，阈值常量 `_CONTENT_SIMILARITY_THRESHOLD`）

```python
_CONTENT_SIMILARITY_THRESHOLD = 0.85

kept, kept_ngrams = [], []
for doc in ctx.docs:
    ngrams = _ngrams(doc["content"], n=4)  # 4-gram 集合
    is_dup = any(
        (len(ngrams & existing) / min(len(ngrams), len(existing))) >= 0.85
        for existing in kept_ngrams
        if ngrams and existing
    )
    if not is_dup:
        kept.append(doc)
        kept_ngrams.append(ngrams)

ctx.docs = kept
```

**算法**：
- 按 reranker 输出顺序遍历（高分优先）
- 对每个文档生成字符级 4-gram 集合
- 若与已保留文档的 n-gram 重叠率 ≥ 0.85 → 丢弃
- 否则保留

**目的**：同一话题可能在 transcript 中反复出现，rerank 会把相似段落都推到前列；抑制后可以让 LLM 看到更多独立信息，不浪费 context token。

**注意**：代码中另有常量 `_MMR_LAMBDA = 0.7` 但 **未实际使用**，是残留/待实现的 MMR（Maximum Marginal Relevance）占位。

### 6.2 上下文装配 `_steps_context.py`

按顺序注入多种上下文（在走完整 RAG 管线、即 `_classify_intent` 为 `rag` 时执行）：

1. **长期记忆**（`MemoryService.search_semantic` 等，经 `load_memories`）— 基于用户 + query 的语义召回
2. **知识图谱实体**（`KnowledgeGraphService.get_entity_context`）— query 中的命名实体 & 相关关系
3. **Session 摘要**（`SessionSummaryService.search`）— 跨 session 的 episodic memory
4. **Web 搜索结果**（`search.py`）— 可选外部信息
5. **对话历史** — 最近 N 条消息

最终由 `_steps_generate.py:build_context()` 组装成 prompt。每一项都是 best-effort，失败不影响生成。

### 6.3 生成 `_steps_generate.py`

- 构造 `ChatPromptTemplate`，注入：`{context}`、`{chat_history}`、`{memories}`、`{entities}`、`{session_summaries}`、`{web_results}`、`{question}`
- LCEL chain：`prompt | llm | StrOutputParser()`
- 流式：通过 `StreamBus` 推送 `step`、`token`、`sources`、`trace`、`web_results`、`error`、`done` 事件
- 后台任务：`asyncio.create_task(extract_facts(...))` 把对话中的新事实落入 memory

---

## 7. 查询改写与自适应 top-k

### 7.1 查询改写 `rag/_query.py:rewrite_query`

```python
async def rewrite_query(question: str) -> str:
    if _is_simple_query(question):
        return question  # 短 & 无代词 → 跳过

    llm = _get_rewrite_llm() or get_llm()
    prompt = ChatPromptTemplate.from_messages([("human", _QUERY_REWRITE_PROMPT)])
    response = await asyncio.to_thread(cached_retry_invoke, llm, prompt.format_messages(query=question))
    return response.content.strip()
```

**Prompt**：

```
Rewrite the query to improve document retrieval quality.
- If the query is in Chinese, include relevant English technical terms.
- Expand abbreviations and acronyms.
- Add synonymous phrasings that might match the document language.
- Keep the core intent unchanged.
- Return ONLY the rewritten query, nothing else.
```

**跳过规则 `_is_simple_query`**：

```python
_REWRITE_MAX_TOKENS = 6  # 6 词以内视为简单
_ANAPHORA_PATTERN = re.compile(r"\b(it|that|this|they|them|these|those|the above|the previous|the last)\b", re.IGNORECASE)

def _is_simple_query(question: str) -> bool:
    return len(question.split()) <= 6 and not _ANAPHORA_PATTERN.search(question)
```

长问题或含代词（可能需要上下文消解） → 改写；短关键词搜索 → 直接用。

**单例 rewrite LLM**：`_get_rewrite_llm()` 使用独立的轻量模型（`QUERY_REWRITE_MODEL`，例如 `gpt-4o-mini`），避免为 rewrite 这种低复杂任务占用主 LLM 的配额。

### 7.2 自适应 top-k `determine_adaptive_top_k`

```python
_COMPLEXITY_KEYWORDS = {
    "how many", "compare", "analyze", "list all", "summary of",
    "relationship between", "difference between", "why did",
    "what caused", "step by step", "explain",
    "all of the", "what is the", "what are the",
    "could you", "can you",
}
_SIMPLE_QUESTION_MIN_CHARS = 30

def determine_adaptive_top_k(question: str, user_requested_k: int | None) -> int:
    if user_requested_k is not None:
        return user_requested_k
    q = question.lower().strip()
    if len(q) < 30 and not any(kw in q for kw in _COMPLEXITY_KEYWORDS):
        return 3  # 简单事实问答
    return 8      # 枚举/对比/分析
```

用户显式传 `top_k` 时总是优先；否则按启发式规则决定。

---

## 8. 配置参考表

| 参数 (YAML key) | 默认值 | 类型 | 说明 |
| --- | --- | --- | --- |
| **切片** | | | |
| `rag.chunk_size` | 1024 | int | 扁平切片字符数上限 |
| `rag.chunk_overlap` | 128 | int | 相邻块重叠字符数 |
| `rag.child_chunk_size` | 256 | int | parent-child 模式子块大小 |
| `rag.child_chunk_overlap` | 32 | int | 子块重叠 |
| `rag.parent_child_enabled` | false | bool | 启用父子双层切片 |
| `rag.semantic_chunking_enabled` | false | bool | 启用结构感知切片 |
| **检索** | | | |
| `rag.distance_metric` | l2 | str | `l2` / `cosine` |
| `rag.top_k` | 5 | int | 最终进入 LLM 的块数 |
| `rag.score_threshold` | 1.5 | float | 向量距离/相似度阈值（按度量含义不同） |
| `rag.hybrid_search_enabled` | false | bool | 向量 + BM25 + RRF |
| `rag.hybrid_alpha` | 0.5 | float | RRF 向量权重（1-α 为 BM25） |
| `rag.rerank_fetch_multiplier` | 3 | int | 向量库过取倍数 |
| `rag.multi_query_enabled` | false | bool | 启用多查询扩展 |
| `rag.multi_query_count` | 3 | int | 生成的查询变体数 |
| `rag.query_rewrite_enabled` | false | bool | 启用 LLM 查询改写 |
| `rag.query_rewrite_model` | `""` | str | 改写专用轻量模型 |
| `rag.retriever_provider` | `native` | str | `native` / `hybrid` / `multimodal` / `hybrid_multimodal` |
| `rag.raganything_enabled` | false | bool | 启用 RAGAnything 多模态检索 |
| `rag.raganything_fallback_to_native` | true | bool | RAGAnything 失败时降级到 native |
| `rag.raganything_working_dir` | `""` | str | RAGAnything 存储目录（空则用 `data/raganything/`） |
| `rag.raganything_index_timeout_seconds` | 120.0 | float | 索引超时 |
| `rag.raganything_query_timeout_seconds` | 30.0 | float | 查询超时 |
| `rag.raganything_llm_timeout_seconds` | 90.0 | float | LLM 调用超时 |
| `rag.index_tables` | true | bool | 索引表格内容 |
| `rag.index_image_captions` | true | bool | 索引图片描述 |
| `rag.image_ocr_min_length` | 15 | int | OCR 文本最小长度（短于此则不索引） |
| `rag.content_type_rerank_enabled` | true | bool | 按内容类型重排 |
| `rag.sibling_coretrieve_enabled` | true | bool | 启用兄弟 chunk 协同检索 |
| `rag.sibling_coretrieve_per_anchor` | 1 | int | 每锚点拉取兄弟数 |
| `rag.sibling_coretrieve_max_total` | 4 | int | 兄弟总数上限 |
| `rag.memory_context_max_tokens` | 800 | int | 记忆上下文 token 预算 |
| `rag.entity_context_max_tokens` | 600 | int | 实体上下文 token 预算 |
| `rag.session_context_max_tokens` | 800 | int | 会话上下文 token 预算 |
| `rag.context_load_timeout_s` | 3.0 | float | 上下文加载超时（秒） |
| `rag.skill_match_timeout_s` | 10.0 | float | 技能匹配超时（秒） |
| **重排** | | | |
| `rag.reranker_binding` | `""` | str | `cohere` / `bge` / `""`(禁用) |
| `rag.reranker_model` | `cohere/rerank-4-pro` | str | 模型名 |
| `rag.reranker_api_key` | `""` | str (secret) | 从 env 读取 |
| `rag.reranker_base_url` | `""` | str | HTTP 端点（空则走 SDK） |
| `rag.reranker_top_n` | 5 | int | rerank 后保留块数 |
| `rag.reranker_min_score` | 0.15 | float | rerank 最低分（BGE 下需重新校准） |

---

## 9. 场景配置模板

### 9.1 极简（开发/测试）

```yaml
rag:
  chunk_size: 1024
  chunk_overlap: 128
  top_k: 5
  distance_metric: l2
  reranker_binding: ""
  hybrid_search_enabled: false
  semantic_chunking_enabled: false
  parent_child_enabled: false
```

特点：最快、最省钱；质量不稳定。用于本地调试。

### 9.2 均衡（生产推荐）

```yaml
rag:
  chunk_size: 1024
  chunk_overlap: 128
  top_k: 5
  distance_metric: cosine
  reranker_binding: "cohere"
  reranker_top_n: 5
  reranker_min_score: 0.15
  hybrid_search_enabled: false
  semantic_chunking_enabled: false
  parent_child_enabled: false
```

特点：向量 + Cohere 重排双保险；延迟可控，质量稳定。

### 9.3 高精度（长文档 / 复杂业务）

```yaml
rag:
  chunk_size: 512
  chunk_overlap: 64
  child_chunk_size: 256
  child_chunk_overlap: 32
  top_k: 5
  distance_metric: cosine
  reranker_binding: "bge"
  reranker_top_n: 5
  hybrid_search_enabled: true
  hybrid_alpha: 0.5
  semantic_chunking_enabled: true
  parent_child_enabled: true
  multi_query_enabled: true
  multi_query_count: 3
  query_rewrite_enabled: true
```

特点：全功能启用，召回与精度都最高；成本高、延迟大，用于关键业务。建议离线跑 `scripts/benchmark rag-all` 验证增益。

---

## 10. 性能特征

| 配置 | 索引时间 | 查询延迟 | Embedding 成本 | Rerank 成本 |
| --- | --- | --- | --- | --- |
| 极简 | 快 | 最快 | 低 | 0 |
| 均衡 (+ Cohere) | 快 | 中 | 低 | 中（API 调用） |
| 高精度（全启用） | 慢（parent-child ≈ 2×） | 慢（Hybrid + RRF + Multi-Query） | 高 | 高（BGE 本地 GPU 或 Cohere） |

**典型瓶颈**：
- **索引**：embedding 远比 chunking 慢。Parent-child 翻倍 embedding 量是最大放大器。
- **查询**：Multi-query 会把检索时间 ×4；rerank（特别是 BGE CPU 推理）是首 token 前的主要延迟来源。
- **LLM 生成**：在 top_k 较小时，rerank 后进入 LLM 的 token 量与生成速度线性相关。

---

## 11. 故障排查

### 检索结果空或不相关

1. `score_threshold` 是否过严？L2 模式下 1.5 对 768 维 embedding 已偏严。
2. 开启 parent-child 时子块是否太小（< 128）导致碎片化？
3. 试试 `hybrid_search_enabled=true` 让 BM25 补充关键词匹配。
4. 开 `multi_query_enabled` 扩展查询表达。
5. 看 trace 中 `retrieve` span 的 `docs_retrieved`，判断是过滤器过严还是向量根本没召回。

### Reranker 无效果或出错

- Cohere：
  - 检查 `RERANKER_API_KEY` 或 `LLM_API_KEY` 是否设置
  - 检查配额、网络、`RERANKER_BASE_URL` 是否正确
  - 日志里搜 `Cohere rerank failed` / `Cohere rerank HTTP failed`
- BGE：
  - `pip install sentence-transformers`
  - 首次启动会下载 `BAAI/bge-reranker-v2-m3`（~2GB）
  - CPU 下每次查询 ~秒级延迟；推荐 GPU
  - `RERANKER_MIN_SCORE=0.15` 对 BGE 无意义，建议设为 0 或做 sigmoid 归一化
- 通用：reranker 失败会 **静默降级**，关键指标要看日志

### Embedding 费用过高

- 关闭 `parent_child_enabled`（embedding 量减半）
- 加大 `chunk_size`（减少块数）
- 确认 `_dedup_existing_chunks` 生效（日志 `all N chunks unchanged, skipping`）
- 切换到本地 embedding 提供商（Ollama / HuggingFace）

### 查询延迟高

- 关 `multi_query_enabled`（省 LLM 变体生成 + 3× 检索）
- 关 `hybrid_search_enabled`（省 FTS5 查询 + RRF 合并）
- 降低 `top_k` 与 `reranker_top_n`
- BGE → Cohere（把 CPU 推理换成网络 IO，大多数场景更快）

---

## 12. 优化方向（持续演进）

按 **影响 × 成本** 排序；每项均标注当前代码位置，便于落地。

### 12.1 Chunking & 索引

1. **按 token 而非字符切分**
   - 现状：`CHUNK_SIZE=1024` 是字符数，中文 1 token ≈ 1.5–2 字符，英文 ≈ 4 字符，同一 chunk_size 在不同语言下表现不稳定。
   - 建议：复用 `services/tokenizer.py`（tiktoken 单例），用 `TokenTextSplitter` 或自研 token-aware splitter。

2. **默认开启结构感知 / parent-child**
   - 现状：`SEMANTIC_CHUNKING_ENABLED` / `PARENT_CHILD_ENABLED` 默认 `False`。
   - 建议：跑 `scripts/benchmark rag-all` 对比，如果召回/答案质量显著优于 flat，就改默认值，并在 `config/main.yaml` 显式注释。

3. **利用 speaker / 静音边界**
   - `transcriber.py` 的 timestamped 转写带 **speaker 变更** 与 **静音间隔**，是比文本正则更强的 topic 边界。
   - 建议：在 `processor/_pipeline.py` 里把 speaker 切换点作为 hard boundary 传给 chunker。

4. **去重哈希纳入更多维度**
   - `_dedup_existing_chunks` 仍只对 `page_content` 做短哈希；`chunk_id` 已含 `file_id`，跨文件碰撞已缓解。
   - 若仍出现“元数据变但正文未变”导致的陈旧向量，可考虑把关键 metadata 片段拼入哈希输入。

5. **Per-file BM25 清理**
   - `_remove_from_bm25` 只按 `meeting_id` 清；`delete_meeting_chunks(meeting_id, file_id=...)` 会让 BM25 残留其他文件的旧条目。
   - 建议：在 `bm25_index` 表加 `file_id` 列，按 `(meeting_id, file_id)` 精确删除。

6. **BM25 侧支持 parent-child / 结构切分**
   - 现状 BM25 永远走 flat。对于开了 parent-child 的库，这意味着 hybrid 检索的两路粒度不一致，RRF 合并时 dedup 失效。
   - 建议：让 BM25 也用 child 粒度（复用 `_index_parent_child` 产生的子块）。

### 12.2 Retrieval

1. **RRF `k=60` 硬编码**
   - 不同数据规模下最优 k 不同，挪到 `settings.RRF_K`。

2. **动态 `HYBRID_ALPHA`**
   - 短关键词 query 应偏 BM25（`α < 0.5`），长自然语言应偏向量（`α > 0.5`）。
   - 建议：`determine_hybrid_alpha(question)`，基于 query 长度/语言/关键词密度。

3. **Multi-Query 与 Hybrid 互斥假设松动**
   - BM25 对同义改写几乎不敏感，multi-query 的收益主要来自 **向量通道的改写**。
   - 建议：multi-query 只对向量通道展开（向量查 4 次、BM25 查 1 次），最后统一 RRF。

4. **严格过滤无结果时的兜底**
   - `meeting_ids / file_types / date_range` 过严时向量召回可能为空，目前直接返回空。
   - 建议：空结果时放宽过滤重试一次，并在 trace 中标注 `filter_relaxed=True`。

5. **MMR 真落地或删除死代码**
   - `_steps_retrieve.py` 有常量 `_MMR_LAMBDA = 0.7` 但没用。
   - 建议：要么实现真正 MMR（向量空间多样性），要么删除常量避免误导。

### 12.3 Rerank

1. **BGE 分数校准**
   - Cohere 返回 `[0,1]`；BGE 返回 logit（无上界）。`RERANKER_MIN_SCORE=0.15` 对 BGE 几乎过滤不到任何文档。
   - 建议（任选其一）：
     - 对 BGE 分数做 `sigmoid` 归一化
     - 或为两种后端分别配置：`RERANKER_MIN_SCORE_BGE`、`RERANKER_MIN_SCORE_COHERE`

2. **BGE 批量推理优化**
   - `CrossEncoder.predict(pairs)` 默认逐对；GPU 上应显式 `batch_size`、`show_progress_bar=False`，并在初始化时 `.to("cuda").eval()`。

3. **两阶段 rerank**
   - 先用便宜的 bi-encoder 粗排到 ~20 条，再喂 cross encoder，可以显著降低 fetch_multiplier 较大时的延迟。

4. **Rerank 结果缓存**
   - 同一 query + 同一候选集（尤其 streaming 重试）rerank 结果值得缓存。
   - 建议：`md5(query + sorted(doc_ids))` 作为 key，LRU 到内存。

5. **HTTP 客户端复用**
   - `_rerank_cohere_http` 每次调用都 `httpx.post`，没有连接池复用。
   - 建议：参考 `services/search.py` 的 `httpx.AsyncClient` 单例模式。

6. **`reranker_used` 可观测性**
   - `PipelineResult` 缺 `reranker_used` / `reranker_backend` / `reranker_latency_ms` 字段，用户侧无法感知降级。
   - 建议：在 `chain/_context.py:PipelineResult` 增加这些字段，并在流式 `trace` 事件中输出。

### 12.4 Query Rewrite & Routing

1. **Rewrite 与预取并行**
   - `rewrite_query` 是顺序执行，首 token 延迟包含完整 rewrite 时间。
   - 建议：流式场景下把 rewrite 与 “会议元数据预取 / warmup 向量库” 并行。

2. **HyDE (Hypothetical Document Embeddings)**
   - 对长难问题，用 LLM 先生成 “假想答案” 再做向量检索，通常比 query 改写更有效。
   - 实现成本低：新 step `_steps_hyde.py`，在 rewrite 之后、retrieve 之前。

3. **意图分类细化**
   - `_routing.py` 只分 casual / retrieval；再拆 “列表枚举 / 事实问答 / 摘要” 三类，各自套不同的 top_k / prompt / rerank_top_n。

### 12.5 上下文装配

1. **Token 预算感知的 pack**
   - `_steps_generate.py` 按文档条数截断，不是 token。
   - 建议：用 `tokenizer.count_tokens()` 做 prompt 预算管理，尽量装满 `max_tokens - output_budget`。

2. **Citations / Source grounding**
   - 现在 `sources` 只返回 metadata，没有句级 citation。
   - 建议：让 LLM 输出 `[source_i]` 引用，前端 `ChatPanel` 高亮对应 chunk。

3. **长上下文去冗余升级**
   - parent-child 下 `_resolve_parent_chunks` 已合并同父；但跨 meeting 的相似 parent 仍可能冗余。
   - 建议：在 `suppress_near_duplicates` 之前多做一次 parent 级合并。

### 12.6 评测与可观测性

1. **把 rerank 指标落到 benchmark**
   - `scripts/_bench_rag_quality.py` 已有评测入口；补充 **rerank 贡献度**（rerank 前后 nDCG@k 差值）指标。

2. **Trace 补充字段**
   - 向量 vs BM25 各自命中数
   - rerank 截断前后的 doc 数
   - query rewrite 前后字符串
   - reranker 实际返回的 top_score / min_score
   - 这些落到 trace 成本极低，对调参帮助巨大。

3. **Benchmark 入 CI**
   - 把 `benchmark rag-all` 的核心指标（recall@k / answer faithfulness）作为 CI 的可选 job，阈值退化时 fail PR。避免 “RAG 参数调好就不敢动”。

---

## 13. 一句话总结

当前 RAG 已经覆盖了工业级所需的大部分关键模块 ——
**结构感知 + parent-child chunking、自适应 top-k、Hybrid + RRF、Cohere/BGE 双 reranker、Query Rewrite、Multi-Query、Dedup、Trace**，并在 “best-effort 降级、单例并发安全、确定性 upsert、per-file vector 清理” 等工程细节上很用心。

下一轮优化的重点应从 **“再加功能”** 转向 **“让默认开关真正开、让指标可量化、让 rerank 与 hybrid 动态自适应”** ——结合 benchmark 抓最优配置，再用 CI 兜住质量退化。
