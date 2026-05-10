# RAG Benchmark 测试计划：Chunk 策略 × 检索策略

## 1. 概述

本文档定义了一个两阶段 benchmark，用于系统性地为每种文件模态选择最优的 **Chunk 策略** 与 **检索策略** 组合。

- **Phase 1 — Chunk 策略对比**：固定检索配置为 `Hybrid + Rerank`，对比每种模态下的 chunk 方法，选出 top-2。
- **Phase 2 — 检索策略 Grid Search**：对每种模态的 top-2 chunk 策略，分别在 **Scoped（限定范围）** 和 **Unscoped（Broad Recall）** 场景下进行检索配置的网格搜索。

**为什么分两阶段，而不是一次性做全量网格？**

Chunk 策略决定了索引单元的 *粒度与元数据丰富度*，检索策略决定了 *如何查找* 这些单元。两者之间存在显著的交互效应（例如：BM25 更受益于细粒度 chunk；reranker 的行为会随 chunk 大小变化），但如果在第一阶段就测试所有组合，矩阵规模将不可控。Phase 1 先缩小 chunk 的设计空间，Phase 2 再在该空间内评估交互效应。

---

## 2. Phase 1：Chunk 策略对比

### 2.1 设计原则

- **固定检索配置**：使用 `HybridStrategy` + `Reranker ON`（生产默认配置），以隔离 chunk 本身的影响。
- **固定 Scope 类型**：分别在 `Scoped` 和 `Unscoped` 数据集上测试，最终通过加权平均分数选出 top-2。
- **纯文本变体**：对于结构化模态（视频/音频、文档），需额外生成一条纯文本索引管线，将提取出的 `text` 字段（不含 segment/page 结构）直接送入 `index_meeting()`，与原生 chunk 方法做对比。

### 2.2 模态 → 原生 Chunk 映射

| 模态 | 文件类型 | Processor 输出 | 原生 Chunk 函数 | 原生 Chunk 名称 |
|------|---------|---------------|----------------|----------------|
| **视频 / 音频** | `video`, `audio` | `FileArtefact.segments`（带 speaker/timestamp 的 ASR 转录文本） | `index_meeting_segments()` | **Segment-Aware** |
| **文档** | `pdf`, `ppt`, `doc`, `xls`, `csv` | `FileArtefact.parsed_doc`（按页组织的文本 + 表格 + 图片资源） | `index_meeting_pages()` | **Page-Aware** |
| **图片** | `png`, `jpg`, `jpeg`, `webp` | `FileArtefact.segments`（单条 segment，包含 caption 与 OCR 文本） | `index_meeting_segments()` | **Caption-OCR Flat** |

> **图片说明**：图片 Processor 会生成一个包含 caption 与 OCR 拼接文本的 `segment`。`index_meeting_segments()` 会将其视为一个语义组处理；若文本长度超过 `CHUNK_SIZE`，单 segment 场景下也不会进一步拆分（单 segment 的边界逻辑）。因此图片内容通常被索引为 **1–2 个 chunk**。

### 2.3 每种模态需测试的 Chunk 方法

对每个模态，均测试以下 **3 种 chunk 方法**：

| 编号 | 方法 | 说明 | 实现方式 |
|------|------|------|---------|
| A | **Native（原生）** | 该模态的默认 chunk 路径（见上表） | 使用现有 pipeline 原样执行 |
| B | **Pure-Text Flat（纯文本平铺）** | 提取的纯文本 + `RecursiveCharacterTextSplitter`（`PARENT_CHILD_ENABLED=False`） | 将 `FileArtefact.text`（或 `parsed_doc.to_text()`）送入 `index_meeting()` |
| C | **Pure-Text Parent-Child（纯文本父子）** | 提取的纯文本 + 两层拆分（`PARENT_CHILD_ENABLED=True`） | 将 `FileArtefact.text`（或 `parsed_doc.to_text()`）送入 `index_meeting()` 并启用父子配置 |

### 2.4 参数预设

对每种方法，测试 **3 组参数预设**，覆盖小/中/大三种 chunk 粒度。

#### Flat / Page-Aware / Caption-OCR Flat 参数

| 预设 | `CHUNK_SIZE` | `CHUNK_OVERLAP` | 说明 |
|------|-------------|-----------------|------|
| **S** (Small) | 512 | 64 | 高粒度；chunk 数量多；有利于关键词/BM25 匹配 |
| **M** (Medium) | 1024 | 128 | **当前默认值**；均衡配置 |
| **L** (Large) | 2048 | 256 | 低粒度；chunk 数量少；保留更多 chunk 内上下文 |

#### Parent-Child 参数

| 预设 | Parent `CHUNK_SIZE` | Parent Overlap | Child `CHILD_CHUNK_SIZE` | Child Overlap |
|------|--------------------|----------------|--------------------------|---------------|
| **S** | 1024 | 128 | 256 | 32 | 小 parent，细粒度 child |
| **M** | 1024 | 128 | 256 | 32 | 与 S 相同（parent-child 对 parent size 敏感度较低） |
| **L** | 2048 | 256 | 512 | 64 | 大 parent，大 child；总单元数更少 |

> **说明**：Parent-Child 实际只需测试 **S** 和 **L** 两组（跳过 M），因为 child size 主导了检索粒度，而 parent size 主要影响 context window 的使用。

#### Segment-Aware 参数

| 预设 | `CHUNK_SIZE`（每组上限） | `AUDIO_SEMANTIC_BOUNDARY_ENABLED` | `AUDIO_SEMANTIC_BOUNDARY_THRESHOLD` | 说明 |
|------|------------------------|-----------------------------------|-------------------------------------|------|
| **S** | 512 | `True` | 0.5 | 小分组，严格语义边界 |
| **M** | 1024 | `True` | 0.5 | 默认配置；中等分组，标准边界敏感度 |
| **L** | 2048 | `False` | — | 大尺寸上限，**关闭语义边界**（纯基于大小的分组） |
| **M-Loose** | 1024 | `True` | 0.3 | 更多边界 → 更小的语义组 |
| **M-Strict** | 1024 | `True` | 0.7 | 更少边界 → 更大的语义组 |

> **说明**：Segment-Aware 的核心对比为 **S**、**M**、**L** 三组。**M-Loose** 和 **M-Strict** 为可选项——仅在视频/音频是重点模态且预期边界敏感度会产生显著影响时加入。

### 2.5 Phase 1 测试矩阵汇总

| 模态 | 方法 A（Native） | 方法 B（Flat） | 方法 C（Parent-Child） | 单次运行数 |
|------|-----------------|---------------|----------------------|----------|
| 视频 / 音频 | Segment-Aware × 3–5 组预设 | Flat × 3 组预设 | Parent-Child × 2 组预设 | **8–10** |
| 文档 | Page-Aware × 3 组预设 | Flat × 3 组预设 | Parent-Child × 2 组预设 | **8** |
| 图片 | Caption-OCR × 3 组预设 | Flat × 3 组预设 | Parent-Child × 2 组预设 | **8** |

每组运行均在 **Scoped** 和 **Unscoped** 两份数据集上各执行一次。

### 2.6 Top-2 筛选标准

对每个模态的每种 "chunk 方法 + 预设" 组合，计算：

```
Combined_Recall@10 = 0.5 × Scoped_Recall@10 + 0.5 × Unscoped_Recall@10
```

按 `Combined_Recall@10` 从高到低排序，选取 **top-2** 预设。若两个预设属于同一 **方法**（例如都是 Flat），则只保留该方法中的最优预设，并顺延取不同方法的下一个最优预设。这样可以确保 Phase 2 的网格覆盖多样化的 chunk 架构。

**平局打破规则**：若 Recall@10 相同，优先选择 `File Coverage@10`（仅 Unscoped）更高的方法。

---

## 3. Phase 2：检索策略 Grid Search

### 3.1 为什么在 Phase 1 之后还要做 Grid Search？

Chunk 策略与检索策略**并非独立**：

1. **BM25（Hybrid）受益于粒度**：细粒度的 flat chunk 能产生更多离散的关键词目标，而大段语义 segment 的关键词密度较低。在 Hybrid 下表现优异的 chunk 方法，在 Native 下未必最优。
2. **Reranker 的区分度受 chunk size 影响**：Reranker 基于完整 chunk 文本打分。若 chunk 过大，相关句子可能被淹没在冗余内容中，导致 reranker 分数被拉低，从而影响 cutoff 判断。
3. **Parent-Child 改变了检索单元**：实际被检索的是 child chunk，但 parent chunk 提供扩展上下文。对 child chunk 做 rerank 与对 parent chunk 做 rerank 的效果不同；reranker 对 Parent-Child 的相对价值高于 Flat。
4. **Scope 改变了 Hybrid 的价值**：在 Scoped 模式下候选池很小（仅几十个 chunk），Native 与 Hybrid 差异不大；在 Unscoped Broad Recall 下候选池是全局的（数千个 chunk），BM25 的关键词精确匹配价值被放大。

因此，不能假设在 `Hybrid+Rerank` 下最优的 chunk 策略，在 `Native+Rerank` 或 `Hybrid`（无 rerank）下依然最优。必须联合优化。

### 3.2 Scoped Grid Search

对每个模态，取其 **Phase 1 top-2 chunk 策略**，运行以下组合：

| 检索策略 | Reranker | 配置项 | 说明 |
|---------|----------|--------|------|
| `NativeStrategy` | OFF | `RAG_RETRIEVER_PROVIDER=native`, `RERANKER_BINDING=""` | 纯向量基线 |
| `NativeStrategy` | ON | `RAG_RETRIEVER_PROVIDER=native`, `RERANKER_BINDING=cohere/bge` | 向量 + rerank |
| `HybridStrategy` | OFF | `RAG_RETRIEVER_PROVIDER=hybrid`, `RERANKER_BINDING=""` | 向量 + BM25（RRF 融合） |
| `HybridStrategy` | ON | `RAG_RETRIEVER_PROVIDER=hybrid`, `RERANKER_BINDING=cohere/bge` | **生产默认配置** |

**执行方式**：调用检索时显式传入 `meeting_ids=[mid]`（或 `file_ids=[fid]`），强制进入 Scoped 检索路径。

**每轮记录指标**：
- `Recall@10`
- `MRR`
- `NDCG@10`

### 3.3 Unscoped（Broad Recall）Grid Search

使用与 Scoped 相同的 **top-2 chunk 策略** 和相同的 **4 种检索×rerank 组合**。

**Unscoped 关键配置**（需保持固定）：
- `RAG_SUMMARY_ROUTER_ENABLED=True`（生产默认）
- `RAG_FAIR_ADAPTIVE_CHUNKS=True`
- `RAG_HIERARCHICAL_ENABLED=True`（funnel 逻辑在 Broad Recall 中因 `file_ids` 被指定而被短路，但仍保持开启以模拟生产环境）

**执行方式**：调用检索时**不传入** `meeting_ids` 或 `file_ids`，触发完整的 Broad Recall 路径：
1. Summary Router 筛选候选文件
2. `fair_retrieve_per_file()` 对每个文件独立检索
3. Over-fetch 机制生效（`per_file_fetch = max(budget*2, budget+2)`）
4. Per-file guarantee 截断生效（`min_floor = max(top_k, distinct_files)`）
5. 全局 rerank 与去重

**每轮记录指标**：
- `Recall@10`
- `MRR`
- `NDCG@10`
- **`File Coverage@10`**：包含 golden chunk 的文件中，有多大比例出现在 top-10 结果里？
- **`Per-File Recall Mean`**：各相关文件的 Recall@10 均值（公平性指标）

### 3.4 Phase 2 Grid Search 表格模板

以某模态的 top-2 chunk 策略 `{C1, C2}` 为例：

| Chunk 策略 | 检索策略 | Rerank | Scoped Recall@10 | Scoped MRR | Scoped NDCG | Unscoped Recall@10 | Unscoped MRR | Unscoped NDCG | Unscoped File Coverage | **综合得分** |
|-----------|---------|--------|------------------|------------|-------------|--------------------|--------------|---------------|------------------------|-------------|
| C1 | Native | OFF | | | | | | | | |
| C1 | Native | ON | | | | | | | | |
| C1 | Hybrid | OFF | | | | | | | | |
| C1 | Hybrid | ON | | | | | | | | |
| C2 | Native | OFF | | | | | | | | |
| C2 | Native | ON | | | | | | | | |
| C2 | Hybrid | OFF | | | | | | | | |
| C2 | Hybrid | ON | | | | | | | | |

**每模态最终选择**：按以下公式计算综合得分，取最高者：

```
综合得分 = 0.4 × Unscoped_Recall + 0.3 × Scoped_Recall + 0.2 × File_Coverage + 0.1 × NDCG
```

> 权重可根据业务优先级调整。若系统以 Scoped 查询为主，可提高 `Scoped_Recall` 权重。

---

## 4. Phase 1 → Phase 2 流转流程

```
┌─────────────────────────────────────────────────────────────┐
│  PHASE 1: Chunk 策略对比                                      │
│  固定条件: Hybrid + Rerank ON                                 │
│  数据集: golden_set_scoped.json + golden_set_unscoped.json    │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
        ▼                     ▼                     ▼
   ┌─────────┐          ┌─────────┐          ┌─────────┐
   │ 视频    │          │ 文档    │          │ 图片    │
   │ Top-2   │          │ Top-2   │          │ Top-2   │
   │ Chunk   │          │ Chunk   │          │ Chunk   │
   └────┬────┘          └────┬────┘          └────┬────┘
        │                     │                     │
        └─────────────────────┼─────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  PHASE 2: 检索策略 Grid Search                                │
│  变量: Native/Hybrid × Rerank ON/OFF                         │
│  数据集: golden_set_scoped.json + golden_set_unscoped.json    │
│  每模态输出: 最优 (Chunk + 检索 + Rerank) 组合                │
└─────────────────────────────────────────────────────────────┘
```

### 4.1 为什么不能直接分别选最优 Chunk 和最优检索，再组合？

一种直觉上的捷径是：
1. 先找到最优 chunk 策略（Phase 1）。
2. 再独立找到最优检索策略。
3. 将两者直接组合。

这种做法**无效**，原因在于 **交互效应**：

| 交互效应 | 说明 |
|---------|------|
| **Chunk Size × Hybrid** | BM25 关键词匹配在 chunk 小而聚焦时效果最好。2048 字符的大 chunk 可能在 Native 下表现不错（embedding 上下文更完整），但在 Hybrid 下会失败，因为 BM25 无法在巨大 chunk 中精确定位关键词。 |
| **Chunk Size × Reranker** | Reranker 基于完整 chunk 文本打分。若 chunk 过大，相关句子可能被埋没，导致 reranker 分数下降，在 cutoff 处产生假阴性。 |
| **Segment-Aware × Native** | Segment-Aware chunk 保留了 speaker label 和 timestamp。这种结构性元数据有助于 embedding 相似度计算（speaker/时间类查询），但对纯关键词查询（BM25）可能引入噪音。 |
| **Parent-Child × Reranker** | Parent-Child 检索的是 child chunk（小粒度），但 parent 提供扩展上下文。没有 reranker 时 child chunk 可能显得过短；有 reranker 时 child 文本可被精确打分。reranker 对 Parent-Child 的相对价值高于 Flat。 |
| **Scope × 策略** | Scoped 模式下候选池很小（仅几十个 chunk），Native 与 Hybrid 差异不大。Unscoped Broad Recall 下候选池是全局的（数千个 chunk），Hybrid 的关键词精确匹配价值被显著放大。"最优"检索策略取决于 scope 类型。 |

**结论**：必须联合优化 chunk 与检索。Phase 1 缩小 chunk 搜索空间；Phase 2 在该空间内评估交互效应。

---

## 5. Benchmark 数据集规范

### 5.1 数据集拆分要求

需要准备**两份独立的 golden set**。切勿将 scoped 与 unscoped 查询混合在同一文件中。

### 5.2 `golden_set_scoped.json`

每个查询仅针对 **一个已知文件/会议**，golden chunk 必须位于该 scope 内。

```json
{
  "version": 1,
  "scope_type": "scoped",
  "items": [
    {
      "id": "scoped_q001",
      "query": "Alice 在 Q1 planning meeting 中关于预算说了什么？",
      "fixture_file": "sample.pdf",
      "meeting_id": 42,
      "file_id": 101,
      "expected_chunks": [
        "meeting_42_file_101_chunk_3",
        "meeting_42_file_101_chunk_4"
      ],
      "expected_file_ids": [101],
      "expected_answer": "Alice 表示预算需要增加 20%。"
    }
  ]
}
```

### 5.3 `golden_set_unscoped.json`

每个查询**不指定范围**。答案可能跨越多个文件/会议。必须显式记录哪些文件是相关的，以便计算 File Coverage。

```json
{
  "version": 1,
  "scope_type": "unscoped",
  "items": [
    {
      "id": "unscoped_q001",
      "query": "哪些会议讨论了预算增加？",
      "expected_chunks": [
        "meeting_42_file_101_chunk_3",
        "meeting_43_file_102_chunk_1"
      ],
      "expected_file_ids": [101, 102],
      "expected_meeting_ids": [42, 43],
      "expected_answer": "Q1 planning meeting 和 all-hands meeting 都讨论了预算增加。"
    }
  ]
}
```

### 5.4 数据集规模建议

| 模态 | Scoped 查询数 | Unscoped 查询数 | 说明 |
|------|-------------|----------------|------|
| 视频 / 音频 | 8–12 | 4–6 | 需包含 speaker-specific 和 temporal 查询 |
| 文档 | 12–16 | 6–10 | 混合单页细节查询与跨页综合查询 |
| 图片 | 4–6 | 2–4 | 聚焦 caption-based 和 OCR-based 查询 |

**最低可行规模**：每模态每 scope 类型至少 5 条查询。查询数量越多，统计稳定性越好。

### 5.5 Chunk ID 命名规范与歧义消除

#### 为什么必须使用完整 Chunk ID？

系统内实际的 chunk ID 由 `_chunk_id_prefix(meeting_id, file_id)` 生成，规则如下：

- 有 `file_id` 时：`meeting_{meeting_id}_file_{file_id}_chunk_{index}`
- 无 `file_id`（legacy 单文件 meeting）时：`meeting_{meeting_id}_chunk_{index}`

在当前的 multi-file 架构下，**绝大多数 chunk ID 都包含 `file_id`**。如果 `golden_set` 中只写 `"chunk_3"`，再由 benchmark 脚本拼接为 `meeting_42_chunk_3`，则与实际 ID `meeting_42_file_101_chunk_3` **无法匹配**，会导致 recall 计算恒为 0。

此外，在 Unscoped 场景下，一个查询的答案可能分布在多个 `meeting_id` + `file_id` 组合中，仅写 `"chunk_3"` 完全无法区分归属。

#### 完整 Chunk ID 示例

| 场景 | 旧写法（有歧义） | 新写法（完整 ID） |
|------|----------------|----------------|
| Scoped，单文件 | `["chunk_3"]` | `["meeting_42_file_101_chunk_3"]` |
| Unscoped，跨文件 | `["chunk_3", "chunk_1"]` | `["meeting_42_file_101_chunk_3", "meeting_43_file_102_chunk_1"]` |
| Legacy 无 file_id | `["chunk_0"]` | `["meeting_42_chunk_0"]` |

#### 如何确定完整 Chunk ID？

在标注 golden set 前，先对测试数据执行一次索引，然后查询 vectorstore 或 `bm25_index` 表获取实际 chunk ID：

```python
from src.services.rag._vectorstore import get_vectorstore
vs = get_vectorstore()
results = vs.get(where={"meeting_id": 42}, include=["metadatas"])
for cid, meta in zip(results["ids"], results["metadatas"]):
    print(cid, meta.get("content_type"), meta.get("file_id"))
```

或在 SQLite 中直接查询：

```sql
SELECT chunk_id, meeting_id, metadata FROM bm25_index WHERE meeting_id = 42;
```

#### Benchmark 脚本修改要点

现有 `scripts/benchmark.py` 中的匹配逻辑需删除前缀拼接，改为直接比较完整 ID：

```python
# 旧的拼接逻辑（需删除）
# expected = {f"meeting_{meeting_id}_{chunk}" for chunk in item.get("expected_chunks", [])}

# 新的直接比较逻辑
expected = set(item.get("expected_chunks", []))
```

同时，检索结果中的 chunk ID 也应直接取自 `metadata.chunk_id`（若存在）或根据返回结果的 `id` 字段直接比较，避免再次拼接。

---

## 6. 实现说明

### 6.1 如何生成纯文本变体

对每个 fixture 文件，在正常处理完成后，提取其纯文本并重新调用 `index_meeting()` 进行索引：

| 模态 | 源文本 | 索引函数 | 关键配置 |
|------|--------|---------|---------|
| 视频/音频 | `FileArtefact.text`（带 speaker 的转录文本） | `index_meeting()` | `PARENT_CHILD_ENABLED=False/True` |
| 文档 | `parsed_doc.to_text()`（拼接后的各页文本） | `index_meeting()` | `PARENT_CHILD_ENABLED=False/True` |
| 图片 | `FileArtefact.text`（caption + OCR） | `index_meeting()` | `PARENT_CHILD_ENABLED=False/True` |

> **注意**：测试纯文本变体时，务必先删除该文件的原生索引（或使用独立的临时向量库），避免不同 chunk 策略的索引数据相互污染。

### 6.2 Benchmark Runner 的扩展

现有 `scripts/benchmark.py` 仅测试 **Scoped** 检索（`meeting_ids=[meeting_id]`）。你需要扩展以支持 Unscoped：

```python
# Unscoped 检索调用（触发 Broad Recall）
results = retrieve(
    query,
    meeting_ids=None,   # 不指定范围
    file_ids=None,      # 不指定范围
    top_k=top_k,
)
```

或使用完整 pipeline：

```python
from src.services.chain import ask
result = await ask(question=query, user_id="benchmark")  # 不传入 meeting_ids
```

### 6.3 单次运行中的配置锁定

为确保公平对比，单轮 benchmark 中需锁定以下配置：

| 配置项 | Phase 1 取值 | Phase 2 取值 |
|--------|-------------|-------------|
| `TOP_K` | 10 | 10 |
| `RERANKER_TOP_N` | 10 | 10 |
| `HYBRID_ALPHA` | 0.5 | 0.5 |
| `RAG_RERANK_FETCH_MULTIPLIER` | 6 | 6 |
| `RAG_SUMMARY_ROUTER_ENABLED` | True | 按需求 |
| `RAG_FAIR_ADAPTIVE_CHUNKS` | True | 按需求 |
| `QUERY_REWRITE_ENABLED` | False | False |

> **为什么要关闭 query rewrite？** Query rewrite 引入了 LLM 调用，可能改变查询语义。在检索 benchmark 中，应测量检索系统回答 *原始问题* 的能力，排除 query rewrite 的干扰。

---

## 7. 执行清单

### Phase 1 清单

- [ ] 准备 `golden_set_scoped.json`，包含各模态的 scoped 查询
- [ ] 准备 `golden_set_unscoped.json`，包含跨文件的 unscoped 查询
- [ ] 为每模态生成 3 种 chunk 方法 × 参数预设的组合
- [ ] 在 Scoped 数据集上运行所有组合（Hybrid + Rerank）
- [ ] 在 Unscoped 数据集上运行所有组合（Hybrid + Rerank）
- [ ] 计算 Combined Recall 并选出每模态的 top-2

### Phase 2 清单

- [ ] 为每模态的 top-2 配置 4 种检索×rerank 组合
- [ ] 执行 Scoped Grid Search，记录 Recall/MRR/NDCG
- [ ] 执行 Unscoped Grid Search，记录 Recall/MRR/NDCG + File Coverage + Per-File Recall
- [ ] 按加权综合得分选出每模态的最终最优配置
- [ ] （可选）在最终选定配置上运行 `rag-snapshot`，建立回归基线

---

## 8. 预期输出示例

### Phase 1 输出示例（视频/音频）

| 排名 | 方法 | 预设 | Scoped Recall@10 | Unscoped Recall@10 | Combined | 是否入选 |
|------|------|------|------------------|--------------------|----------|---------|
| 1 | Segment-Aware | M | 0.82 | 0.71 | **0.765** | ✅ Top-1 |
| 2 | Pure-Text Flat | S | 0.78 | 0.68 | 0.730 | ✅ Top-2 |
| 3 | Segment-Aware | L | 0.75 | 0.70 | 0.725 | — |
| 4 | Pure-Text Parent-Child | S | 0.76 | 0.65 | 0.705 | — |

### Phase 2 输出示例（视频/音频）

| Chunk | 检索 | Rerank | Scoped Rec@10 | Unscoped Rec@10 | File Cov | 综合得分 | 排名 |
|-------|------|--------|---------------|-----------------|----------|---------|------|
| Seg-Aware M | Hybrid | ON | 0.82 | 0.71 | 0.88 | **0.763** | 1 |
| Seg-Aware M | Hybrid | OFF | 0.80 | 0.65 | 0.82 | 0.712 | 2 |
| Flat S | Hybrid | ON | 0.78 | 0.68 | 0.85 | 0.730 | 3 |
| Flat S | Native | ON | 0.72 | 0.55 | 0.70 | 0.617 | 4 |

**视频/音频模态最终推荐配置**：`Segment-Aware (M) + Hybrid + Rerank ON`。
