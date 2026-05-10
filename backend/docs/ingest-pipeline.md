# 数据摄入管线（Ingest Pipeline）

> 从用户点击「上传」到内容可被 RAG 检索之间的全链路说明。
>
> 代码位置：
> `backend/src/api/routers/meetings/_upload.py` · `backend/src/services/processor/`（含 `_pipeline.py`、`_pipeline_meeting.py`、`_pipeline_summary.py`、`_pipeline_common.py`、`_recovery.py`）·
> `backend/src/services/parser/cascade.py`（`profile` / `_router` / `_quality` / `providers/*`）·
> `backend/src/services/transcriber.py` · `backend/src/services/rag/_indexer.py`

## 1. 管线鸟瞰

```
POST /api/v1/meetings/upload
      │
      ▼
 Upload router
  ├─ sanitize filename（防路径穿越）
  ├─ 流式写盘，检查 MAX_UPLOAD_BYTES（来自 MAX_UPLOAD_SIZE_MB）
  ├─ 计算 content_hash（sha256）
  ├─ 去重：若同哈希已存在 → 直接复用
  ├─ 新建或关联 meeting + meeting_files 记录
  └─ BackgroundTasks.add_task(process_meeting_file, file_id)
      │
      ▼
 process_meeting_file(file_id)
  ├─ fetch_metadata                 ← trace span
  ├─ WS progress ≈ **0.2**（开始处理文件）
  │
  ├─ _resolve_processor(file_type)
  │   ├─ video/audio → AVFileProcessor → transcribe() (AssemblyAI)
  │   ├─ image       → ImageFileProcessor（`parse_structured` 级联 + 可选 vision caption）
  │   ├─ document    → DocumentFileProcessor → parse_structured()（cascade）
  │   └─ text        → TextFileProcessor
  │
  ├─ WS progress ≈ **0.6**（文本提取完成）
  ├─ index_meeting / index_meeting_file（chunking + embed）
  │   ├─ chunk：flat / parent-child / semantic
  │   ├─ embed（traffic-controlled）
  │   └─ Chroma 确定性 ID：`meeting_{mid}_file_{fid}_chunk_{i}`（见 `_indexer`）
  ├─ index_meeting_pages / index_meeting_segments（结构化索引）
  ├─ index_file_with_raganything（条件执行）
  │
  ├─ WS progress ≈ **0.8** / 单文件完成可达 **1.0**
  ├─ 写回 meeting_files 状态与 transcript / 结构化 artefacts
  ├─ per-file 摘要（MEETING_AUTO_SUMMARIZE_FILES）
  ├─ 聚合父 meeting：_update_meeting_status_from_files()
  └─ WS complete（会议维度 status：如 ready / failed）

> 单 meeting 旧路径 `process_meeting(meeting_id)` 使用 **0.0 → 0.2 → 0.6 → 0.8** 的进度比例（`processor/_pipeline.py`）。
```

## 2. 上传端点：`POST /api/v1/meetings/upload`

### 2.1 流式写盘 + 容量保护

```python
# 伪代码
async with aiofiles.open(target, "wb") as fh:
    total = 0
    async for chunk in file.stream():
        total += len(chunk)
        if total > settings.MAX_UPLOAD_BYTES:
            raise HTTPException(413, "File too large")
        await fh.write(chunk)
        sha.update(chunk)
```

**不预读整个文件**，避免大文件撑爆内存。

### 2.2 文件名清洗

- 去除 `..` / 绝对路径 / 非法字符  
- 保留原扩展名（用于 dispatch）  
- 冲突时追加 `_1`, `_2` …  
- Magic byte 验证（`_MAGIC_BYTES` in `_common.py`）

### 2.3 内容哈希幂等

- `meeting_files.content_hash` + 每会议唯一索引（见数据库迁移 v21）  
- **reprocess** 时未变化的哈希可跳过重复解析

### 2.4 Meeting vs File 关系

- 可传 `meeting_id` 追加文件；否则新建会议  
- 删除单文件：`delete_meeting_chunks(meeting_id, file_id=...)` 仅清该文件向量

## 3. 后台处理：`process_meeting_file(file_id)`

代码：`services/processor/_pipeline.py`。

### 3.1 状态机（文件 vs 会议）

**`meeting_files.status`**（管线与恢复逻辑中最常见）：

```
processing ──► ready
           └──► error
```

新建文件行通常以 **`processing`** 插入，并带 **`processing_started_at`**（用于 `recover_stale_meetings` 的 grace 判断）。

**`meetings.status`**（父级聚合，见 `MeetingLifecycleStatus`）：包含 `uploading` / `processing` / `ready` / `failed` / `error` 等；与单文件 `error` 并存时需理解聚合函数 `_update_meeting_status_from_files()` 的规则（以源码为准）。

### 3.2 阻塞调用的隔离

```python
await asyncio.wait_for(
    asyncio.to_thread(parser.parse, path, trace=trace),
    timeout=settings.PARSE_TIMEOUT_SECONDS,
)
```

超时或异常 → 更新文件为 `error` 并写 `error_message`；会议可能被聚合为 `failed`。

### 3.3 WebSocket 进度事件

处理器在 **0.2 / 0.6 / 0.8 /（单文件 1.0）** 等节点调用 `websocket_manager.notify_*`。载荷示例：

```json
{"type":"progress","meeting_id":42,"status":"processing","progress":0.6,"message":"Text extracted from deck.pdf"}
{"type":"complete","meeting_id":42,"status":"ready","title":"Q1 Review"}
```

多文件场景以 **`meeting_id` 广播**；`file_id` 一般不在 payload 中。连接：`WS /api/v1/ws?client_id=...`（生产需 `api_key` query 或 dev 模式）。

### 3.4 Trace spans

阶段 span 包括：`fetch_metadata`、`parse` 或 `transcribe`、`index_meeting`、`db_persist` 等。  
解析成功后 metadata 含 **`parser_used`**，取值为路由结果中的 provider 名：**`local` / `marker` / `mineru` / `paddle`**（与 `ParserName` 一致），**不是**旧文档中的 `paddleocr`。

摄入结束后管线会 **`logger.info("ingest_trace %s", json.dumps(trace.to_dict()))`** — 默认**不写独立 `trace` 表**；benchmark 或链路透传另论。

## 4. 解析管线：`services/parser/cascade.py`

### 4.1 总览（与旧版「固定三级」的区别）

当前实现为 **内容感知路由 + 云 API 级联（本地兜底）**：

1. **格式分派**：纯文本（.txt/.md/.json/.csv 等）走本地直读；`.ppt/.pptx/.doc/.xls` 先做格式转换（LibreOffice → PDF/DOCX/XLSX），其余 PDF/图片直接进入 profile。
2. **本地画像**：`profile_document()`（PyMuPDF / python-pptx 等）仅用于路由决策（页数、字符密度、图片占比、是否偏扫描）。不抽取内容。
3. **路由**：`select_parsers(profile, user_hint=...)`（`services/parser/_router.py`）返回有序 provider 元组（`marker / mineru / paddle / local`）。
4. **级联**：在同一 **asyncio** 事件循环内依次尝试（共享 `httpx` 客户端，避免多次 `asyncio.run` 破坏连接）。
5. **全部云 API 失败**：回退到 `providers/local.py`（PyMuPDF `get_text()`），**不会硬失败**。仅当本地提取也无法产出有效内容时才抛出 `AllParsersFailedError`。

### 4.2 Provider 一览

| 名称 | 实现模块 | 说明 |
|---|---|---|
| `local` | `providers/local.py` | 本地 PyMuPDF/python-pptx/python-docx/openpyxl 提取；亦作云 API 全部失败时的最后兜底 |
| `marker` | `providers/marker_api.py` | Marker 云 API（布局友好） |
| `mineru` | `providers/mineru_api.py` | MinerU 云 API（偏 OCR / 复杂 PDF） |
| `paddle` | `providers/paddle_api.py` | Paddle 布局解析云 API |

> 与历史「本机 magic-pdf / 本机 PaddleOCR 引擎」不同：默认路径为 **HTTP 云解析**，镜像保持精简。

### 4.3 `OCR_PROVIDER`（配置提示）

环境变量 / 配置中的 **`OCR_PROVIDER`** 传入 `_router.select_parsers` 的 `user_hint`：**软偏好** — 若 hint 出现在当次路由序列中，则**提升到队首**；**不**绕过 `DocumentProfile` 的格式规则（例如独立图片不会把 MinerU 放在首位，因 MinerU v4 提取以 PDF 为主场景）。

### 4.4 路由摘要（`_router.py`）

- **独立图片**（png/jpg/…）：默认顺序 **`paddle` → `marker`**，再按 hint 调整。  
- **PDF**：按 `avg_chars_per_page` 与 `image_ratio` 分支 — 高文本密度且低图占比可 **`local` 优先**；扫描感强则 **`mineru` 提前**；其余常见为 **`marker` → `mineru` → `paddle` → `local`** 的变体。  
- **PPTX**：偏文字时可 `local` 优先；图多则 `marker` / `paddle` 提前。  
- **DOCX/XLSX 等**：默认 `marker` → `mineru` → `paddle` → `local`。

阈值常量：`TEXT_DENSITY_HIGH`（200）、`TEXT_DENSITY_LOW`（50）、`IMAGE_RATIO_*` 见 `_router.py`。

### 4.5 支持格式与旧 Office

- **`SUPPORTED_EXTS`**：`types.py`；种类注册见 `services/files/_kinds.py`。  
- **`.ppt` / `.doc` / `.xls`**：LibreOffice headless 转为 pptx/docx/xlsx（`parser/converters.py`）；缺失 LibreOffice 时记录警告并尽力继续。

### 4.6 页数上限

`_estimate_page_count()` 在级联前估算；超过 **`MAX_PARSE_PAGES`**（默认 1000）抛出 `ValueError`，避免天价解析。

### 4.7 超时与线程

- 总预算：**`PARSE_TIMEOUT_SECONDS`**（级联内单调时钟）。  
- 从 **已有事件循环** 内调用时，cascade 通过 **线程池 + `asyncio.run`** 在子线程跑完整异步级联，避免阻塞 FastAPI（见 `cascade._dispatch_cascade`）。

## 5. 转写：`services/transcriber.py`

### 5.1 Provider

| `ASR_PROVIDER` | 说明 |
|---|---|
| `assemblyai` | **唯一**支持的云端 ASR；说话人 / 时间戳 / 多语言（`services/asr/_assemblyai.py`） |

本地 Whisper 等已移除以保持镜像精简。视频先 **ffmpeg** 抽 WAV 再上传。

### 5.2 输出与 API

`TranscriptResult`：`text`、`segments`、`language`、`duration_seconds`；时间线 API 消费 `segments`。

## 6. 索引：`services/rag/_indexer.py`

### 6.1 Chunking

Flat / Parent-Child / Semantic — 详见 [`rag.md`](./rag.md#3-索引流程-chunking)。

### 6.2 确定性 chunk ID

```text
meeting_{meeting_id}_file_{file_id}_chunk_{index}
```

便于幂等写入与按前缀删除。

### 6.3 Chroma metadata（示例）

`meeting_id`、`file_id`、`chunk_index`、`file_type`、`file_name`、`page`、时间戳字段、`date` 等，供检索 `where` 过滤。

### 6.4 多模态索引协调 `_reconcile.py`

当 `RAGANYTHING_ENABLED=True` 时，`_reconcile.py` 负责 Chroma 与 RAGAnything 双写的一致性校验和修复。启动时检查 `index_state` 表中标记为待同步的记录，确保两个向量库的状态一致。

### 6.5 会议摘要自动触发 `_pipeline_summary.py`

处理完成后自动触发会议摘要生成。当 `MEETING_AUTO_SUMMARIZE_FILES=True` 时，对每个文件生成摘要后再生成会议级别的整体摘要。

## 7. 重跑：`POST /meetings/{id}/reprocess`

按 `content_hash` 跳过未变文件；失败项可单文件 reprocess。用于解析器/embedding 配置变更后的重刷（大变更常配合 **rebuild-vectors**）。

## 8. 边界与容错

| 情况 | 行为 |
|---|---|
| 超过 `MAX_UPLOAD_BYTES` | 413，已写入部分应被清理（见上传路由实现） |
| 路径穿越 / 非法名 | 400 + sanitize |
| 云解析商之一失败 | 级联下一 provider；全失败则本地兜底 |
| 兜底仍失败 | `error` + `error_message`；会议可能被标为 `failed` |
| 进程中途崩溃 | 重启后 `recover_stale_meetings`（5 分钟 grace） |
| Embedding 限流 | `traffic_control` 排队 / 熔断 |

## 9. 性能与成本建议

- **视频**：先转 WAV 再送 AssemblyAI，省带宽。  
- **大 PDF**：调低 `MAX_PARSE_PAGES` 或拆分上传。  
- **云解析**：配置各 provider 的 API Key 与配额；监控 `parser_used` 与日志中的 fallback 链。  
- **并发**：embedding 受 `LLM_MAX_CONCURRENCY` 等约束；上传为 BackgroundTasks 逐请求调度。
