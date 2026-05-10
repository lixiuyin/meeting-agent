# Chunk & Retrieval 技术文档

本文档系统描述 meeting-agent 的 **Chunk（分块）** 与 **Retrieval（检索）/Rerank（重排序）** 两大部分的实现逻辑、数据流与代码对应关系。

---

## 1. Chunk 实现

### 1.1 触发链路：文件上传后何时触发 Chunk

Chunk 的触发始于一次会议文件上传。后端通过 FastAPI BackgroundTasks 异步启动处理管道，整体链路如下：

1. **上传入口**：`backend/src/api/routers/meetings/_upload.py` 接收文件，写入磁盘，创建 `meeting` / `meeting_files` 记录，状态设为 `pending`。随后调用 `process_meeting_file()` 或 `process_meeting()` 进入后台任务。
2. **处理器调度**：
   - 新版多文件入口：`backend/src/services/processor/_pipeline.py:process_meeting_file()` 根据 `file_type` 选择对应 Processor，将文件解析为统一的 `FileArtefact` 数据形态，再据此分发到不同的 chunk 索引入口。
   - 旧版单文件入口：`backend/src/services/processor/_pipeline_meeting.py:process_meeting()` 仅作为兼容路径存在，统一将内容转为纯文本后调用 `index_meeting()`。
3. **文本提取完成后**：提取的文本/结构化数据被送往 RAG 索引层，由三个不同的入口函数根据数据类型选择 chunk 策略。

---

### 1.2 处理器与数据形态依赖

系统不直接按文件类型选择 chunk 策略，而是**先通过 Processor 将不同文件解析为统一的中间数据形态 `FileArtefact`，再根据该形态中的关键字段分发到对应的 chunk 入口**。

处理器选择由 `_pipeline.py:_resolve_processor()` 根据 `file_type` 决定：

| 文件类型 | 处理器 | 核心解析行为 |
|---------|--------|-------------|
| `video`、`audio` | `AVFileProcessor` | 调用 ASR（AssemblyAI）进行转录，产出带时间戳和 speaker 标签的 segment 列表 |
| `pdf`、`ppt`、`doc`、`xls`、`csv` | `DocumentFileProcessor` | 调用 Parser Cascade 进行文档解析，产出 `ParsedDocument`（含逐页文本、表格、图片资产） |
| `image` | `ImageFileProcessor` | 调用 `parse_structured` 做基础解析，同时调用 Vision 模块进行 Caption 与 OCR；若提取到有效内容，会同时填充 `segments` 与 `parsed_doc` |
| `txt`、`md` 等纯文本 | `TextFileProcessor` | 直接读取文本内容，仅产出 `text` |

`FileArtefact` 的数据结构定义位于 `backend/src/services/processor/_processors/_types.py:FileArtefact`，其关键字段如下：

- `text: str` —— 提取后的纯文本（所有处理器都会产出）。
- `segments: list[dict] | None` —— 音视频转录段，每个段含 `start`、`end`、`text`、`speaker`；`ImageFileProcessor` 在提取到 caption/OCR 时也会填充为单段列表。
- `parsed_doc: ParsedDocument | None` —— 结构化文档对象，仅 `DocumentFileProcessor` 与 `ImageFileProcessor` 产出。
- `aux_segments: list[dict] | None` —— 视频关键帧 segment（仅视频文件）。
- `structured_json / structured_kind` —— 结构化数据序列化结果。

> 各处理器产出代码：
> - `AVFileProcessor`：`backend/src/services/processor/_processors/av.py:AVFileProcessor.process()`
> - `DocumentFileProcessor`：`backend/src/services/processor/_processors/document.py`（返回 `FileArtefact` 处）
> - `ImageFileProcessor`：`backend/src/services/processor/_processors/image.py:ImageFileProcessor.process()`
> - `TextFileProcessor`：`backend/src/services/processor/_processors/text.py:TextFileProcessor.process()`

---

### 1.3 数据形态到 Chunk 入口的分发

所有 chunk 与向量存储逻辑集中在 `backend/src/services/rag/_indexer.py`。在 `_pipeline.py:process_meeting_file()` 的分发逻辑中，系统根据 `FileArtefact` 的字段优先级与配置做判断：

```python
if _should_route_artefact_to_text_chunking(artefact):
    index_meeting(...)               # 纯文本扁平/父子分块（覆盖模式）
elif artefact.segments is not None:
    index_meeting_segments(...)      # 语义分块
elif artefact.parsed_doc is not None:
    index_meeting_pages(...)         # 按页分块
else:
    index_meeting(...)               # 标准扁平分块
```

**分发规则说明**：

| 判断条件 | 说明 | 对应 Chunk 入口 |
|---------|------|----------------|
| `NON_TEXT_CHUNKING_STRATEGY == "text"` 且 artefact 含 `segments` 或 `parsed_doc` | 将结构化内容展平为纯文本后送入标准分块 | `index_meeting()` |
| `artefact.segments is not None`（未被 text 覆盖） | 存在时间戳化的 segment 列表（音视频转录段，或图片的 caption/OCR 单段） | `index_meeting_segments()` |
| `artefact.parsed_doc is not None`（未被 text 覆盖） | 存在结构化文档对象（多页文本、表格、图片资产） | `index_meeting_pages()` |
| 上述皆无 | 仅有纯文本字符串 | `index_meeting()` |

> **注意**：
> - `ImageFileProcessor` 在成功提取 caption/OCR 后，`segments` 字段非空，因此**优先进入 `index_meeting_segments()`**（除非被 text 覆盖）；若未提取到任何内容（`segments=None`），则回退到 `parsed_doc` 分支进入 `index_meeting_pages()`。
> - **Text 覆盖模式**是 benchmark 中生成 Pure-Text Flat / Parent-Child 变体的推荐方式：只需修改 `NON_TEXT_CHUNKING_STRATEGY` 配置，无需手动抽取文本。

每个入口内部再根据配置决定使用 **Flat Chunking** 或 **Parent-Child Chunking**。

---

### 1.4 文件类型到 Chunk 策略的完整映射

综合处理器解析行为与数据形态分发逻辑，最终映射如下：

| 文件类型 | 处理器 | 数据形态关键字段 | Chunk 入口 | Chunk 策略 | 说明 |
|---------|--------|-----------------|-----------|-----------|------|
| `video`、`audio`（原生） | `AVFileProcessor` | `segments`（转录段列表） | `index_meeting_segments()` | **Segment-Aware** | 基于 speaker segment 语义边界分块，支持说话人标签对齐与 embedding 复用 |
| `video`、`audio`（text 覆盖） | `AVFileProcessor` | `segments` → 展平文本 | `index_meeting()` | **Flat / Parent-Child** | `NON_TEXT_CHUNKING_STRATEGY="text"` 时，segment 被拼接为带 `[mm:ss] Speaker: text` 前缀的纯文本后走标准分块 |
| `pdf`、`ppt`、`doc`、`xls`、`csv`（原生） | `DocumentFileProcessor` | `parsed_doc`（结构化文档） | `index_meeting_pages()` | **Page-Aware** | 按页/slide 分块，文本、表格、图片 Caption/OCR 分别成独立 chunk |
| `pdf`、`ppt` 等（text 覆盖） | `DocumentFileProcessor` | `parsed_doc` → 展平文本 | `index_meeting()` | **Flat / Parent-Child** | `NON_TEXT_CHUNKING_STRATEGY="text"` 时，调用 `ParsedDocument.to_indexable_text()` 展平后走标准分块 |
| `image`（提取到内容时） | `ImageFileProcessor` | `segments`（单段 caption/OCR） | `index_meeting_segments()` | **Segment-Aware** | 将 caption/OCR 文本作为单 segment 进行语义分块 |
| `image`（未提取到内容时） | `ImageFileProcessor` | `parsed_doc`（结构化解析结果） | `index_meeting_pages()` | **Page-Aware** | 回退到结构化文档分块路径 |
| `txt`、`md` 等纯文本 | `TextFileProcessor` | `text`（纯文本） | `index_meeting()` | **Flat / Parent-Child** | 使用 `RecursiveCharacterTextSplitter` 做字符级分块，可选结构感知增强；若开启 Parent-Child 则走两级拆分 |
| Legacy 单文件（任意旧数据） | `process_meeting()` | `text`（纯文本） | `index_meeting()` | **Flat / Parent-Child** | 旧版兼容路径，统一将内容视为纯文本后分块 |

> 处理器解析与入口分发代码：`backend/src/services/processor/_pipeline.py:process_meeting_file()` 中分发逻辑所在的后半段。

---

### 1.5 Flat Chunking（标准扁平分块）

Flat 模式对应函数 `backend/src/services/rag/_indexer.py:_index_flat()`，是默认的通用分块路径：

1. **是否启用语义结构感知拆分**：由配置项 `SEMANTIC_CHUNKING_ENABLED` 控制。若开启，先调用 `backend/src/services/rag/_chunkers.py:_split_by_structure()` 进行粗分；再对超长段落使用 `RecursiveCharacterTextSplitter` 细分。
2. **纯字符级拆分**：若未开启语义拆分，直接使用 `RecursiveCharacterTextSplitter`，参数为：
   - `chunk_size = settings.CHUNK_SIZE`
   - `chunk_overlap = settings.CHUNK_OVERLAP`
   - `separators`：预定义的多级分隔符列表（段落、行、句子、空格、空字符），见 `_indexer.py` 中 `_SEPARATORS` 常量定义。

**`_split_by_structure()` 的实现**（`backend/src/services/rag/_chunkers.py`）：
- 基于正则 `_TOPIC_BREAK_PATTERNS` 识别话题边界：Markdown 标题、`Speaker \d` 标签、编号列表、水平分割线等。
- 按边界将文本切分为若干 segment，再对相邻小 segment 做合并（merge buffer），确保每个最终 chunk 不超过 `max_chunk_size`。
- 该策略对会议转录稿尤其有效，可避免在说话人切换或议题切换处硬截断。

---

### 1.6 Parent-Child Chunking（父子两级分块）

当配置 `PARENT_CHILD_ENABLED = true` 时，调用 `backend/src/services/rag/_indexer.py:_index_parent_child()`：

- **Parent 层**：`chunk_size = CHUNK_SIZE`，用于保留完整上下文。
- **Child 层**：`chunk_size = CHILD_CHUNK_SIZE`，用于实际检索命中。
- 每个 child chunk 的 metadata 中写入 `parent_id`，检索命中 child 后，系统会**回查 parent** 作为最终送入 LLM 的上下文（见 `backend/src/services/rag/_vector.py:_resolve_parent_chunks()`）。
- ID 生成规则：`meeting_{id}_file_{fid}_parent_{i}` / `meeting_{id}_file_{fid}_child_{i}_{j}`。

---

### 1.7 Page-Aware Chunking（文档解析后的按页分块）

针对 PDF、PPTX 等由解析器（Parser Cascade）产出的 `ParsedDocument`，使用 `backend/src/services/rag/_indexer.py:index_meeting_pages()`：

1. **逐页遍历**：对每个 `page`（来自 `parsed.pages`），提取 `page_num`、`text`、`heading_path`、图片/表格资产。
2. **文本分块**：
   - 若整页文本长度 ≤ `CHUNK_SIZE`，整页作为一个 chunk；
   - 否则用 `RecursiveCharacterTextSplitter` 对该页文本进一步细分。
3. **表格独立成块**：若配置 `RAG_INDEX_TABLES = true`，将 `page.tables` / `page.table_assets` 转换为 Markdown 格式后作为独立 chunk，content_type 标记为 `"table"`。
4. **图片 Caption/OCR 成块**：若配置 `RAG_INDEX_IMAGE_CAPTIONS = true`，提取图片的 caption 与 OCR 文本：
   - 两者皆有时合并为 `[Caption] ... \n[OCR] ...`，type 为 `"image_combined"`；
   - 仅有其一则分别为 `"image_caption"` / `"image_ocr"`。
5. **Metadata 增强**：每个 chunk 的 metadata 中记录 `page_number`、`content_type`、`heading_path`，以及图片资产的 storage_path/thumbnail_path，供前端展示与后续 sibling co-retrieval 使用。

> 辅助函数：`backend/src/services/rag/_indexer_extract.py` 提供 `_page_extra_meta()`、`_normalize_table_markdown()`、`_extract_image_texts()`、`_page_preview_paths()` 等页面级元数据提取工具。

---

### 1.8 Segment-Aware Chunking（音视频语义分块）

针对 AssemblyAI 等 ASR 产出的 speaker segment 列表，使用 `backend/src/services/rag/_indexer.py:index_meeting_segments()`：

1. **过滤空 segment**，按配置决定是否将 speaker 标签前缀写入文本（`AUDIO_SPEAKER_IN_CONTENT`）。
2. **逐 segment 计算 embedding**：通过 `backend/src/services/llm/_embeddings_adapter.py:embed_documents_batched()` 批量获取每个 segment 的向量。
3. **语义边界检测**（`_indexer.py:_detect_semantic_boundaries()`）：
   - 计算相邻 segment embedding 的 cosine similarity；
   - 当 similarity 低于阈值 `AUDIO_SEMANTIC_BOUNDARY_THRESHOLD` 且 segment 数满足 `min_segments/max_segments` 约束时，标记为边界。
4. **按边界与字符上限分组合并**：`_indexer.py:_group_segments_by_boundaries()` 遍历 segments，维护一个当前组（`current_group`）和累计字符长度（`current_len`）。遇到以下任一条件时，先将当前组打包为一个 chunk，再清空组并将当前 segment 加入新组：

   - **语义边界触发**：当前 segment 的索引 `i` 出现在 `_detect_semantic_boundaries` 返回的边界集合中（即此处话题发生转折）。
   - **字符上限触发**：`current_len + 当前 segment 文本长度 > CHUNK_SIZE`，且当前组非空。

   **合并策略的核心逻辑**：
   1. 优先尊重语义边界，保证 chunk 内部话题一致；
   2. 若同一话题内文本累积超过 `CHUNK_SIZE`，则强制按字符上限切分，避免单个 chunk 过长；
   3. 每次切分时，调用 `_build_chunk_with_speaker_alignment()` 将当前组内所有 segments 拼接为 chunk 文本，并计算该 chunk 的 timestamp 范围与 embedding（组内 segment embeddings 的平均值）。

   **举例说明**（假设 `CHUNK_SIZE = 30`）：

   | 索引 | Speaker | 文本 | 长度 | 处理过程 |
   |------|---------|------|------|----------|
   | S0 | Alice | "开场介绍今天的会议目标。" | 12 | 加入组，累计 12 |
   | S1 | Alice | "同步一下上周的项目进度。" | 12 | 加入组，累计 24 |
   | S2 | Bob | "我来说后端的情况。" | 10 | **语义边界** → 先打包 [S0,S1] 为 **Chunk A**（24 字），再将 S2 加入新组，累计 10 |
   | S3 | Bob | "API 完成了 80%。" | 10 | 加入组，累计 20 |
   | S4 | Bob | "预计本周可以提测。" | 10 | 加入组，累计 30 |
   | S5 | Alice | "前端各模块开发完毕，只剩联调工作尚未开始，预计还需要两天时间。" | 40 | **字符上限** → 先打包 [S2,S3,S4] 为 **Chunk B**（30 字），再将 S5 加入新组，累计 40 |
   | — | — | — | — | 遍历结束，打包 [S5] 为 **Chunk C**（40 字） |

   上述过程同时展示了两种切分场景：S2 处因语义边界切出 Chunk A；S5 处因加入后会导致 [S2,S3,S4,S5] 总长达 70 字超过 30，因而先切出 Chunk B，让 S5 自成 Chunk C。

5. **Speaker 对齐**：在 `_build_chunk_with_speaker_alignment()` 中，每个 chunk 的默认 speaker 取组内第一个 segment 的 speaker；若该 segment 无 speaker（如环境音），则继承上一个 chunk 的 speaker，避免在 chunk 边界处丢失说话人信息。组内各 segment 的文本前缀仍优先使用自身 speaker 标签（若配置 `AUDIO_SPEAKER_IN_CONTENT`）。
6. **Embedding 复用**：chunk 的 embedding 直接取组内 segment embeddings 的平均向量，避免再次调用 embedding API，显著降低成本。
7. **富 metadata**：每个 chunk 记录 `timestamp_start`、`timestamp_end`、`speaker`、`speakers_in_chunk`、`time_position_ratio`（chunk 中点占整场会议的比例），支持后续时间维度的精准过滤。

---

### 1.9 向量存储、去重与 BM25 同步

Chunk 生成后，统一进入 `backend/src/services/rag/_indexer_store.py` 完成持久化：

- **Chunk ID 规则**：`_chunk_id_prefix()` 生成 `meeting_{meeting_id}_file_{file_id}` 前缀，后缀按索引序号递增（如 `_chunk_0`）。这保证了同一文件重新索引时 ID 稳定。
- **内容去重**：`_indexer_store.py:_dedup_existing_chunks()` 计算每段内容的 SHA-256 短哈希，与 vectorstore 中已有 chunk 比对；若哈希一致则跳过，避免重复嵌入。
- **向量写入**：`_indexer_store.py:_upsert_with_trace()` 调用 `get_embeddings().embed_documents()` 计算向量（或直接使用预计算向量），随后通过 `vectorstore._collection.upsert()` 写入 Chroma，操作受 `vectorstore_write_lock` 保护。
- **BM25 同步**：若 `HYBRID_SEARCH_ENABLED = true`，同步将 chunk 写入 SQLite FTS5 索引（`bm25_index` 表）：
  - 若启用 Parent-Child，BM25 也采用相同的二级拆分逻辑，保证与向量索引的 chunk 粒度一致；
  - 函数 `_indexer_store.py:_add_to_bm25()` 与 `_indexer_store.py:_add_docs_to_bm25()` 分别服务文本流与 Document 列表两种输入形态。
- **清理接口**：`_indexer_store.py:delete_meeting_chunks()` 支持按 `meeting_id` 或 `file_id` 精确删除 Chroma 向量、BM25 索引、摘要向量及 `index_state` 记录，保证多文件会议下的增删一致性。

---

## 2. Retrieval & Rerank

### 2.1 整体分层架构

检索相关代码分布在多个核心文件中，形成**六层架构**。越靠近上层，职责越偏向"业务编排"；越靠近下层，职责越偏向"存储访问"。

| 层级 | 职责 | 核心代码位置 | 关键函数/类 |
|------|------|-------------|------------|
| **Layer 5: Post-Processing** | 重排序、去重、低信息过滤 | `_retrieve_post.py` | `rerank_documents()`、`suppress_near_duplicates()`、`pre_rerank_dedup()` |
| **Layer 4: Pipeline Step** | 检索编排：Broad Recall、Scoped、过滤、Anchor | `_steps_retrieve.py`（编排）+ `_retrieve_broad.py`（Broad Recall）+ `_retrieve_filters.py`（过滤） | `retrieve_documents()` |
| **Layer 3: Scoping & Fair Retrieval** | Broad Recall 时的文件选择与公平检索 | `_scoping_strategies.py`、`_funnel_narrow.py`、`_fair_retriever.py` | `get_scoping_strategy()`、`fair_retrieve_per_file()` |
| **Layer 2: Orchestrator** | 基础检索入口 | `_retriever.py` | `retrieve()` |
| **Layer 1: Strategy Backend** | 四种策略的具体执行逻辑 | `_retriever.py` | `_run_native_strategy()`、`_run_hybrid_strategy()`、`_run_multimodal_strategy()`、`_run_hybrid_multimodal_strategy()` |
| **Layer 0: Storage** | 向量库、全文索引、摘要路由库 | `_vectorstore.py`、`_bm25.py`、`_meeting_summary_vectorstore.py`、`_summary_router.py` | Chroma、`fts5_search()`、`route_meetings_by_summary()`、`route_files_by_summary()` |

**依赖关系（自上而下）**：

```
Layer 5 (Post-Processing: _retrieve_post.py)
    ↑ 读取 ctx.docs
Layer 4 (Pipeline Step: retrieve_documents in _steps_retrieve.py)
    ↑ 调用 Layer 3（Broad Recall 时，逻辑在 _retrieve_broad.py）或直接调用 Layer 2（Scoped 时）
    ↑ 过滤由 _retrieve_filters.py 提供（speaker · temporal · content-type bias）
Layer 3 (Scoping & Fair Retrieval: _scoping_strategies.py + _funnel_narrow.py + _fair_retriever.py)
    get_scoping_strategy().select_scope() ──→ fair_retrieve_per_file()
    └──→ 内部调用 Layer 2 的 retrieve()
Layer 2 (Orchestrator: _retriever.py)
    retrieve() ──→ Layer 1 Strategy.retrieve()
Layer 1 (Strategy Backend: _retriever.py)
    Native / Hybrid / Multimodal / HybridMultimodal
    └──→ _vector_retrieve() / _bm25_retrieve() / retrieve_with_raganything()
Layer 0 (Storage)
    Chroma / SQLite FTS5 / Summary Collection
```

> **核心原则**：
> - `retrieve()` 是所有检索的**统一底层入口**（Layer 2），负责策略选择、query analysis、filter 构建。
> - **Broad Recall** 时，`retrieve_documents()` 先通过 **Layer 3** 的文件选择策略（scoping strategy + funnel narrow）确定候选文件，再由 `fair_retrieve_per_file()` 逐文件调用 `retrieve()`。
> - **Scoped** 时，`retrieve_documents()` 直接调用 `retrieve()`，跳过 Layer 3。
> - Rerank 是 `retrieve_documents()` 执行完毕后的**独立后处理步骤**。

---

### 2.2 Pipeline 调用链路

在 `backend/src/services/chain/_api.py:_run_pipeline()` 中，检索分支的执行顺序是严格串行的：

```python
async def _retrieve_branch() -> None:
    await retrieve_documents(ctx)                           # Layer 4 (编排)
    pre_rerank_dedup(ctx)                                   # Layer 5a (预去重)
    await asyncio.to_thread(rerank_documents, ctx)           # Layer 5b (重排序)
    await asyncio.to_thread(suppress_near_duplicates, ctx)   # Layer 5c (近去重)
```

**完整调用链路（以一次典型流式问答为例）**：

1. `POST /api/v1/chat/stream` → `chat_stream()` → `ask_stream()` → `_run_pipeline(ctx)`（`_api.py`）
2. `ensure_session(ctx)` + `rewrite_query_step(ctx)` 并行 —— 会话管理（`_steps_session.py`）+ Query 解析/改写（`_resolver.py` 多轮 / `_query.py` 单轮）
3. `_prewarm_query_embedding(ctx)` —— 预热 query embedding 缓存（`_api.py`）
4. `retrieve_documents(ctx)` —— 主检索编排（`_steps_retrieve.py:retrieve_documents()`）
5. `pre_rerank_dedup(ctx)` —— 预去重（`_retrieve_post.py`）
6. `rerank_documents(ctx)` —— 重排序（`_retrieve_post.py`）
7. `suppress_near_duplicates(ctx)` —— 近去重（`_retrieve_post.py`）

**在 `retrieve_documents()` 内部，根据是否有 file scope 分两条路径**：

| 条件 | 路径 | 说明 |
|------|------|------|
| `ctx.file_ids` 为空（Broad Recall） | Meeting Summary Router → File Scoping Strategy → `fair_retrieve_per_file()` → 每个文件单独调 `retrieve()` | 保证每份文件都有 chunk 进入候选池 |
| `ctx.file_ids` 非空（Scoped） | 直接调 `retrieve()` | 在限定范围内做检索 |

> **Multi-Query 与 Broad Recall 的关系**：Multi-Query 已提升到输入维度。在 `_retrieve_broad_recall()` 中，若 `MULTI_QUERY_ENABLED=true`、查询非简单查询且 `RAG_BROAD_RECALL_MULTI_QUERY_ENABLED=true`，系统会生成多条 query 变体。每条变体独立执行完整的文件选择 + `fair_retrieve_per_file()` 流程，最后将各变体的文件级结果通过 RRF 合并。因此 **Multi-Query 与 Broad Recall 可以共存**，且仍然享受 per-file fairness（每条变体各自走 fair retrieval）。若 `RAG_BROAD_RECALL_MULTI_QUERY_ENABLED=false`，则 Broad Recall 模式下禁用 Multi-Query。

---

### 2.3 两次检索与两层缓存

在 Broad Recall 的 `router_and_funnel` 策略中，存在一个常见疑问：**Funnel Wide Fetch 是否将 query 与所有 chunk 计算了一次匹配度，而后续 `fair_retrieve_per_file()` 又要对每个文件再算一次相似度？这不就重复了吗？**

#### 事实：确实发生了两次检索

从代码上看，Broad Recall 的 Layer 3 内部确实发起了两轮向量检索：

**第一轮：Funnel Wide Fetch（全局粗筛）**
```python
# _funnel_narrow.py:_wide_fetch()
docs, _ = retrieve(
    primary_query,
    meeting_ids=wide_fetch_meeting_ids,
    file_ids=None,          # ← 无 file scope，全局检索
    top_k=wide_k,           # ← 通常较大（如 50~200+）
    ...
)
```
这一步在**整个向量库**上做 ANN 搜索，目的是获取“全局范围内哪些 chunk 与 query 最相关”，进而聚合出哪些**文件**值得被选中。

**第二轮：Fair Retrieve Per File（单文件精筛）**
```python
# _fair_retriever.py:fair_retrieve_per_file()
docs, _ = retrieve(
    query,
    file_ids=[file_id],     # ← 严格限定单个文件
    top_k=per_file_fetch,   # ← per_file_fetch = max(budget*2, budget+2)
    ...
)
```
文件被 funnel narrow 选中后，系统需要为**每个文件单独检索**。因为 Wide Fetch 的 `top_k` 是全局的，单个文件可能只命中 1~2 条 chunk，不代表该文件内部没有更多相关内容。

#### 优化一：Query Embedding LRU 缓存（对冲 embedding 成本）

`backend/src/services/embedder.py` 中的 `_QueryCachedEmbeddings` 对 `embed_query` 做了线程安全的 LRU 缓存 + 请求合并（stampede protection）：

```python
def embed_query(self, text: str) -> list[float]:
    with self._cache_lock:
        hit = self._cache.get(text)
        if hit is not None:
            return hit
        ...
```

默认开启（`EMBEDDING_QUERY_CACHE_ENABLED`）。**同一句 query 的 embedding 在 Wide Fetch 和 Per-File Fetch 之间不会重复调用 Embedding API**，第二次会直接命中内存缓存。这消除了两次检索中成本最高的部分。

#### 优化二：`docs_by_file` 缓存（对冲 Chroma 调用）

`_funnel_narrow.py` 的 `narrow_scope_via_funnel()` 会将 Wide Fetch 的结果按 `file_id` 分组缓存：

```python
docs_by_file = _group_docs_by_file(docs)
return ScopeSelection(..., docs_by_file=docs_by_file)
```

下游 `_fair_retriever.py` 会检查缓存：

```python
file_cache = (cached_docs or {}).get(file_id)
if file_cache and len(file_cache) >= per_file_fetch:
    return file_cache[:per_file_fetch]   # ← 直接复用，跳过 Chroma
```

如果某个文件在 Wide Fetch 中已经命中了足够多的 chunk（≥ `per_file_fetch`），第二次检索**完全不会发生**。

#### 为什么设计上仍保留两次检索？

| 维度 | Wide Fetch | Per-File Fair Retrieve |
|------|-----------|----------------------|
| **目标** | 发现哪些文件可能相关（文件选择层） | 在已选文件内精确检索最相关 chunk（检索层） |
| **Scope** | 全局（`file_ids=None`） | 单文件（`file_ids=[fid]`） |
| **Top-K** | 大（全局 top 50~200+） | 小（每文件 budget*2，通常 4~20） |
| **Query** | 固定用 primary_query | Multi-Query 模式下各变体语义不同 |
| **结果用途** | 用于 funnel aggregate 选文件 + `docs_by_file` 缓存 | 用于最终送入 reranker 和 LLM |

两次检索的设计目标不同，不是简单的冗余。此外，Per-File Fetch 带有 `file_ids` filter，Chroma 可在 metadata 层面预过滤，搜索空间远小于全局，即使触发第二次调用，其开销也显著低于 Wide Fetch。

---

### 2.4 `retrieve()`：基础检索入口与策略选择

`backend/src/services/rag/_retriever.py:retrieve()` 是所有检索的**统一底层入口**，无论上层是否启用漏斗，最终都会走到这里。其内部流程如下：

1. **Query Analysis**：调用 `analyze_query()` 提取 speaker_names、temporal_hint、topic_query。
2. **构建 Filters**：根据 meeting_ids、file_ids、file_types、date_from/date_to 生成 Chroma/SQLite 过滤条件。
3. **解析 Provider**：通过 `_resolve_provider(rag_mode)` 将用户传入的 `rag_mode`（或系统默认配置）解析为策略名称字符串：
   - `"native"` / `"hybrid"` / `"multimodal"` / `"hybrid_multimodal"`
4. **选择策略**：调用 `select_strategy()`，将上述字符串映射为四个 `RetrievalStrategy` 实例之一。四个策略的 `run` 回调分别指向：
   - `NativeStrategy` → `_run_native_strategy`
   - `HybridStrategy` → `_run_hybrid_strategy`
   - `MultimodalStrategy` → `_run_multimodal_strategy`
   - `HybridMultimodalStrategy` → `_run_hybrid_multimodal_strategy`
5. **执行检索**：调用 `strategy.retrieve(...)`，将 query、filters、k、threshold 等参数传入 Layer 1。
6. **多样性控制**：若未指定 scope 且开启 `UNSCOPED_DIVERSITY_ENABLED`，对结果按 meeting 做上限截断，避免热门会议垄断结果。

**返回**：`(docs: list[dict], qa: QueryAnalysis)`，其中 `docs` 的元素格式为 `{"content": str, "metadata": dict, "score": float}`。

---

### 2.5 四种检索策略详解

四种策略的协议定义在 `backend/src/services/rag/_strategies.py`，但**真正的执行逻辑**在 `backend/src/services/rag/_retriever.py` 的四个 `_run_*_strategy` 函数中。

#### 2.5.1 NativeStrategy（原生策略）

`_retriever.py:_run_native_strategy()` 的行为由 `HYBRID_SEARCH_ENABLED` 和 `HYBRID_ALPHA` 共同决定：

| `HYBRID_ALPHA` | 行为 |
|---------------|------|
| `<= 0.0` | 纯 BM25（`_bm25_retrieve()`） |
| `>= 1.0` | 纯向量检索（`_vector_retrieve()`） |
| `(0.0, 1.0)` | Hybrid RRF 融合（`_hybrid_retrieve()`） |
| `HYBRID_SEARCH_ENABLED = false` | 纯向量检索 |

- **纯向量路径**：`_vector_retrieve()` 调用 Chroma `similarity_search_with_score()`，支持重试、超时回退 BM25、Parent-Child 回查、score threshold 过滤。
- **纯 BM25 路径**：`_bm25_retrieve()` 调用 SQLite FTS5 `fts5_search()`，rank 函数为负 BM25 值，返回时取反保证"越高越好"。

#### 2.5.2 HybridStrategy（强制 Hybrid）

`_retriever.py:_run_hybrid_strategy()` **无条件**调用 `_hybrid_retrieve()`，即始终做 Vector + BM25 的 RRF 融合，不受 `HYBRID_ALPHA` 边界值影响。

`_retriever.py:_hybrid_retrieve()` 的实现细节：
- 向量侧**不预滤** threshold（传入 `threshold=None`），确保 BM25-only 的结果也能参与 RRF 排名。
- 分别获取 vector_results 与 bm25_results 后，调用 `_rrf_merge()` 按排名倒数加权融合，输出 `top_k` 条结果。

#### 2.5.3 MultimodalStrategy（多模态策略）

`_retriever.py:_run_multimodal_strategy()` 优先使用 **RAGAnything**（外部多模态检索服务）：

1. 若 query 是 scoped（指定了 meeting/file）且 `RAGANYTHING_ENABLED = true`，**绕过 RAGAnything**，直接回退到 `_run_native_strategy()`。原因：RAGAnything 对精确过滤的支持较弱，scoped 查询用原生检索更准。
2. 若 `RAGANYTHING_ENABLED = false`，回退到 `_run_native_strategy()`。
3. 调用 `retrieve_with_raganything()` 获取结果；若返回空或抛异常，根据 `RAGANYTHING_FALLBACK_TO_NATIVE` 决定是否再次回退到原生策略。

#### 2.5.4 HybridMultimodalStrategy（混合多模态策略）

`_retriever.py:_run_hybrid_multimodal_strategy()` 将 **Native Vector** 与 **RAGAnything** 做 RRF 融合：

1. Scoped 查询同样绕过 RAGAnything，回退到 `_run_native_strategy()`。
2. 若 `RAGANYTHING_ENABLED = false`，回退到 `_run_hybrid_strategy()`。
3. 否则，分别获取 vector_results 与 raganything_results，通过 `_rrf_merge_multi()` 按 `HYBRID_ALPHA` 权重融合。

---

### 2.6 文件选择策略与漏斗窄化（Broad Recall 的 Layer 3）

当用户未指定 `file_ids` 时，系统通过 **Layer 3** 的文件选择架构来确定哪些文件应参与检索。该架构由 `RAG_FILE_SCOPING_MODE` 控制，核心代码位于 `backend/src/services/rag/_scoping_strategies.py`。

#### 2.6.1 为什么要结合 Summary Router 与 Funnel Wide Fetch？

因为它们查询的是**完全不同层级、不同语义空间、不同置信度**的数据，二者天然互补：

| 维度 | Summary Router（摘要路由） | Funnel Wide Fetch（块级宽搜） |
|------|-------------------------|---------------------------|
| **查询对象** | `meeting_files_summaries` 集合 | 主 `meetings` chunk 向量集合 |
| **粒度** | 每文件仅**一个摘要向量** | 每文件数十到数百个**chunk 向量** |
| **语义空间** | 文件整体主题/概要 | 文件内部具体段落细节 |
| **优势** | 极轻量、高召回文件整体相关性、不受 chunk 噪声干扰 | 能捕捉摘要未覆盖的细节、语义精确 |
| **劣势** | 摘要可能遗漏细节；对具体问题无法反映 | 全局 top_k 可能挤出“整体相关但 chunk 分数分散”的文件；受热门文件垄断 |

**单独使用任意一个都有盲区**：
- 若只用 Summary Router：用户问的是某页表格里的细节，但摘要根本没提，该文件就会被漏掉。
- 若只用 Wide Fetch：某文件整体与话题相关，但具体 chunk 分散在多个子话题中，每个 chunk 单独相似度都不高，全局 top_k 可能一条都没命中该文件，导致漏检。

因此，`router_and_funnel` 策略将两者**并发执行**，通过排名感知的融合算法让“摘要高置信”与“chunk 有证据”的文件都能进入候选池。

#### 2.6.2 四种文件选择策略

| 策略 | 名称 | 行为 |
|------|------|------|
| `router_and_funnel` | Summary Router + Funnel 并行 | 默认策略。Summary Router（基于文件摘要向量）与 Funnel Wide Fetch 并发执行，结果通过 RRF/zigzag 合并后，再经 Funnel Narrow 聚合选出 Top-N 文件。 |
| `funnel_only` | 纯漏斗 | 跳过 Summary Router，仅通过 Funnel 的 chunk-evidence 聚合选择文件。 |
| `router_pre_filter` | Router 预过滤 + Funnel | 先用 Summary Router 缩小候选 meeting 范围，再在该范围内执行 Funnel Narrow。 |
| `router_only` | 纯 Router | 仅用 Summary Router 选择文件，不执行 Funnel Wide Fetch/Narrow。保留 anchor injection 与 cap+evict 语义。 |

#### 2.6.3 Router + Funnel 的融合流程（取交集的策略）

这不是严格取交集，而是**排名感知的融合（Rank-Aware Union）**，默认策略为 **RRF**（Reciprocal Rank Fusion）。

**Step 1：并发获取两个信号**
```python
router_task = asyncio.create_task(
    _route_scope_files_with_scores(primary_query, meeting_ids, trace=trace)
)   # → [(file_id, score), ...]
wide_task = asyncio.create_task(
    asyncio.to_thread(wide_fetch_for_funnel, primary_query, meeting_ids, ...)
)   # → [chunk_doc, ...]
routed_with_scores, wide_docs = await asyncio.gather(router_task, wide_task)
```

**Step 2：将 Wide Fetch 的 chunk 证据聚合到 file 级别**
Wide Fetch 返回的是 chunk，必须先 rollup 到文件维度才能与 Router 对话：
- `aggregate_by_file_scored()` 将 chunk 分数按 file 聚合。
- 得到 `funnel_candidates`（所有有 chunk 命中的文件及分数）和 `funnel_filtered`（通过 evidence floor 过滤后的文件列表）。

**关键保护机制：`router_protected`**
```python
_protect_count = max(1, target_files // 4)
router_protected = {fid for fid, _ in router_scope[:_protect_count]}
```
Router 排名前 25% 的文件会被“保护”——即使它们在 Wide Fetch 中的 chunk evidence 很弱，也不会被 evidence floor 过滤掉。这确保了高置信度的摘要匹配不会被粗粒度的 chunk 证据 floor 误杀。

**Step 3：RRF / Zigzag 合并**
- **RRF 默认**：每个文件在 Router 列表和 Funnel 列表中的排名分别贡献 `1/(rrf_k + rank + 1)`。同时出现在两个列表中的文件获得**双倍贡献**，自然达到“交集优先”的效果。最终按 RRF 总分降序排列，取 top `target_files`。
- **Zigzag 备选**：先取交集（保留 Router 顺序），然后交替插入 Router-only 和 Funnel-only 的文件，截断到 `target_files`。

**Step 4：Anchor 注入**
若开启 `RAG_ANCHOR_BOOST_IN_BROAD_RECALL`，将 anchor file_ids 补入候选集（带 cap+evict 语义）。

**Step 5：计算 `file_scores`**
优先级：**funnel 分数 > router 分数 > anchor fallback**。归一化到 `[0, 1]`，供下游自适应 chunk 预算分配使用。

> **注意**：Funnel Narrow 的 Wide Fetch 结果会被缓存为 `docs_by_file`，供下游 `fair_retrieve_per_file()` 直接消费，避免对同一文件重复调用 Chroma（详见 2.3 节）。

#### 2.6.4 Meeting 级预过滤

若 `RAG_MEETING_SUMMARY_ROUTER_ENABLED=true`，在文件选择之前，系统会先调用 `route_meetings_by_summary()`（`_meeting_summary_vectorstore.py`）缩小候选会议范围。这发生在 `_retrieve_broad_recall()` 的早期阶段。

---

### 2.7 Broad Recall 完整流程（含 Meeting 预过滤）

当用户未指定任何 `file_ids` 时，Broad Recall 的完整执行流程如下：

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Broad Recall 全流程                              │
└─────────────────────────────────────────────────────────────────────────┘

  用户提问 "上周讨论了哪些后端 API 变更？"
       │
       ▼
  ┌────────────────────────────────────────┐
  │ Phase 0: Meeting 级预过滤               │
  │  (仅当用户未选定 meeting 时触发)        │
  │                                        │
  │  route_meetings_by_summary()           │
  │  ├── 在 meeting_summaries 集合中检索    │
  │  ├── 返回相关 meeting IDs (如 [4,7])   │
  │  └── 命中不足或失败 → fail-open (全库) │
  └────────────────────────────────────────┘
       │
       ▼  scoped_meeting_ids = [4, 7] (示例)
  ┌────────────────────────────────────────┐
  │ Phase 1: File Scoping (Layer 3)        │
  │  (由 RAG_FILE_SCOPING_MODE 决定策略)    │
  └────────────────────────────────────────┘
       │
       ├──► 并发执行 ┌─────────────────────┐
       │            │ Summary Router        │
       │            │ (file-level)          │
       │            │                       │
       │            │ 在 meeting_files_     │
       │            │ summaries 集合检索    │
       │            │ → [(file_12, 0.92),   │
       │            │    (file_8, 0.85),    │
       │            │    (file_3, 0.71)]    │
       │            └─────────────────────┘
       │                          │
       ├──► 并发执行 ┌─────────────────────┐
       │            │ Funnel Wide Fetch     │
       │            │ (chunk-level)         │
       │            │                       │
       │            │ 在 meetings 集合中    │
       │            │ 全局检索 top wide_k   │
       │            │ → [chunk_doc, ...]    │
       │            │   (约 60~200 条)      │
       │            └─────────────────────┘
       │                          │
       ▼                          ▼
  ┌────────────────────────────────────────┐
  │ Phase 2: Funnel Narrow (聚合与融合)     │
  │                                        │
  │  1. _aggregate_funnel_chunks()         │
  │     → 将 chunk rollup 到 file 级别     │
  │     → 应用 evidence floor 过滤          │
  │     → router_protected 保护 top router │
  │                                        │
  │  2. _merge_router_funnel()             │
  │     → RRF 或 zigzag 合并               │
  │     → 交集文件天然获更高权重            │
  │                                        │
  │  3. _inject_anchor()                   │
  │     → 将会话 anchor files 补入候选集   │
  │     → cap+evict 语义                   │
  │                                        │
  │  产出: ScopeSelection                  │
  │    • scope_file_ids: [12, 8, 3]        │
  │    • file_scores: {12:0.95, ...}       │
  │    • docs_by_file: {12:[...], ...}     │
  └────────────────────────────────────────┘
       │
       ▼
  ┌────────────────────────────────────────┐
  │ Phase 3: 自适应 Chunk 预算分配          │
  │                                        │
  │  compute_chunk_budget()                │
  │  → 按 file_scores + 文件大小因子       │
  │     为每份文件分配 chunk 预算           │
  │     例: file_12 得 8, file_8 得 6,     │
  │         file_3 得 4                    │
  └────────────────────────────────────────┘
       │
       ▼
  ┌────────────────────────────────────────┐
  │ Phase 4: Fair Retrieve Per File        │
  │                                        │
  │  fair_retrieve_per_file()              │
  │  → 对每个 file_id 单独发起检索          │
  │  → per_file_fetch = max(budget*2,      │
  │                       budget + 2)      │
  │  → 缓存充足时直接复用 docs_by_file      │
  │  → 并发数受 RAG_FAIR_CONCURRENCY 限制   │
  │                                        │
  │  产出: 合并后的 ctx.docs               │
  └────────────────────────────────────────┘
       │
       ▼
  ┌────────────────────────────────────────┐
  │ Phase 5: 下游过滤与后处理               │
  │                                        │
  │  Speaker Filter → Temporal Filter      │
  │       → Sibling Co-Retrieve            │
  │       → rerank_documents()             │
  │       → suppress_near_duplicates()     │
  │                                        │
  │  最终保留 10~15 条高质量 chunk          │
  │  送入 LLM 生成答案                      │
  └────────────────────────────────────────┘
```

---

### 2.8 Broad Recall 与 Fair Retrieval 详细对比

Broad Recall 与 Fair Retrieval 的逻辑封装在 Pipeline Step `backend/src/services/chain/_steps_retrieve.py:retrieve_documents()` 与 `_retrieve_broad.py:_retrieve_broad_recall()` 内。当用户未指定 `file_ids` 时触发。

**完整流程**：

1. **Meeting 级 Summary Router 预过滤**：
   - 若 `RAG_MEETING_SUMMARY_ROUTER_ENABLED=true` 且未指定 `meeting_ids`，调用 `route_meetings_by_summary()`（`_meeting_summary_vectorstore.py`）。
   - 在 `meeting_summaries` Chroma 集合中做相似度检索，返回相关 meeting IDs，缩小后续文件选择的搜索空间。

2. **文件选择策略（File Scoping）**：
   - 调用 `get_scoping_strategy()`（由 `RAG_FILE_SCOPING_MODE` 决定具体策略）。
   - 策略内部可能并发执行 Summary Router（文件级）与 Funnel Wide Fetch，最终通过 `narrow_scope_via_funnel()` 或 Router Only 逻辑输出 `ScopeSelection`（含 `file_scores` 与预取 docs 缓存）。
   - **Anchor Boost**：若开启 `RAG_ANCHOR_BOOST_IN_BROAD_RECALL`，将 anchor file_ids 补入候选集。

3. **自适应 Chunk 分配**：
   - `compute_chunk_budget()`（`_retrieve_routing.py`）按文件相关性分数 + 文件大小因子（页数/时长）为每份文件分配 chunk 预算。

4. **Fair Retrieve Per File**：
   - 调用 `backend/src/services/rag/_fair_retriever.py:fair_retrieve_per_file()`。
   - **对每个 file_id 单独发起检索**，底层直接调用 `retrieve(file_ids=[fid], top_k=per_file_fetch, ...)`。
   - 并发数受 `RAG_FAIR_CONCURRENCY` 信号量限制。
   - 每文件实际请求数 `per_file_fetch = max(budget * 2, budget + 2)`（超量 fetch），若 Funnel 已缓存足够 docs 则直接命中缓存、跳过 Chroma 调用。

5. **结果合并**：按 `(meeting_id, file_id, chunk_index)` 去重后合并为 `ctx.docs`。

#### 超量 Fetch、去重与最终截断

`retrieve()` 自身（Layer 2 → Layer 1）**没有任何去重逻辑**，它只负责忠实返回向量/BM25/Hybrid 的原始结果。去重和截断发生在更上层。Broad Recall 与非 Broad Recall 两条路径的超量策略和收敛方式完全不同，以下分别说明。

---

##### 路径一：Broad Recall（未指定 file_ids）

**Step 1 — 配额分配**：`_compute_adaptive_chunks()` 为每个文件分配一个目标配额 `budget`（如 file_A 得 6 个，file_B 得 4 个）。

**Step 2 — 逐文件超量 fetch**：在 `fair_retrieve_per_file()` 中，实际请求数远大于配额：

```python
budget = _resolve_chunks_per_file(file_id, chunks_per_file, default_chunks)
per_file_fetch = max(budget * 2, budget + 2)   # 超量 fetch

docs, _ = retrieve_fn(
    query,
    file_ids=[file_id],
    top_k=per_file_fetch,   # 实际请求的是配额的两倍（或+2）
    ...
)
```

**超量数值示例**：

| 配额 budget | 实际请求 per_file_fetch | 超量原因 |
|------------|----------------------|---------|
| 1 | `max(2, 3) = 3` | 3x，小配额时绝对余量更大 |
| 2 | `max(4, 4) = 4` | 2x |
| 4 | `max(8, 6) = 8` | 2x |
| 6 | `max(12, 8) = 12` | 2x |
| 10 | `max(20, 12) = 20` | 2x |

**为什么要逐文件超量？**
1. **为下游过滤留余量**：Speaker Filter、Temporal Filter 可能强硬过滤掉一部分命中；
2. **为跨文件去重留余量**：同一段内容可能因文件分割而出现在多个文件中（极少见但存在）；
3. **为 embedding 语义漂移留余量**：同一文件内不同 chunk 的向量分数分布不均匀，超量 fetch 确保高相关但排名稍低的 chunk 不被截断在文件内部。

**Step 3 — 跨文件去重**：`fair_retrieve_per_file()` 以 `(meeting_id, file_id, chunk_index)` 为 key 做全局去重；若 `chunk_index` 缺失，回退到内容 SHA-1 哈希前 12 位。此时总 chunk 数通常仍远大于最终需要。

**Step 4 — 下游过滤**：回到 `retrieve_documents()` 后，结果会依次经过：
- Speaker Filter（`_apply_speaker_filter()`）
- Temporal Filter（`_apply_temporal_filter()`）
- Sibling Co-Retrieval（可能增加少量 chunk）

**Step 5 — Rerank 与最终截断**：在 `rerank_documents()` 中：

```python
if is_broad:
    # Broad recall 模式下，截断下限 = max(final_top_k, distinct_file_count)
    covered_files = {文件去重后的 distinct file_id 集合}
    min_floor = max(final_top_k, len(covered_files))
    if len(ctx.docs) > min_floor:
        ctx.docs = ctx.docs[:min_floor]
```

**Broad Recall 的截断规则**：
- 上限不是固定的 `final_top_k`，而是 **`max(final_top_k, 去重后的文件数)`**。
- 这意味着如果检索覆盖了 15 个 distinct 文件，即使 `final_top_k = 10`，最终也会保留至少 15 条 chunk（配合 Per-File Guarantee 保证每个文件至少 1 条）。
- 若 reranker 未配置，则直接以 `ctx.docs` 的原始顺序截断到 `min_floor`。

---

##### 路径二：非 Broad Recall（指定了 file_ids）

**Step 1 — 全局超量系数**：在 `retrieve_documents()` 中：

```python
fetch_multiplier = settings.RAG_RERANK_FETCH_MULTIPLIER if settings.RERANKER_BINDING else 1
```

若配置了 reranker，`fetch_multiplier` 通常为 2~4（默认由配置决定）；若无 reranker，则为 1（不超量）。

**Step 2 — 单次检索超量**：Scoped 路径直接调用 `retrieve()`：

```python
# 在 retrieve() 内部
k = (top_k or settings.TOP_K) * fetch_multiplier
```

例如用户指定 `top_k = 10`，`fetch_multiplier = 3`，则 `retrieve()` 会请求 30 条 chunk。

**Step 3 — 无跨文件去重**：Scoped 查询不走 `fair_retrieve_per_file()`，因此**没有 `(meeting_id, file_id, chunk_index)` 级别的跨文件去重**。如果同一内容块在向量库中被重复索引（理论上不应发生，除非重新索引时未清理旧数据），它会以重复形式进入候选池。

**Step 4 — Rerank 与最终截断**：在 `rerank_documents()` 中：

```python
elif len(ctx.docs) > final_top_k:
    ctx.docs = ctx.docs[:final_top_k]
```

**非 Broad Recall 的截断规则**：
- **严格截断到 `final_top_k`**，不考虑文件覆盖数。
- 因为用户已经显式缩小了范围（指定了 file），系统认为不需要再保证 per-file 保底。
- 若 reranker 未配置（`fetch_multiplier = 1`），`retrieve()` 返回的 chunk 数已经接近 `final_top_k`，截断损失很小。

---

##### 两条路径的截断对比

| 维度 | Broad Recall | 非 Broad Recall（Scoped） |
|------|-------------|-------------------------|
| **超量发生时机** | 逐文件（`per_file_fetch = max(budget*2, budget+2)`） | 全局（`k = top_k * fetch_multiplier`） |
| **超量控制参数** | 硬编码公式（`_fair_retriever.py`） | `RAG_RERANK_FETCH_MULTIPLIER`（配置项） |
| **去重环节** | `fair_retrieve_per_file()` 按 chunk_index/内容哈希 | 无专门跨文件去重（依赖向量库不重复索引） |
| **最终截断策略** | `max(final_top_k, distinct_files)`，保证每文件至少 1 条 | 严格 `final_top_k`，不保证文件覆盖 |
| **截断代码位置** | `rerank_documents()` 中 `is_broad=True` 分支 | `rerank_documents()` 中 `is_broad=False` 分支 |
| **常见最终 chunk 数** | 常大于 `final_top_k`（受文件数驱动） | 等于或略大于 `final_top_k`（仅由 rerank pool 宽度驱动） |

---

##### Multi-Query 分支的特殊处理

在 Multi-Query 模式下（`MULTI_QUERY_ENABLED=true` 且 `RAG_BROAD_RECALL_MULTI_QUERY_ENABLED=true`），系统生成多条 query 变体。在 Broad Recall 中，**每条变体独立执行完整的文件选择策略 + `fair_retrieve_per_file()` 流程**，返回的结果先经过 `_dedup_docs()` 按内容 SHA-256 去重，然后截断：

```python
merged = _dedup_docs(all_docs, lower_is_better=lower_is_better)
_sort_docs_by_score(merged, lower_is_better=lower_is_better)
ctx.docs = merged[: effective_k * fetch_multiplier]
```

- Multi-Query 的去重发生在**变体间**（不同 query 可能召回同一块），而非文件间。
- 截断点是 `effective_k * fetch_multiplier`，与 Scoped 路径一致，**不受 distinct files 数驱动**。

> **关键点**：`fair_retrieve_per_file()` 的每次内部调用都会独立走一遍完整的 Layer 2 → Layer 1 → Layer 0 链路，**除非缓存命中**。Broad Recall 时，文件选择策略（Layer 3）中的 Funnel Narrow 已经完成了 Wide Fetch 和 Meeting/File 聚合，`fair_retrieve_per_file()` 只是负责在已选文件范围内逐文件精准检索。

---

### 2.9 Rerank 与后处理

Rerank 是 `retrieve_documents()` 执行完毕后的**独立步骤**，不侵入检索内部逻辑。

#### 2.9.1 `rerank_documents()`

`backend/src/services/chain/_retrieve_post.py:rerank_documents()` 的触发与行为：

- **跳过条件**：未配置 `RERANKER_BINDING`、候选集为空、候选数 ≤ `final_top_k`、或仅单文件 scope。
- **后端选择**：`cohere`（Cohere Rerank API）或 `bge`（本地 BGE CrossEncoder）。
- **Pool 宽度**：`RERANKER_TOP_N` 控制 reranker 保留数；Broad recall 模式下自动扩展为 `max(RERANKER_TOP_N, distinct_files)`，确保每文件都有机会进入 rerank。
- **后处理**：
  - **Score 截断**：低于 `RERANKER_MIN_SCORE` 过滤；若全低于阈值，保留 top_n 防止空结果。
  - **Per-File Guarantee**：Broad recall 模式下，强制保证每个 distinct `file_id` 至少保留 1 条。
  - **Content-Type Bias**：若 query 含 "table"/"图" 等关键词，对对应类型 chunk 加分。

#### 2.9.2 `suppress_near_duplicates()`

`backend/src/services/chain/_retrieve_post.py:suppress_near_duplicates()` 基于 **4-gram 重叠率**（阈值 0.85）剔除内容高度相似的 chunk，保留排名最高的一条。随后调用 `_filter_low_information_chunks()` 丢弃页码/版权页等低信号内容。

---

### 2.10 场景示例：Broad Recall + Router-and-Funnel + Hybrid + Rerank

**用户行为**：打开首页，未选择任何 meeting/file，提问 "上周讨论了哪些后端 API 变更？"

**配置**：`RAG_FILE_SCOPING_MODE="router_and_funnel"`，`RAG_MEETING_SUMMARY_ROUTER_ENABLED=true`，`MULTI_QUERY_ENABLED=false`，`HYBRID_SEARCH_ENABLED=true`，`HYBRID_ALPHA=0.7`，`RERANKER_BINDING=cohere`

**流程**：

1. `rewrite_query_step()` 改写 query 为更利于检索的表述。
2. `retrieve_documents(ctx)` 进入 **Broad Recall** 分支：
   - `route_meetings_by_summary()` 在 meeting 摘要集合中检索，返回相关 meeting IDs（如 `meeting_4`、`meeting_7`）。
   - `get_scoping_strategy()`（`router_and_funnel`）并发执行：
     - **Summary Router**（文件级）在 `meeting_files_summaries` 中检索，返回 `[(file_12, 0.92), (file_8, 0.85), (file_3, 0.71)]`。
     - **Funnel Wide Fetch** 全局检索，拿到约 60 条候选 chunk。
     - `narrow_scope_via_funnel()` 对 wide fetch 结果按 meeting/file 聚合，与 router 结果 RRF 合并，最终选出 `file_12`、`file_8`、`file_3`。
   - `compute_chunk_budget()` 按分数分配：file_12 得 8 个 chunk，file_8 得 6 个，file_3 得 4 个。
   - `fair_retrieve_per_file()` 对每个文件并发调用 `retrieve(file_ids=[fid], top_k=per_file_fetch)`，其中 `per_file_fetch = max(budget*2, budget+2)`。若 funnel 已缓存该文件 docs 且数量充足，直接命中缓存。
   - 三份文件结果合并、按 `(meeting_id, file_id, chunk_index)` 去重，写入 `ctx.docs`。
3. `rerank_documents(ctx)`：
   - Cohere reranker 对全部候选重排序；Broad recall 模式下 pool 自动扩展覆盖 3 个文件。
   - Per-File Guarantee 确保每个文件至少保留 1 条；Score 截断过滤低分。
4. `suppress_near_duplicates(ctx)`：4-gram 去重，最终保留 12 条高质量 chunk 送入 LLM。

---

## 3. 配置项速查表

| 配置项 | 作用域 | 说明 |
|-------|-------|------|
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | Chunk | 标准分块大小与重叠 |
| `CHILD_CHUNK_SIZE` / `CHILD_CHUNK_OVERLAP` | Chunk | Parent-Child 模式下的子块参数 |
| `SEMANTIC_CHUNKING_ENABLED` | Chunk | 是否启用结构感知拆分 |
| `PARENT_CHILD_ENABLED` | Chunk / Retrieval | 是否启用父子两级分块与 parent 回查 |
| `NON_TEXT_CHUNKING_STRATEGY` | Chunk | 非文本文件（segment/page）的 chunk 路径：`"native"`（默认，走原生入口）或 `"text"`（展平为纯文本后走 `index_meeting()`） |
| `HYBRID_SEARCH_ENABLED` | Retrieval | 是否启用 BM25 + Vector Hybrid |
| `HYBRID_ALPHA` | Retrieval | Hybrid 时 vector 侧权重（0=纯BM25, 1=纯vector） |
| `TOP_K` | Retrieval | 默认返回 chunk 数 |
| `RERANKER_BINDING` | Rerank | 重排序后端：`cohere` / `bge` / 空 |
| `RERANKER_TOP_N` | Rerank | Reranker 保留的候选数 |
| `RERANKER_MIN_SCORE` | Rerank | 最低接受分数 |
| `RAG_HIERARCHICAL_ENABLED` | Retrieval | 是否启用 funnel narrow 等分层逻辑（影响 scoping strategy 内部行为） |
| `RAG_FILE_SCOPING_MODE` | Retrieval | Broad Recall 文件选择策略：`router_and_funnel` / `funnel_only` / `router_pre_filter` / `router_only` |
| `RAG_MEETING_SUMMARY_ROUTER_ENABLED` | Retrieval | 是否启用 meeting 级摘要预路由（在文件选择前缩小候选会议范围） |
| `RAG_BROAD_RECALL_MULTI_QUERY_ENABLED` | Retrieval | Broad Recall 模式下是否允许 Multi-Query |
| `RAG_FUNNEL_FETCH_MULTIPLIER` | Retrieval | Funnel Wide Fetch 超量倍数 |
| `RAG_FUNNEL_TOP_MEETINGS` / `TOP_FILES` | Retrieval | Funnel Meeting/File 选择上限 |
| `RAG_ANCHOR_ENABLED` | Retrieval | 是否启用会话 anchor 记忆 |
| `RAG_SUMMARY_ROUTER_ENABLED` | Retrieval | 文件级摘要预路由开关（由 scoping strategy 内部使用） |
| `MULTI_QUERY_ENABLED` | Retrieval | 是否启用多查询变体召回 |
| `QUERY_REWRITE_MODEL` | Query | 查询改写专用模型 |

---

## 4. 附录：核心文件索引

| 文件 | 职责 |
|-----|------|
| `backend/src/services/rag/_indexer.py` | Chunk 主入口（flat/parent-child/page/segment） |
| `backend/src/services/rag/_chunkers.py` | 结构感知拆分逻辑 |
| `backend/src/services/rag/_indexer_extract.py` | 页面级元数据、表格、图片提取辅助 |
| `backend/src/services/rag/_indexer_store.py` | 向量/BM25 写入、去重、删除 |
| `backend/src/services/rag/_retriever.py` | 检索主入口、策略执行 |
| `backend/src/services/rag/_strategies.py` | 检索策略协议与选择器 |
| `backend/src/services/rag/_vector.py` | Parent-Child 回查、分数方向判断 |
| `backend/src/services/rag/_vectorstore.py` | Chroma 向量库单例与写入锁 |
| `backend/src/services/rag/_bm25.py` | BM25 FTS5 检索封装 |
| `backend/src/services/rag/_bm25_maintenance.py` | BM25 索引维护（drift 检测、rebuild） |
| `backend/src/services/rag/_funnel.py` | 漏斗聚合、Score 归一化、Title Prior |
| `backend/src/services/rag/_funnel_narrow.py` | Funnel Narrow 文件选择逻辑 |
| `backend/src/services/rag/_scoping_strategies.py` | Broad Recall 文件选择策略（4 种模式） |
| `backend/src/services/rag/_fair_retriever.py` | 每文件公平检索与并发控制 |
| `backend/src/services/rag/_summary_router.py` | 基于文件摘要向量的预路由 |
| `backend/src/services/rag/_summary_vectorstore.py` | 文件摘要向量存储 |
| `backend/src/services/rag/_meeting_summary_vectorstore.py` | Meeting 级摘要向量存储与预路由 |
| `backend/src/services/rag/_scope_types.py` | ScopeSelection、BroadRecallContext 等类型定义 |
| `backend/src/services/rag/_routing.py` | 检索路由辅助函数 |
| `backend/src/services/rag/_filters.py` | Chroma/SQLite filter 构建 |
| `backend/src/services/rag/_query.py` | Query 改写、自适应 top_k、意图检测 |
| `backend/src/services/rag/_query_analysis.py` | Speaker/Temporal 提取、主题净化 |
| `backend/src/services/rag/_reranker.py` | Cohere / BGE 重排序、Per-File Guarantee |
| `backend/src/services/rag/_rrf.py` | RRF 融合算法 |
| `backend/src/services/rag/_anchor_inject.py` | Anchor file 注入与 TTL 管理 |
| `backend/src/services/rag/_raganything.py` | RAGAnything 多模态检索桥接 |
| `backend/src/services/chain/_api.py` | Pipeline 编排主入口（ask、_run_pipeline） |
| `backend/src/services/chain/_api_stream.py` | 流式 Pipeline 编排（ask_stream、StreamBus） |
| `backend/src/services/chain/_steps_retrieve.py` | Pipeline 中 retrieve_documents 编排 |
| `backend/src/services/chain/_retrieve_broad.py` | Broad Recall / Scoped 检索逻辑 |
| `backend/src/services/chain/_retrieve_post.py` | rerank、pre_rerank_dedup、suppress_near_duplicates |
| `backend/src/services/chain/_retrieve_filters.py` | Speaker/Temporal/Content-Type 过滤与 bias |
| `backend/src/services/chain/_retrieve_routing.py` | Chunk 预算分配、speaker 查找 |
| `backend/src/services/chain/_retrieve_utils.py` | 常量、评分、去重、低信息过滤 |
| `backend/src/services/chain/_steps_context.py` | Memory/Session/Entity/Web/History 上下文加载 |
| `backend/src/services/chain/_steps_generate.py` | build_context、generate_answer、save_messages |
| `backend/src/services/chain/_steps_session.py` | ensure_session、rewrite_query_step |
| `backend/src/services/chain/_generate_helpers.py` | Token stripping、retry wrappers、circuit breaker |
| `backend/src/services/chain/_formatting.py` | Source 提取、文档格式化 |
| `backend/src/services/chain/_extraction.py` | Combined fact + entity extraction |
| `backend/src/services/chain/_resolver.py` | History-aware query resolution（多轮） |
| `backend/src/services/chain/_routing.py` | Intent classification（casual vs retrieval） |
| `backend/src/services/chain/_anthropic_cache.py` | Anthropic prompt caching helper |
| `backend/src/services/chain/_context.py` | PipelineContext、PipelineResult 定义 |
| `backend/src/services/chain/_per_file_summary.py` | Per-file summary 生成与缓存 |
| `backend/src/services/chain/_speaker_context.py` | Speaker utterance block 构建 |
| `backend/src/api/routers/chat.py` | Chat 流式/同步 API 路由 |
| `backend/src/services/processor/_pipeline.py` | 多文件处理管道（触发 chunk 的核心调度） |
| `backend/src/services/processor/_pipeline_meeting.py` | 单文件处理管道（旧版兼容路径） |
