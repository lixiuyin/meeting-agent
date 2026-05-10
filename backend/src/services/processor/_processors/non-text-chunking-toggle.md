# 非文本文件 Chunk 路由开关 — 设计与改动说明


---

## 1. 开关是什么

### 设置项

| 字段 | 值 | 默认 | 含义 |
|---|---|---|---|
| `rag.non_text_chunking_strategy` | `"native"` \| `"text"` | `"native"` | 非文本文件 ingest 时的 chunk 路径选择 |

### 取值语义

- `native`（默认，等价于改动前的行为）：每种模态走自己的专用 chunk 策略
  - audio/video → `index_meeting_segments()`（segment-aware，含语义边界 + speaker 对齐 + embedding 复用）
  - pdf/ppt/doc/xls/csv → `index_meeting_pages()`（page-aware，每页一块 + 表格/图片独立成块）
  - image → `index_meeting_segments()`（caption+OCR 单段）
  - txt/md → `index_meeting()`（纯文本 flat / parent-child）
- `text`：所有非文本文件的 `artefact.text` 走纯文本 chunk 入口（`index_meeting()`），与
  txt/md 走同一条管道。txt/md 本身不受影响。

### 触发流程（保护机制）

切换这个开关会要求 `confirm_vector_rebuild=true`，否则 `PUT /api/v1/settings` 会被
返回 409。原因：旧 chunk 与新策略下产生的 chunk 的 ID 前缀、metadata、粒度不一致，
**新数据写入前需要先清掉对应文件的旧 chunk**。

---

## 2. 改动总览

未 commit 的代码改动如下（12 个文件）。按"开关基础设施"、"路由实现"、"信号增强"、
"rebuild 修复"、"前端"、"测试" 六组分类。

### 2.1 开关基础设施

| 文件 | 改动 |
|---|---|
| [backend/config/main.yaml](../../../../config/main.yaml) | `rag` 段下新增 `non_text_chunking_strategy: native` 默认值 |
| [backend/src/core/config.py](../../../core/config.py) | 新增 `NON_TEXT_CHUNKING_STRATEGY: str` settings 字段（来自 YAML） |
| [backend/src/models/schemas/settings.py](../../../models/schemas/settings.py) | `RAGSettings` 新增同名字段；`@field_validator` 校验取值只能是 `{native, text}`，自动 lowercase |
| [backend/src/api/routers/settings/__init__.py](../../../api/routers/settings/__init__.py) | `_rebuild_required` 加入此键 → 切换会强制要求 rebuild 确认；`_update_settings_in_memory` 写回；`_get_current_settings` 暴露给响应 |

### 2.2 路由实现（核心）

| 文件 | 改动 |
|---|---|
| [backend/src/services/processor/_pipeline.py](../_pipeline.py) | 新增 `_should_route_artefact_to_text_chunking(artefact)`、`_format_timestamp_label(seconds)`、`_build_text_route_payload(artefact)` 三个辅助函数；text-route 分支在原有的 `index_meeting_*` 三选一前面加了短路；命中时先 `delete_meeting_chunks(meeting_id, file_id=...)` 再 `index_meeting()`；metadata 注入 `chunk_strategy_route ∈ {native, text}` 用作可观测性 |

`_pipeline.py` 路由层的实际分发逻辑：

```
if NON_TEXT_CHUNKING_STRATEGY == "text" and (segments 非空 or parsed_doc 非空):
    route_text = _build_text_route_payload(artefact)
    delete_meeting_chunks(meeting_id, file_id=...)
    index_meeting(text=route_text)              # 文本链路（flat / parent-child）
elif segments 非空:
    index_meeting_segments(...)                 # native 链路：音视频 / 图片
elif parsed_doc 非空:
    index_meeting_pages(...)                    # native 链路：文档
else:
    index_meeting(text=artefact.text)           # 纯文本（txt/md），原行为不变
```

### 2.3 信号增强（让 text 路由不丢关键信息）

| 文件 | 改动 |
|---|---|
| [backend/src/services/parser/types.py](../../parser/types.py) | `ParsedDocument` 新增 `to_indexable_text()`：保留 page text + 表格 markdown + 图片 caption/OCR（与原 `to_text()` 并存，不动旧 page-aware 主线） |
| [backend/src/services/processor/_pipeline.py](../_pipeline.py) | `_build_text_route_payload()` 给 audio/video 注入 `[hh:mm:ss] speaker:` 前缀；给 document 调 `to_indexable_text()`；image 单段直接用 `artefact.text`（caption+OCR） |

### 2.4 rebuild 修复（与开关无直接关系，但被一起带出来的 bug）

| 文件 | 改动 |
|---|---|
| [backend/src/api/routers/settings/_rebuild.py](../../../api/routers/settings/_rebuild.py) | `_rebuild_vectors_task` 改为按 `meeting_files` 粒度查询：`JOIN meetings` 拿标题/日期 → 每个文件独立 `delete_meeting_chunks(meeting_id, file_id)` + `index_meeting(meeting_id, text, metadata={file_id, file_name, file_type, title, meeting_date, chunk_strategy_route="text"})`。修复"多文件会议被拍成一坨"的旧 bug |

### 2.5 前端

| 文件 | 改动 |
|---|---|
| [frontend/src/api/client-settings.ts](../../../../../frontend/src/api/client-settings.ts) | `RAGSettings` 接口加 `non_text_chunking_strategy: "native" \| "text"` |
| [frontend/src/views/settings/RagTab.tsx](../../../../../frontend/src/views/settings/RagTab.tsx) | RAG 设置页加下拉选择 + helper 文案 |
| [frontend/src/i18n/messages.ts](../../../../../frontend/src/i18n/messages.ts) | 中英双语 key：`settings.rag.nonTextChunkingStrategy*` |

### 2.6 测试

| 文件 | 改动 |
|---|---|
| [backend/tests/test_ingest_trace.py](../../../../tests/test_ingest_trace.py) | 新增两个测试：`test_process_meeting_file_routes_audio_artefact_text_through_text_chunking`、`test_process_meeting_file_routes_document_artefact_text_through_text_chunking`，验证开关打开后 audio/document 都改走 `index_meeting()`，且 metadata 里 `chunk_strategy_route="text"`，audio 文本带 `[mm:ss]` 时间戳前缀 |
| [backend/tests/test_settings_rebuild_check.py](../../../../tests/test_settings_rebuild_check.py) | 新增 `test_non_text_chunking_strategy_change` 验证切换该开关会触发 rebuild 要求 |

---

## 3. 如何切换不同 Chunk 策略

RAG 一共有 **三个相互独立** 的 chunk 维度，按"影响范围"由大到小排列：

### 维度 1：非文本文件的整体路由（本次新增，最外层）

```http
PUT /api/v1/settings
Content-Type: application/json
X-API-Key: <key>

{
  "rag": { "non_text_chunking_strategy": "text" },
  "confirm_vector_rebuild": true
}
```

- `native` ↔ `text`，影响 audio / video / pdf / ppt / doc / image。
- 切完只对**新上传**或**主动 reprocess** 的文件生效。

### 维度 2：纯文本路径下的 Flat ↔ Parent-Child

```http
PUT /api/v1/settings
{
  "rag": {
    "parent_child_enabled": true,
    "child_chunk_size": 500,
    "child_chunk_overlap": 50
  }
}
```

- 影响 `index_meeting()` 的内部行为。
- 维度 1 切到 `text` 后，所有非文本文件也吃这个开关。
- **不会**触发 rebuild 强制确认（不在 `_rebuild_required` 列表里）。

### 维度 3：Flat 模式下的结构感知预切

```http
PUT /api/v1/settings
{
  "rag": { "semantic_chunking_enabled": true }
}
```

- 仅作用于 flat 路径（`parent_child_enabled=false` 时）。
- 用 `_split_by_structure()` 按 Markdown 标题、`Speaker N` 标签、编号列表等粗切，再按 `chunk_size` 细切。

### 让旧数据应用新策略

| 想要的效果 | 推荐操作 | 备注 |
|---|---|---|
| 让旧文件按新 chunk_size / parent_child / `non_text_chunking_strategy=text` 重切 | `POST /api/v1/settings/rebuild-vectors` | 已修复为按文件粒度，从 `meeting_files.transcript` 重切。**不重新转录/解析**，所以 audio 时间戳前缀和 document 表格 markdown 这些只在 ingest 阶段才能拿到的信号不会回来 |
| 让旧文件回到 native（恢复 segment-aware / page-aware） | `POST /api/v1/meetings/{meeting_id}/reprocess` 或单文件 `.../files/{file_id}/reprocess` | 完整重跑 ingest pipeline，会重新调 ASR / parser，**有外部 API 成本** |

---

## 4. 旧路线 vs 新策略：信号保留对照

下表对每种文件类型，比较 `non_text_chunking_strategy=native`（旧路线，原行为）
和 `non_text_chunking_strategy=text`（新策略，统一走文本 chunk）的**信号差异**。

### 4.1 Audio / Video（AVFileProcessor）

| 项目 | native（segment-aware） | text（统一文本 chunk） |
|---|---|---|
| Chunk 入口 | `index_meeting_segments()` | `index_meeting()` |
| Chunk 切分逻辑 | 基于相邻段 embedding 余弦相似度做语义边界检测 + 字符上限 | 基于字符的递归切分，可叠加 `parent_child_enabled` |
| Speaker 对齐 | ✅ 每个 chunk 有 `speaker` / `speakers_in_chunk` metadata，跨段继承 | ⚠️ 只在文本里有 `Speaker:` 行首前缀，metadata 里没有结构化 speaker 字段 |
| 时间戳 | ✅ 每个 chunk 有 `timestamp_start` / `timestamp_end` / `time_position_ratio` metadata | ⚠️ 只在文本里有 `[mm:ss]` 前缀（本次新增 `_build_text_route_payload()` 注入），metadata 里没有时间字段 |
| Embedding 成本 | ✅ chunk 向量 = 段向量平均，复用 segment embedding，省一次 embedding API | ❌ 重新对每个 chunk 调 embedding API |
| Temporal Filter（"会议开头"、"前 30 分钟"等） | ✅ 命中 `time_position_ratio` 硬过滤 | ❌ 失效（无 metadata 时间字段）；但文本中的 `[mm:ss]` 前缀仍能被 BM25 / 向量召回到 |
| Speaker Filter（"Alice 说了什么"） | ✅ 命中 `speaker` 硬过滤 | ⚠️ 退化为内容匹配（"Alice:" 子串） |
| Audio Chunking 专项设置（`AUDIO_SEMANTIC_BOUNDARY_*`、`AUDIO_SPLIT_ON_SPEAKER_CHANGE`） | ✅ 全部生效 | ❌ 全部失效（不走 segment 路径） |
| 受 `parent_child_enabled` 影响 | ❌ 不受 | ✅ 受 |
| 受 `semantic_chunking_enabled` 影响 | ❌ 不受（segment-aware 自带语义边界） | ✅ 受 |

**信号损失评估**：中等。speaker / 时间戳信息在文本中保留，但 metadata 维度的硬过滤
（temporal filter、按 speaker 过滤）会失效，等价为依赖文本本身被召回。

### 4.2 PDF / PPT / DOC / XLS / CSV（DocumentFileProcessor）

| 项目 | native（page-aware） | text（统一文本 chunk） |
|---|---|---|
| Chunk 入口 | `index_meeting_pages()` | `index_meeting()` |
| 切分粒度 | 每页 ≤ `CHUNK_SIZE` 整页一块，否则递归切 | 全文按字符递归切，**忽略页边界** |
| 页码 | ✅ `page_number` metadata | ❌ 无 |
| Heading path | ✅ `heading_path` metadata | ❌ 无 |
| 表格 | ✅ 表格 markdown 独立成块，`content_type="table"` | ⚠️ 表格 markdown 拼到正文中（本次新增 `to_indexable_text()`），可能被切散，**没有独立的 table chunk** |
| 图片 caption | ✅ 独立 chunk，`content_type="image_caption"` 或 `image_combined` | ⚠️ 拼到正文中（同上） |
| 图片 OCR | ✅ 独立 chunk，`content_type="image_ocr"` 或 `image_combined`，受 `RAG_IMAGE_OCR_MIN_LENGTH` 过滤 | ⚠️ 拼到正文中，**不受 OCR 长度阈值过滤**（短噪声 OCR 也会进入） |
| Image 资产路径 | ✅ `image_storage_path` / `image_thumbnail_path` metadata，前端能直接渲染缩略图 | ❌ 无（资产路径丢失） |
| Reranker Content-Type Bias（query 含"table"/"图"） | ✅ 命中 `content_type` 加分 | ❌ 失效 |
| `RAG_INDEX_TABLES` / `RAG_INDEX_IMAGE_CAPTIONS` 设置 | ✅ 控制是否独立成块 | ❌ 无效（统一拼到正文） |
| 受 `parent_child_enabled` 影响 | ❌ 不受（page-aware 用自己的 splitter） | ✅ 受 |

**信号损失评估**：较大。文本内容（含表格 markdown、caption、OCR）在新增的
`to_indexable_text()` 帮助下基本不丢，但**结构化 metadata（页码、content_type、图片路径）
全部丢失**，导致前端"跳到第几页"、"展示图片缩略图"、reranker 的 table/image bias 都失效。

### 4.3 Image（ImageFileProcessor）

| 项目 | native（segment-aware，单段） | text（统一文本 chunk） |
|---|---|---|
| Chunk 入口 | `index_meeting_segments()`（caption+OCR 作为单段） | `index_meeting()` |
| 切分 | 单段直接成块 | 内容超过 `CHUNK_SIZE` 时按字符切 |
| metadata `speaker="image"` | ✅ 有 | ❌ 无（`_build_text_route_payload` 显式跳过 `image` speaker 前缀） |
| 内容 | caption 和 OCR 用 `\n\n` 拼接 | 同 native（fallback 到 `artefact.text`） |

**信号损失评估**：很小。内容完全一致，主要是 metadata 上的 `speaker="image"` 标签丢失。

### 4.4 Txt / Md（TextFileProcessor）

完全不受 `non_text_chunking_strategy` 影响。两种模式下都走 `index_meeting()`。

---

## 5. 切换语义三连问

### 5.1 选了 `text` 之后所有文件都走 text chunk 吗？此时怎么再选不同的 text chunk 策略？

**是的，所有文件最终都进入 `index_meeting()` 这一条路。** 详细分发（来自
[_pipeline.py](../_pipeline.py)）：

| 文件类型 | `non_text_chunking_strategy=text` 下的实际路径 |
|---|---|
| audio / video | `_build_text_route_payload()` 注入时间戳 → `index_meeting()` |
| pdf / ppt / doc / xls / csv | `to_indexable_text()` 拼上表格+caption → `index_meeting()` |
| image | `artefact.text`（caption+OCR）→ `index_meeting()` |
| txt / md | `artefact.text` → `index_meeting()`（本来就是这条） |

进入 `index_meeting()` 后（[_indexer.py:78-88](../../rag/_indexer.py#L78)）由两个独立开关
决定细分策略：

```python
if PARENT_CHILD_ENABLED:
    _index_parent_child(...)        # 父子两级
else:
    _index_flat(...)                # 内部再看 SEMANTIC_CHUNKING_ENABLED
```

所以 text 路由下你能选的"text chunk 子策略"实际是 **3 种组合**：

| 组合 | `parent_child_enabled` | `semantic_chunking_enabled` | 行为 |
|---|---|---|---|
| Flat（默认） | false | false | 纯字符递归切，按 `chunk_size`/`chunk_overlap` |
| Flat + Semantic | false | true | 先按 Markdown 标题 / `Speaker N` / 编号列表粗切，再字符细切 |
| Parent-Child | true | （被忽略） | 父块 `chunk_size`、子块 `child_chunk_size`，检索命中 child 后回查 parent |

**一次切到位的写法**：

```http
PUT /api/v1/settings
{
  "rag": {
    "non_text_chunking_strategy": "text",
    "parent_child_enabled": true,
    "chunk_size": 1500,
    "chunk_overlap": 200,
    "child_chunk_size": 500,
    "child_chunk_overlap": 50
  },
  "confirm_vector_rebuild": true
}
```

注意：`parent_child_enabled` / `semantic_chunking_enabled` 不在 `_rebuild_required`
列表里，单独切它们**不会**触发 rebuild 强制确认；但同请求里若也切了
`non_text_chunking_strategy`，整个请求就需要 `confirm_vector_rebuild=true`。

### 5.2 先上传文件还是先选开关？

两种顺序都能用，但语义不同：

| 顺序 | 行为 | 适用 |
|---|---|---|
| **先选开关 → 再上传**（推荐） | 上传时直接按新策略入库，**没有任何重切成本** | 干净的 baseline 实验、新数据 |
| **先上传 → 再切开关** | 已上传的文件**不会自动重切**，仍按上传时的策略存在向量库里 | 需要对历史数据做对比 |

后者要让旧文件应用新策略，必须显式触发"重切"（见 5.3）。

### 5.3 上传后能不能再切到别的策略？

**能切，但路径分两种，覆盖度不同。**

#### 路径 A：`POST /api/v1/settings/rebuild-vectors`（轻量、免费、快）

```http
POST /api/v1/settings/rebuild-vectors
X-API-Key: <key>
```

- 行为（已修复为按文件粒度）：从 `meeting_files.transcript`（已经存好的纯文本）逐文件
  `delete_meeting_chunks(meeting_id, file_id)` + `index_meeting()`，**不重新调 ASR/parser**。
- 覆盖范围：
  - ✅ 切 `chunk_size` / `chunk_overlap` / `parent_child_enabled` /
    `semantic_chunking_enabled` —— 全生效。
  - ✅ 从 `native` 切到 `text` —— rebuild 出来的就是 text 路径的 chunk。
  - ⚠️ **但本次新增的"信号增强"拿不回来**：rebuild 用的是数据库里的 `transcript` 纯文本，
    没有 `segments` / `parsed_doc`，所以 audio chunk 不会带 `[mm:ss]` 前缀，
    document chunk 也不会有表格 markdown。
  - ❌ 从 `text` 切回 `native` —— **rebuild 做不到**，因为 native 需要
    `segments`（ASR 产物）/ `parsed_doc`（parser 产物），rebuild 没有这些。

#### 路径 B：`POST /api/v1/meetings/{id}/reprocess` 或 `/files/{fid}/reprocess`（完整、贵）

```http
POST /api/v1/meetings/<meeting_id>/reprocess
# 或单文件
POST /api/v1/meetings/<meeting_id>/files/<file_id>/reprocess
```

- 行为：完整重跑 `process_meeting_file()`，包括重新 ASR、重新 parser、重新 vision caption。
- 覆盖范围：
  - ✅ 任何方向切换全生效，**包括 `text` → `native`**。
  - ✅ 信号增强（audio 时间戳前缀、document 表格 markdown）也会回来。
  - ❌ 有外部 API 成本：AssemblyAI 按音频时长收费、mineru.net / aistudio
    按文档页数收费。

#### 决策表

| 你的目标 | 用哪条 |
|---|---|
| 切 `non_text_chunking_strategy=text` 让旧数据应用 | rebuild-vectors（接受信号弱化）或 reprocess（信号完整但花钱） |
| 切回 `native` 让旧数据应用 | **必须** reprocess |
| 改 chunk_size / parent_child / semantic_chunking 让旧数据应用 | rebuild-vectors |
| 改完后只测**新文件** | 什么都不用做，直接上传新文件 |

### 5.4 标准 A/B 实操 cookbook

```bash
# Step 1: 默认 native，上传文件
curl -X POST -F "file=@meeting.mp4" /api/v1/meetings/upload
# (记录 meeting_id 和 file_id)

# Step 2: 跑一组查询作为 baseline
curl -X POST /api/v1/chat -d '{"query": "...", "meeting_ids": [<id>]}' \
  | jq '.sources[].metadata.chunk_strategy_route'  # 应为 "native"

# Step 3: 切到 text，同时选 parent-child
curl -X PUT /api/v1/settings -d '{
  "rag": {
    "non_text_chunking_strategy": "text",
    "parent_child_enabled": true
  },
  "confirm_vector_rebuild": true
}'

# Step 4: 让这个文件应用新策略（信号完整版）
curl -X POST /api/v1/meetings/<meeting_id>/files/<file_id>/reprocess

# Step 5: 跑同一组查询
curl -X POST /api/v1/chat -d '{"query": "...", "meeting_ids": [<id>]}' \
  | jq '.sources[].metadata.chunk_strategy_route'  # 应为 "text"
```

#### 检查某条 chunk 走的是哪条路径

`/api/v1/chat` 响应中的 `sources[].metadata` 里会有
`chunk_strategy_route ∈ {native, text}`。也可以直接查 Chroma 集合 metadata。

---

## 6. 已知限制 / 后续可做

1. **`/settings/rebuild-vectors` 仍是"半招"**：本次维度 X 修复了"多文件会议被拍扁"
   的 bug，但它**不会重新调 ASR/parser**，所以切了 `non_text_chunking_strategy=text` 之后
   rebuild 出来的 audio chunk 不会带 `[mm:ss]` 前缀（要回这部分信号需 reprocess）。
   反向（text → native）也只能靠 reprocess。
2. **粒度只有全局开关**：目前 `non_text_chunking_strategy` 是全模态一刀切。如果想做
   "audio 走 text、PDF 保留 page-aware"这样的精细对照，需要拆成
   `audio_chunking_route` / `document_chunking_route` 两个独立枚举。
3. **Reranker 的 content-type bias 在 text 路由下失效**：query 含 "表格"/"图" 时不会
   再加分。现在没解决，因为 text 路径下不再产出独立的 table/image chunk。
