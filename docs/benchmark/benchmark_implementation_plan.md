# 计划：为音频模态扩展 Benchmark（Chunk + RAG 两阶段评测）

## 背景

项目已有一份两阶段 benchmark 测试计划（`docs/benchmark_test_plan.md`），要求为每种文件模态系统性选择最优的 **Chunk 策略** 与 **检索策略** 组合。当前 benchmark 脚本（`backend/scripts/benchmark.py`）仅支持 legacy 的合成 fixtures（`sample.pdf`、`sample.pptx`），存在以下不足：
- 使用旧版 chunk ID（如 `chunk_0`），而非完整的 `meeting_{mid}_file_{fid}_chunk_0`
- 仅支持 **Scoped** 检索评测，不支持 **Unscoped（Broad Recall）**
- 未按模态区分 chunk 策略（无 segment-aware、page-aware 等差异化评测）

本计划基于现有代码，将 benchmark 扩展至支持 **音频模态**，使用 AMI 语料库 fixtures（`backend/tests/fixtures/Dataset/amicorpus/`），包含 4 个会议（`ES2015a`–`ES2015d`）的 `.wav` 音频文件。目标是实现可落地的两阶段 benchmark（Chunk 对比 + 检索 Grid Search）。

## 核心洞察：隔离无需手动清理

现有 `bench_environment()` 上下文管理器（`backend/scripts/_bench_env.py`）已为每次 benchmark 运行创建了**完全隔离的临时环境**（临时数据库 + 临时向量库 + 临时上传目录）。这意味着：
- **无需手动删除向量库**来避免不同 chunk 策略之间的索引污染
- 每次 Phase 1 / Phase 2 运行只需在独立的 `bench_environment()` 内执行，退出后操作系统自动删除临时目录

---

## 第一部分：Amicorpus 音频数据的摄取流程

### 1.1 音频转录
现有 `process_meeting_file()` 流水线已完整支持音频文件：
- 将 `.wav` 复制到 `UPLOAD_DIR`
- 通过 `transcriber.py` → `asr/_assemblyai.py` 调 ASR
- 返回带 `segments`（说话人 + 时间戳 + 文本）的 `FileArtefact`

**前提**：`.env` 中需配置 `ASSEMBLYAI_API_KEY`。

### 1.2 新增 Amicorpus 摄取助手
在 `backend/scripts/_bench_fixtures.py` 中新增（或新建 `backend/scripts/_bench_amicorpus.py`）：

```python
async def ingest_amicorpus_meeting(meeting_name: str) -> tuple[int, int]:
    """创建会议、复制 .wav、走 ASR 流水线。返回 (meeting_id, file_id)。"""
```

具体步骤：
1. 创建 `Meeting` 记录，title 为 `meeting_name`，日期固定
2. 复制 `amicorpus/{meeting_name}/audio/{meeting_name}.Mix-Headset.wav` 到上传目录
3. 创建 `meeting_files` 记录，`file_type="audio"`
4. 调用 `await process_meeting_file(file_id)` 触发转录
5. 返回 `(meeting_id, file_id)`

转录完成后，向量库中的 chunks 由**当前激活的 chunk 设置**决定。这就是 Phase 1 对比的基础。

---

## 第二部分：Benchmark 数据构建方式（LLM 自动生成 Golden Set）

### 2.1 核心思路：Query/Answer 与 Chunk ID 解耦

不同 chunk 策略产生的 chunks 不同，若人工为每种策略标注 chunk ID，工作量巨大且不可维护。我们的方案是：

1. **LLM 基于内容生成 Query + Answer**（不绑定具体 chunk ID）
2. **动态 Ground Truth 映射**：对每种 chunk 策略，自动计算哪些 chunks 包含了该 answer 的关键信息

这样生成的 golden set 是 **chunk-agnostic** 的——query 和 answer 固定，但 `expected_chunks` 根据当前 chunk 策略动态计算。

### 2.2 第一步：LLM 生成 Query + Answer

先选择一个参考 chunk 策略（如默认 Segment-Aware M）索引全部 4 个会议，导出所有 chunks 的文本。然后调用 LLM 批量生成 query/answer。

**Prompt 设计（Scoped）**：
```
你是一个会议问答系统测试数据生成器。给定以下会议转录文本的片段：

{chunk_texts}

请生成 {n} 个用户可能会问的真实问题，要求：
1. 每个问题必须能仅通过上述文本中的信息回答
2. 问题类型需覆盖：事实查询、说话人特定查询、时间范围查询、摘要查询
3. 给出标准答案（answer），答案必须完全基于文本内容
4. 指出该问题涉及哪些会议/文件（meeting_id, file_id）

输出格式（JSON）：
[
  {
    "query": "...",
    "expected_answer": "...",
    "expected_meeting_ids": [1],
    "expected_file_ids": [1],
    "query_type": "factual|speaker|temporal|summary"
  }
]
```

**Prompt 设计（Unscoped）**：
```
以下是多个会议的摘要和关键内容：

{meeting_summaries}

请生成 {n} 个跨会议查询，要求：
1. 问题的答案分布在多个会议中
2. 类型包括：比较、汇总、跨会议事实查询
3. 给出标准答案，并指出涉及哪些会议/文件

输出格式（JSON）...
```

**实现模块**：新建 `backend/scripts/_bench_generate_golden.py`

```python
async def generate_queries_from_chunks(
    chunks: list[dict],
    scope_type: str,  # "scoped" or "unscoped"
    num_queries: int,
) -> list[dict]:
    """调用 LLM 基于 chunks 生成 query + answer，不含 chunk IDs。"""
```

关键细节：
- 为避免 query 过于局部，输入 LLM 的文本应为 **相邻 3–5 个 chunks 的组合**（保留完整的上下文），而非单个 chunk
- 对于音频模态，在 prompt 中显式提供 `speaker` 和 `timestamp` 信息，引导 LLM 生成说话人相关和时间相关查询
- 使用项目现有的 LLM 调用方式（`src.services.llm`）而非外部 API，确保配置一致性

### 2.3 第二步：动态计算 Expected Chunks

对于每种 chunk 策略，在索引完成后，需要为该策略下的所有 query 自动计算 `expected_chunks`。

新建 `backend/scripts/_bench_map_golden.py`：

```python
def compute_expected_chunks(
    query_item: dict,
    chunks: list[dict],  # 当前 chunk 策略下的所有 chunks，每项含 chunk_id, text, metadata
    method: str = "hybrid",
) -> list[str]:
    """
    为当前 chunk 策略计算哪些 chunks 包含了 answer 所需的信息。
    返回 chunk_id 列表。
    """
```

**映射策略（推荐混合法）**：

| 策略 | 说明 |
|------|------|
| **关键词覆盖** | 从 `expected_answer` 中提取关键实体（名词、数字、专有名词），检查每个 chunk 是否包含这些实体。覆盖率高但可能有噪音。 |
| **Embedding 相似度** | 计算 `expected_answer` 与每个 chunk 的 embedding 余弦相似度，取 Top-N（如 Top-3）。能捕获语义相关但无共同关键词的 chunk。 |
| **LLM 判定**（可选） | 将 chunk 内容和 answer 给 LLM，问"该 chunk 是否包含 answer 所需的信息？" 精确但成本高。 |

**推荐实现**：先组合 **关键词覆盖 + Embedding 相似度**，取并集：
```python
# 1. 关键词覆盖
keywords = extract_keywords(query_item["expected_answer"])
keyword_hits = [c for c in chunks if contains_keywords(c["text"], keywords)]

# 2. Embedding 相似度
answer_emb = embed(query_item["expected_answer"])
chunk_embs = embed([c["text"] for c in chunks])
similarities = cosine_similarity(answer_emb, chunk_embs)
embedding_hits = [chunks[i] for i in top_k_indices(similarities, k=3)]

# 3. 取并集，去重
expected_chunks = list({c["chunk_id"] for c in keyword_hits + embedding_hits})
```

这样即使 chunk 切分方式改变（如从 Segment-Aware 改为 Flat），只要内容相同，就能自动找到对应的 chunks。

### 2.4 Golden Set 文件格式

最终存储两个 JSON 文件（仅含 query + answer，不含 chunk IDs）：

- `backend/tests/fixtures/benchmark/amicorpus_golden_scoped.json`
- `backend/tests/fixtures/benchmark/amicorpus_golden_unscoped.json`

**Scoped 示例**：
```json
{
  "version": 1,
  "scope_type": "scoped",
  "modality": "audio",
  "items": [
    {
      "id": "audio_scoped_001",
      "query": "Alice 在 ES2015a 中关于预算说了什么？",
      "expected_meeting_ids": [1],
      "expected_file_ids": [1],
      "expected_answer": "Alice 提议将预算增加 20%。",
      "query_type": "speaker"
    }
  ]
}
```

**Unscoped 示例**：
```json
{
  "version": 1,
  "scope_type": "unscoped",
  "modality": "audio",
  "items": [
    {
      "id": "audio_unscoped_001",
      "query": "哪些会议讨论了预算增加？",
      "expected_meeting_ids": [1, 2],
      "expected_file_ids": [1, 2],
      "expected_answer": "ES2015a 和 ES2015b 都讨论了预算增加。",
      "query_type": "summary"
    }
  ]
}
```

**运行时动态绑定 chunk IDs**：
在每次 benchmark 运行（某 chunk 策略）时：
1. 读取上述 JSON（query + answer 固定）
2. 获取当前向量库中的所有 chunks
3. 调用 `compute_expected_chunks()` 为每个 item 动态计算 `expected_chunks`
4. 用动态计算出的 chunk IDs 进行 retrieval 评测

### 2.5 完整工作流

```
┌─────────────────────────────────────────────┐
│ 1. 用参考 chunk 策略索引全部 4 个会议          │
│    （如 Segment-Aware M）                     │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│ 2. 导出所有 chunks 文本                       │
│    （含 speaker、timestamp、文本内容）         │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│ 3. LLM 生成 Golden Query + Answer           │
│    - Scoped: 8–12 条                         │
│    - Unscoped: 4–6 条                        │
│    输出: amicorpus_golden_scoped.json        │
│          amicorpus_golden_unscoped.json      │
│    （仅含 query/answer，不含 chunk IDs）      │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│ 4. Benchmark 运行时动态绑定                  │
│    对每种 chunk 策略：                        │
│    a. 重新索引                                │
│    b. 读取 golden JSON                        │
│    c. compute_expected_chunks()              │
│    d. 运行 retrieval 评测                     │
└─────────────────────────────────────────────┘
```

### 2.6 优势与注意事项

**优势**：
- **完全自动化**：无需人工阅读 chunk 或撰写 query
- **Chunk-agnostic**：同一套 query/answer 可复用于所有 chunk 策略
- **公平可比**：每种 chunk 策略的 ground truth 都是根据该策略的切分结果动态计算，避免"用 Flat 的 chunk ID 去评测 Segment-Aware"的不公平情况
- **可扩展**：新增 chunk 策略时，只需重新运行动态映射，无需重新标注

**注意事项**：
- 动态映射的准确性依赖关键词提取和 embedding 相似度的质量。建议在实现后抽样验证：随机选 10 条 query，人工检查 `compute_expected_chunks()` 返回的 chunks 是否确实包含 answer 信息
- 若 `expected_answer` 很短（如 "Alice"），关键词覆盖可能返回过多 chunks，可加入最小关键词数阈值（如至少匹配 2 个关键词）
- 对于 Parent-Child 策略，检索单元是 child chunk，但动态映射时应检查 child chunks 的内容（因为它们才是实际被检索的）

---

## 第三部分：如何扩展现有 Benchmark 代码

### 3.1 修复现有代码中的 Chunk ID 匹配逻辑
当前 `backend/scripts/benchmark.py` 的 `run_rag_retrieval_benchmark()` 使用旧版前缀拼接：
```python
expected = {f"meeting_{meeting_id}_{chunk}" for chunk in item.get("expected_chunks", [])}
```
需改为直接比较完整 ID：
```python
expected = set(item.get("expected_chunks", []))
```
检索结果的 ID 也应直接从 `metadata.chunk_id` 获取；若不存在，再用 `_chunk_id_prefix()` 根据 `meeting_id`、`file_id`、`chunk_index` 构造。

### 3.2 新增 Phase 1 / Phase 2 评测逻辑
新建模块 `backend/scripts/_bench_rag_phase1.py` 和 `backend/scripts/_bench_rag_phase2.py`（也可合并为一个函数）。

#### Phase 1：Chunk 策略对比
输入：一个 chunk 配置字典。

对每个配置，在独立的 `bench_environment()` 内执行：
1. 摄取全部 4 个 amicorpus 音频文件
2. 对 `amicorpus_golden_scoped.json` 中每条 query：
   - 调用 `retrieve(query, meeting_ids=[mid], top_k=10, fetch_multiplier=1)`
   - 记录 `Recall@10`、`MRR`、`NDCG@10`
3. 对 `amicorpus_golden_unscoped.json` 中每条 query：
   - 调用 `retrieve(query, meeting_ids=None, file_ids=None, top_k=10)`
   - 记录 `Recall@10`、`MRR`、`NDCG@10`、`File Coverage@10`
4. 计算综合得分：`0.5 × Scoped_Recall@10 + 0.5 × Unscoped_Recall@10`

**音频模态需测试的 8 组配置**：

| 方法 | 预设 | CHUNK_SIZE | AUDIO_SEMANTIC_BOUNDARY_ENABLED | AUDIO_SEMANTIC_BOUNDARY_THRESHOLD | PARENT_CHILD_ENABLED | CHILD_CHUNK_SIZE |
|------|------|-----------|--------------------------------|-----------------------------------|---------------------|-----------------|
| A Native（Segment-Aware） | S | 512 | True | 0.5 | False | — |
| A Native（Segment-Aware） | M | 1024 | True | 0.5 | False | — |
| A Native（Segment-Aware） | L | 2048 | False | — | False | — |
| B Flat | S | 512 | — | — | False | — |
| B Flat | M | 1024 | — | — | False | — |
| B Flat | L | 2048 | — | — | False | — |
| C Parent-Child | S | 1024 | — | — | True | 256 |
| C Parent-Child | L | 2048 | — | — | True | 512 |

**如何切换配置**：
- 在摄取前修改 `settings.CHUNK_SIZE = cfg["chunk_size"]` 等
- **Flat / Parent-Child**：设置 `settings.NON_TEXT_CHUNKING_STRATEGY = "text"`，然后正常走 `process_meeting_file()` 流水线。系统会自动通过 `_should_route_artefact_to_text_chunking()` 将 segment/page 内容展平为纯文本并送入 `index_meeting()`。无需手动拼接文本。
- **Native**：设置 `settings.NON_TEXT_CHUNKING_STRATEGY = "native"`（默认），正常走流水线，自动调用 `index_meeting_segments()`。
- 所有配置切换后，确保调用 `delete_meeting_chunks(meeting_id, file_id=file_id)` 清理旧索引（`bench_environment` 的隔离环境已自动保证这一点）。

**Top-2 筛选**：
全部 8 组运行完成后，按 `Combined_Recall@10` 排序选出 top-2，并确保优先来自**不同方法**（按 plan §2.6 规则）。

#### Phase 2：检索策略 Grid Search
对 Phase 1 选出的 top-2 chunk 配置，每组运行 4 种检索组合 × 2 种 scope：

| 检索策略 | Reranker | 配置 |
|---------|----------|------|
| Native | OFF | `RAG_RETRIEVER_PROVIDER=native`, `RERANKER_BINDING=""` |
| Native | ON | `RAG_RETRIEVER_PROVIDER=native`, `RERANKER_BINDING=cohere/bge` |
| Hybrid | OFF | `RAG_RETRIEVER_PROVIDER=hybrid`, `RERANKER_BINDING=""` |
| Hybrid | ON | `RAG_RETRIEVER_PROVIDER=hybrid`, `RERANKER_BINDING=cohere/bge` |

每组组合在独立的 `bench_environment()` 内：
1. 用**同一** top-2 chunk 配置重新索引全部会议
2. 运行 scoped queries（`meeting_ids=[mid]`）
3. 运行 unscoped queries（`meeting_ids=None`）
4. 记录全部指标
5. 计算加权综合得分：`0.4 × Unscoped_Recall + 0.3 × Scoped_Recall + 0.2 × File_Coverage + 0.1 × NDCG`

### 3.3 新增 CLI 子命令
在 `backend/scripts/benchmark.py` 的 `_build_parser()` 中新增：

```python
# rag-chunk-phase1
rag_p1 = sub.add_parser("rag-chunk-phase1", help="Phase 1: 对比音频模态的 chunk 策略")
rag_p1.add_argument("--golden-scoped", default=str(FIXTURE_DIR / "amicorpus_golden_scoped.json"))
rag_p1.add_argument("--golden-unscoped", default=str(FIXTURE_DIR / "amicorpus_golden_unscoped.json"))
rag_p1.add_argument("--output", default=str(RESULTS_DIR / "phase1_results.json"))

# rag-chunk-phase2
rag_p2 = sub.add_parser("rag-chunk-phase2", help="Phase 2: 对 top-2 chunk 做检索网格搜索")
rag_p2.add_argument("--phase1-result", required=True)
rag_p2.add_argument("--output", default=str(RESULTS_DIR / "phase2_results.json"))

# rag-chunk-full
rag_full = sub.add_parser("rag-chunk-full", help="端到端运行 Phase 1 + Phase 2")
```

### 3.4 单次运行中的配置锁定
每次运行前固定以下配置，确保公平对比：
```python
settings.TOP_K = 10
settings.RERANKER_TOP_N = 10
settings.HYBRID_ALPHA = 0.5
settings.RAG_RERANK_FETCH_MULTIPLIER = 6
settings.RAG_FAIR_ADAPTIVE_CHUNKS = True
settings.RAG_FILE_SCOPING_MODE = "router_and_funnel"  # 固定 Broad Recall 文件选择策略
settings.RAG_MEETING_SUMMARY_ROUTER_ENABLED = True      # 固定 meeting 级预路由
settings.RAG_BROAD_RECALL_MULTI_QUERY_ENABLED = False   # 排除 Multi-Query 干扰
settings.QUERY_REWRITE_ENABLED = False                  # 排除 query rewrite 的 LLM 干扰
```

> **关于 `RAG_SUMMARY_ROUTER_ENABLED` 与 `RAG_MEETING_SUMMARY_ROUTER_ENABLED`**：> - `RAG_MEETING_SUMMARY_ROUTER_ENABLED` 控制 meeting 级摘要预路由（在 `_retrieve_broad_recall()` 早期阶段）。> - `RAG_SUMMARY_ROUTER_ENABLED` 控制文件级摘要路由（由 scoping strategy 内部使用）。两者均保持开启以模拟生产环境。
> - `RAG_FILE_SCOPING_MODE` 必须锁定，否则不同策略（`router_and_funnel` vs `router_only`）会导致文件选择行为不一致，影响 benchmark 可比性。

---

## 第四部分：文件修改清单

### 修改已有文件
1. **`backend/scripts/benchmark.py`**
   - 修复 chunk ID 匹配逻辑，从拼接改为直接比较完整 ID
   - 新增子命令 `rag-chunk-phase1`、`rag-chunk-phase2`、`rag-chunk-full`
   - 实现 `run_rag_chunk_phase1()`、`run_rag_chunk_phase2()`

2. **`backend/scripts/_bench_fixtures.py`**
   - 新增 `ingest_amicorpus_meeting()` 助手
   - 新增 `ingest_all_amicorpus()`，返回 `{meeting_name: (mid, fid)}`

3. **`backend/scripts/_bench_aggregate.py`**
   - 新增 `format_chunk_benchmark_markdown()`，渲染 Phase 1/2 结果表格

### 新增文件
4. **`backend/scripts/_bench_amicorpus.py`**
   - 复制 amicorpus 音频、创建会议、调用 ASR 流水线的辅助函数

5. **`backend/scripts/_bench_chunk_configs.py`**
   - 定义音频模态 8 组配置的数据类
   - `apply_chunk_config(cfg)` 函数，安全修改 `settings`
   - 新增 `apply_non_text_strategy(strategy: str)`，用于切换 `"native"` / `"text"` 模式

6. **`backend/scripts/_bench_rag_phase1.py`**
   - Phase 1 核心逻辑：遍历配置 → 摄取 → 评测 scoped + unscoped → 计算综合得分 → 返回 top-2

7. **`backend/scripts/_bench_rag_phase2.py`**
   - Phase 2 核心逻辑：读取 top-2 → 遍历 4 种检索×rerank 组合 → 评测 → 计算加权得分

8. **`backend/scripts/_bench_generate_golden.py`**
   - 调用 LLM 基于 chunks 内容自动生成 query + answer（chunk-agnostic）

9. **`backend/scripts/_bench_map_golden.py`**
   - 动态 Ground Truth 映射：为每种 chunk 策略自动计算 `expected_chunks`
   - 实现关键词覆盖 + Embedding 相似度的混合映射策略

10. **`backend/tests/fixtures/benchmark/amicorpus_golden_scoped.json`**
11. **`backend/tests/fixtures/benchmark/amicorpus_golden_unscoped.json`**

---

## 第五部分：如何运行整个评测流程

### 前置条件
- `backend/.env` 中已配置 `ASSEMBLYAI_API_KEY`
- golden set 已标注并放入 fixtures 目录

### 逐步执行

1. **一次性生成 Golden Set**（若 golden set 已存在则跳过）：
   ```bash
   cd backend
   # 先用参考 chunk 策略（默认 Segment-Aware M）索引全部会议
   uv run python -m scripts._bench_generate_golden \
       --output-scoped tests/fixtures/benchmark/amicorpus_golden_scoped.json \
       --output-unscoped tests/fixtures/benchmark/amicorpus_golden_unscoped.json \
       --num-scoped 10 --num-unscoped 5
   # 可选：人工抽样验证生成的 query/answer 质量
   ```

2. **运行 Phase 1**（约 8 次独立运行，每次重新转录）：
   ```bash
   uv run python -m scripts.benchmark rag-chunk-phase1
   ```
   输出：`backend/benchmark-results/phase1_results_{timestamp}.json` + `.md`

3. **运行 Phase 2**（约 8 次独立运行）：
   ```bash
   uv run python -m scripts.benchmark rag-chunk-phase2 \
       --phase1-result benchmark-results/phase1_results_xxx.json
   ```
   输出：`backend/benchmark-results/phase2_results_{timestamp}.json` + `.md`

4. **一键运行完整流程**：
   ```bash
   uv run python -m scripts.benchmark rag-chunk-full
   ```

### 应对转录成本与耗时
AssemblyAI 转录既慢又贵，而 `bench_environment()` 每次创建隔离临时目录，导致每次运行都重新转录。缓解方案：
- **方案 A（推荐）**：首次转录后接受成本，benchmark 运行时正常走流程
- **方案 B（快速路径）**：预先转录 4 个会议一次，将 segments 保存为 `backend/tests/fixtures/benchmark/amicorpus_transcripts/ES2015a_segments.json`。在 `_bench_amicorpus.py` 中增加快捷逻辑：当环境变量 `BENCH_USE_PRETRANSCRIBED=1` 存在且预转录文件存在时，跳过 ASR，直接加载 JSON 并构造 artefact 后索引

我们将在 `_bench_amicorpus.py` 中实现方案 B 的快速路径：
```python
if os.environ.get("BENCH_USE_PRETRANSCRIBED") and pretranscribed_path.exists():
    segments = json.loads(pretranscribed_path.read_text())
    # 跳过 process_meeting_file 的 ASR 阶段，直接创建 artefact 并索引
else:
    # 正常 ASR 流水线
```

---

## 第六部分：验证方法

实现完成后，执行以下命令验证：

```bash
cd backend
uv run python -m scripts.benchmark rag-chunk-phase1 --output /tmp/phase1_test.json
```

检查点：
1. Markdown 报告包含 8 行表格（每组预设一行）
2. 每行显示 Scoped Recall@10、Unscoped Recall@10、Combined 得分
3. JSON 输出包含 `top_2` 字段，且两个配置属于不同方法
4. 日志中无 chunk ID 拼接错误

再验证 Phase 2：
```bash
uv run python -m scripts.benchmark rag-chunk-phase2 --phase1-result /tmp/phase1_test.json
```

检查点：
1. 报告包含 8 行网格（2 个 chunk 配置 × 4 种检索组合）
2. Unscoped 行包含 `File Coverage@10` 指标
3. 最终输出包含 `recommendation` 字段，指明最优配置

---

## 附录：Parent-Child 模式下 BM25/Vector 一致性修复（方案 A）

### 问题背景

在 Parent-Child 分块模式下，当前实现存在严重的**检索侧不一致**：

- **Vector 侧**：检索命中 child chunk，通过 `_resolve_parent_chunks()` 回查并返回 **parent chunk**。
- **BM25 侧**：`_add_to_bm25()` 重新拆分原始文本后写入 child chunk，`_bm25_retrieve()` 直接返回 **child chunk**，不做 parent 回查。
- **ID 空间不一致**：Vector 中 child ID 为 `..._child_{i}_{j}`，BM25 中 child ID 为 `..._chunk_{i*1000+j}`。
- **RRF 去重失效**：`_rrf_dedup_key()` 依赖 `chunk_id` / `chunk_index` / content hash，parent 和 child 在这三个维度上均不同，导致同一个语义内容以两种粒度同时进入 top-k，占据两个位置。

这会导致 benchmark 中 Hybrid 检索的 Recall/MRR 计算失真，且 golden set 无法同时匹配两边结果。

### 修复目标

让 BM25 在 Parent-Child 模式下也返回 **parent chunk**，与 Vector 侧的返回粒度完全一致，实现：
1. ID 空间对齐（都返回 `..._parent_{i}`）。
2. RRF 去重生效（同一个 parent 不会被重复计数）。
3. golden set 只需标注 parent ID 即可同时匹配 Vector 和 BM25。

### 修改文件与具体逻辑

#### 修改 1：`backend/src/services/rag/_indexer_store.py` 的 `_add_to_bm25()`

**当前问题**：BM25 索引时 metadata 中缺少 `parent_id` 和 `chunk_type`，且 chunk ID 使用独立的 `chunk_{index}` 格式。

**修改内容**：

1. **统一 chunk ID**：Parent-Child 模式下，BM25 的 child chunk ID 改为与向量库一致：`f"{prefix}_child_{i}_{j}"`。
2. **丰富 metadata**：写入 `"parent_id"`（指向 parent chunk ID）和 `"chunk_type": "child"`，供检索时回查使用。

**修改后的核心逻辑（伪代码）**：

```python
def _add_to_bm25(meeting_id: int, text: str, metadata: dict, separators: list[str]) -> None:
    # ... 前段 splitter 逻辑不变 ...
    prefix = _chunk_id_prefix(meeting_id, metadata.get("file_id"))
    indexed = 0
    try:
        with get_write_connection() as conn:
            if settings.PARENT_CHILD_ENABLED:
                for i, parent_text in enumerate(parent_chunks):
                    parent_id = f"{prefix}_parent_{i}"
                    for j, child_text in enumerate(child_splitter.split_text(parent_text)):
                        chunk_id = f"{prefix}_child_{i}_{j}"
                        add_bm25_chunk(
                            conn,
                            chunk_id=chunk_id,
                            meeting_id=meeting_id,
                            content=child_text,
                            tokenized="[]",
                            metadata=json.dumps({
                                "meeting_id": meeting_id,
                                "chunk_index": i * 1000 + j,
                                "chunk_type": "child",
                                "parent_id": parent_id,
                                **metadata,
                            }),
                        )
                        indexed += 1
            else:
                # Flat 模式保持原有逻辑不变
                for chunk_text, chunk_index in chunks:
                    chunk_id = f"{prefix}_chunk_{chunk_index}"
                    add_bm25_chunk(
                        conn,
                        chunk_id=chunk_id,
                        meeting_id=meeting_id,
                        content=chunk_text,
                        tokenized="[]",
                        metadata=json.dumps({
                            "meeting_id": meeting_id,
                            "chunk_index": chunk_index,
                            **metadata,
                        }),
                    )
                    indexed += 1
        logger.info("Meeting %d: added %d chunks to FTS5 index", meeting_id, indexed)
    except Exception as e:
        logger.warning("Failed to persist BM25 chunks to database: %s", e)
```

> **注意**：Flat 模式的逻辑保持原样不动，仅修改 Parent-Child 分支。

#### 修改 2：新增 `backend/src/services/rag/_vector.py` 的通用 parent 回查函数

当前 `_resolve_parent_chunks()` 紧耦合了 Vector 检索的返回格式（`list[tuple[Any, float]]`）。为了被 BM25 复用，需新增一个更通用的版本。

**新增函数**：

```python
def resolve_parent_chunks_by_ids(
    parent_ids: list[str],
    child_scores: dict[str, float],
) -> list[dict]:
    """给定 parent_id 列表和对应的 child 分数，批量回查 vectorstore 获取 parent chunk。

    对同一 parent 的多个 child hits，保留分数最优（距离最低 / 相似度最高）的一个。
    """
    if not parent_ids:
        return []
    vectorstore = get_vectorstore()
    try:
        parent_data = vectorstore.get(
            ids=parent_ids,
            include=["documents", "metadatas"],
        )
    except Exception:
        logger.warning("Failed to fetch parent chunks", exc_info=True)
        return []

    out = []
    for idx, content in enumerate(parent_data["documents"]):
        meta = parent_data["metadatas"][idx]
        pid = parent_data["ids"][idx]
        if pid in child_scores:
            out.append({
                "content": content,
                "metadata": meta,
                "score": float(child_scores[pid]),
            })
    return out
```

**改造原有 `_resolve_parent_chunks()`**：

使其内部调用 `resolve_parent_chunks_by_ids()`，保持对外接口不变：

```python
def _resolve_parent_chunks(
    vectorstore: Any,
    child_results: list[tuple[Any, float]],
    threshold: float | None,
    lower_is_better: bool = True,
) -> list[dict]:
    seen_parents: dict[str, float] = {}
    for doc, score in child_results:
        if threshold is not None:
            if lower_is_better:
                if score > threshold:
                    continue
            elif score < threshold:
                continue
        parent_id = doc.metadata.get("parent_id")
        if not parent_id:
            continue
        if parent_id not in seen_parents or (
            (lower_is_better and score < seen_parents[parent_id])
            or (not lower_is_better and score > seen_parents[parent_id])
        ):
            seen_parents[parent_id] = score
    return resolve_parent_chunks_by_ids(list(seen_parents.keys()), seen_parents)
```

#### 修改 3：`backend/src/services/rag/_bm25.py` 的 `_bm25_retrieve()`

**修改内容**：在返回结果前，对 Parent-Child 模式下命中的 child chunk 做 parent 回查。

**判断是否需要回查**：结果 metadata 中存在 `"parent_id"` 且 `"chunk_type" == "child"`。

**修改后的核心逻辑**：

```python
def _bm25_retrieve(
    query: str,
    meeting_ids: list[int] | None,
    file_ids: list[int] | None,
    k: int,
    *,
    trace: TraceContext | None = None,
    speaker_names: list[str] | None = None,
) -> list[dict]:
    # ... 前段 fts5_search 逻辑不变 ...

    out = []
    parent_id_to_best_score: dict[str, float] = {}
    child_hits: list[dict] = []

    for r in results:
        try:
            meta = json.loads(r["metadata"]) if r["metadata"] else {}
        except json.JSONDecodeError:
            meta = {"meeting_id": r["meeting_id"]}

        score = float(-r["rank"]) if r["rank"] else 0.0
        parent_id = meta.get("parent_id")

        # Parent-Child 模式下的 child hit：收集最优分数，稍后统一回查 parent
        if parent_id and meta.get("chunk_type") == "child":
            if parent_id not in parent_id_to_best_score or score > parent_id_to_best_score[parent_id]:
                parent_id_to_best_score[parent_id] = score
            continue

        # Flat 模式或 parent hit：直接保留
        out.append({
            "content": r["content"],
            "metadata": meta,
            "score": score,
        })

    # 回查 parent chunks（如果存在 child hits）
    if parent_id_to_best_score:
        from ._vector import resolve_parent_chunks_by_ids
        parents = resolve_parent_chunks_by_ids(
            list(parent_id_to_best_score.keys()),
            parent_id_to_best_score,
        )
        out.extend(parents)

    if trace:
        trace.finish_span("bm25_search")
    return out
```

> **边界情况处理**：
> 1. 若 `parent_id_to_best_score` 中的某个 parent ID 在 vectorstore 中已被删除（理论上不应发生，因为 BM25 和 vectorstore 是同步写入的），`resolve_parent_chunks_by_ids()` 会自然跳过该 ID。
> 2. 同一 parent 被多个 child 命中时，保留 BM25 score 最高的那个（因为 BM25 的 score 是"越高越好"）。
> 3. 若 vectorstore 回查失败（网络/超时），记录 warning 并仅保留已有的 flat/parent 结果，避免空返回。

### 修改后的预期行为

| 维度 | Vector 侧 | BM25 侧 | 一致性 |
|------|----------|---------|--------|
| **匹配对象** | child chunk | child chunk | ✅ 一致 |
| **返回对象** | parent chunk | parent chunk | ✅ 一致 |
| **chunk ID** | `..._parent_{i}` | `..._parent_{i}` | ✅ 一致 |
| **metadata** | `chunk_type: "parent"` | `chunk_type: "parent"` | ✅ 一致 |
| **RRF 去重** | 正常 | 正常 | ✅ 同一 parent 不会重复 |

### 对 Benchmark 的影响

修复完成后，在 Parent-Child 策略的评测中：
1. **golden set 只需标注 parent chunk ID**（如 `meeting_42_file_101_parent_3`），即可同时匹配 Vector 和 BM25 的结果。
2. **Hybrid RRF 的结果池**中不再出现 parent + child 重复，top-k 利用率更高。
3. **Phase 1 / Phase 2 的指标计算**（Recall@10、MRR、NDCG）更加准确可信。

---

## 总结

本计划复用现有隔离机制（`bench_environment`）、现有摄取流水线（`process_meeting_file` → AssemblyAI）和现有检索接口（`retrieve()` 支持 scoped/unscoped；Broad Recall 时通过 `fair_retrieve_per_file()` 逐文件调用），为音频模态构建两阶段 benchmark。主要扩展点：
- amicorpus `.wav` 文件的摄取助手
- 带完整 chunk ID 的 golden set JSON 文件
- Phase 1/2 评测逻辑，在隔离环境中遍历 chunk 配置
- `benchmark.py` 的 CLI 扩展
- 预转录快速路径，避免重复 ASR 开销
- **Parent-Child 模式下 BM25/Vector 一致性修复（方案 A）**

所有修改限于 `backend/scripts/`、`backend/src/services/rag/` 和 fixtures 目录。
