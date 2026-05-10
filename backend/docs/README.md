# Meeting Agent 后端文档

> `backend/docs/` 目录是 Meeting Agent 后端的**子系统级文档集**。每份文件聚焦一个子系统，所有文档都会在 [`architecture.md`](./architecture.md) 里被交叉引用。
>
> **英文示意图**（Mermaid 流程图等）位于仓库根目录 [`docs/diagrams/`](../../docs/diagrams/)，与本文档互为补充；总索引见 [`docs/README.md`](../../docs/README.md)。

## 阅读顺序建议

1. **新加入项目？** 先读 [`architecture.md`](./architecture.md) 了解分层与数据流总览
2. **准备部署？** 读 [`configuration.md`](./configuration.md) + [`lifespan-and-operations.md`](./lifespan-and-operations.md) + [`database.md`](./database.md)（含 Alembic）+ [`operations/alembic.md`](./operations/alembic.md)
3. **对接 API？** 读 [`api-reference.md`](./api-reference.md) 和 [`mcp-server.md`](./mcp-server.md)
4. **用终端操作系统？** 读 [`cli.md`](./cli.md)
5. **深入业务逻辑？** 读 [`chain-pipeline.md`](./chain-pipeline.md) → [`rag.md`](./rag.md) → [`memory-and-kg.md`](./memory-and-kg.md)；Skill 扩展见 [`SKILLS.md`](./SKILLS.md)
6. **研究存储 / 性能？** 读 [`database.md`](./database.md) + [`llm-and-traffic.md`](./llm-and-traffic.md) + [`benchmarking.md`](./benchmarking.md)
7. **排查上传问题？** 读 [`ingest-pipeline.md`](./ingest-pipeline.md)

## 文档索引

### 总览

| 文档 | 主题 |
|---|---|
| [`architecture.md`](./architecture.md) | 系统架构总览、分层、数据流、跨切面关注点 |
| [`lifespan-and-operations.md`](./lifespan-and-operations.md) | FastAPI lifespan、关键/尽力路径、运维命令、故障恢复 |
| [`configuration.md`](./configuration.md) | 三级配置覆盖、全部配置项参考、典型场景模板 |

### 业务管线

| 文档 | 主题 |
|---|---|
| [`ingest-pipeline.md`](./ingest-pipeline.md) | 上传 → 解析/转写 → 索引 全链路 |
| [`rag.md`](./rag.md) | RAG 架构、chunking、检索、rerank、后处理、优化方向 |
| [`chain-pipeline.md`](./chain-pipeline.md) | `ask()` / `ask_stream()` 编排、并行上下文、流式事件 |
| [`memory-and-kg.md`](./memory-and-kg.md) | 记忆系统、衰减、合并、画像、知识图谱 |
| [`SKILLS.md`](./SKILLS.md) | Skill 加载、意图匹配、与 chain 集成 |

### 基础设施

| 文档 | 主题 |
|---|---|
| [`database.md`](./database.md) | SQLite 读写分离、**Alembic + 遗留迁移**、44 步 `_MIGRATIONS` 摘要、主要表、Repository |
| [`llm-and-traffic.md`](./llm-and-traffic.md) | Provider 注册表、缓存、并发/限速/熔断 |

### 运维与实践

| 文档 | 主题 |
|---|---|
| [`operations/alembic.md`](./operations/alembic.md) | Alembic 与 `init_db` 关系、stamp、`upgrade head`、团队约定 |
| [`operations/backup.md`](./operations/backup.md) | 备份策略与脚本入口 |
| [`operations/restore.md`](./operations/restore.md) | 恢复流程 |
| [`operations/retention.md`](./operations/retention.md) | 数据保留与清理 |
| [`operations/sla.md`](./operations/sla.md) / [`operations/slo.md`](./operations/slo.md) | SLA / SLO 说明 |
| [`operations/runbooks/`](./operations/runbooks/) | AssemblyAI 超时、429 风暴、Chroma 维度不一致、熔断等 |

### 接入

| 文档 | 主题 |
|---|---|
| [`api-reference.md`](./api-reference.md) | REST API 路由、请求/响应 schema、错误语义 |
| [`mcp-server.md`](./mcp-server.md) | MCP Server 工具、传输、调试、扩展 |
| [`cli.md`](./cli.md) | CLI 命令、交互引导、导出与排障 |

### 性能

| 文档 | 主题 |
|---|---|
| [`benchmarking.md`](./benchmarking.md) | 基准测试工具、命令、典型指标 |

## 维护约定

- 所有文档用**中文**撰写（保持风格一致；`operations/runbooks` 等历史英文材料若保留，新写 runbook 请优先中文）
- 源码位置以**绝对路径**标注：`backend/src/...`
- 切勿在文档中硬编码 secret 或生产 URL
- 文档和代码**同步更新**：修改子系统的重大行为时，同时 PR 更新对应文档
- 新增子系统时：
  1. 在 `backend/docs/` 下加新 `md`
  2. 在本 `README.md` 索引表中追加条目
  3. 在 [`architecture.md`](./architecture.md) 的"子系统索引"表中追加条目
