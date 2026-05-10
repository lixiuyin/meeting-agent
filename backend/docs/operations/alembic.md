# Alembic 数据库迁移工作流

> 本仓库在保留 **`_migrations.py` 遗留元组迁移** 的同时，引入 **Alembic** 作为可审查、可重复的 schema 演进方式。  
> 启动路径：`backend/src/api/lifespan.py` → `_run_alembic_upgrade()` → `alembic upgrade head`。  
> 基线 revision：`20260414_000001`（文件 `backend/alembic/versions/20260414_000001_baseline_schema.py`）。

## 1. 当前机制说明

- **线上 / 本地 uvicorn**：优先执行 Alembic；基线的 `upgrade()` 会遍历 `_MIGRATIONS` 并写入 `schema_version`，与历史上仅调用 `init_db()` 的效果对齐。  
- **`init_db()`**：仍保留；在「无 Alembic」或「缺少 `alembic.ini`」时由 `_run_alembic_upgrade` 回退调用，也可在脚本、测试中单独使用。  
- **后续变更**：新表/新列应在 **Alembic revision** 中声明（并遵守团队 PR 审查）；若短期仍只改 `_MIGRATIONS`，须与维护者约定避免与 Alembic 分叉。

## 2. 已有数据库的一次性对齐（stamp）

若数据库在引入 Alembic **之前** 已由 `init_db()` 建全表，且 `schema_version` 已反映最新版本，可对齐 Alembic 头指针而**不重复执行**基线 SQL：

```bash
cd backend
uv run alembic stamp 20260414_000001
```

不确定时先**备份** `data/meetings.db`，再在测试环境验证。

## 3. 常用命令

```bash
cd backend

# 查看当前 heads
uv run alembic heads

# 升级到最新
uv run alembic upgrade head

# 新建空 revision（手写 upgrade/downgrade）
uv run alembic revision -m "describe schema change"

# 自当前库自动生成差异 revision（需 sqlalchemy 元数据与模型对齐时使用）
# uv run alembic revision --autogenerate -m "sync models"
```

## 4. 团队约定（建议）

1. 涉及 **schema 变更** 的 PR 应附带 **Alembic revision**（或经团队同意的 `_MIGRATIONS` 递增版本，二者勿混用未沟通）。  
2. `upgrade()` / `downgrade()` 在可行时应成对实现；**生产环境**以前进升级为主，`downgrade()` 多用于开发回滚。  
3. PR 描述中注明：迁移是否可逆、是否需数据回填、是否与 Chroma/索引重建有关。  
4. 合并前在干净库与「接近生产体量的副本」上各执行一次 `alembic upgrade head`。

## 5. 与日志的交互

`alembic.ini` 会通过 `fileConfig` 调整 logging。项目用例 `tests/test_logging.py` 约束：**加载 Alembic 配置后应用文件 handler 仍须可用**。若修改 `alembic/env.py`，请运行该测试。

## 6. 相关文档

- 表结构与 27 条遗留迁移摘要：[`../database.md`](../database.md)  
- 启动顺序与运维：[`../lifespan-and-operations.md`](../lifespan-and-operations.md)
