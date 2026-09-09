# 会议场景质量审核流程

本流程用于建立“可用于发布判断”的真实材料留出集。仓库中的合成数据集和
历史结果只能用于回归与诊断；`valid=true` 仅表示评测按协议完成，不代表模型
达到生产质量。

## 当前准入标准

- 每个留出集只对应一个认证主体；多用户数据库必须显式指定 `--user-id`。
- 默认会议域至少 30 个最终接受的案例，覆盖至少 10 场会议，其中至少 6 个
  案例必须同时依赖两场会议的证据。若同时评估 `meeting` 与
  `course_research`，每个域至少 30 个案例，总数至少 60。
- 每题必须包含问题、参考答案、来源文件、会议身份、支持 chunk 和可回溯的
  原文引用。预期文件 ID 只供评估器评分，不能用于缩小生成时的检索范围。
- 审核人逐条判断问题是否自然、答案是否完全受原文支持、证据是否充分；修改
  题目或答案后必须重新生成指纹和审核表。
- 正式运行至少进行 3 次独立 judge 评分，系统模型与 judge 模型必须不同；
  reranker 必须对所有案例真实执行。缺失或跳过的指标保持 `null` 并报告
  evaluated/skipped，不得补成零分或推测分。

## 执行顺序

在 `backend/` 中执行：

```bash
uv run python -m scripts.production_holdout_benchmark curate \
  --source-db ../data/meetings.db \
  --user-id <principal-user-id> \
  --required-domain meeting \
  --cases 30 \
  --minimum-domain-cases 30 \
  --minimum-meetings 10 \
  --minimum-cross-meeting-cases 6 \
  --output .private-benchmarks/production-holdout.json

uv run python -m scripts.business_review prepare \
  --holdout .private-benchmarks/production-holdout.json \
  --decisions .private-benchmarks/production-holdout-review.csv

# 审核人完成 CSV 后：
uv run python -m scripts.business_review validate \
  --holdout .private-benchmarks/production-holdout.json \
  --decisions .private-benchmarks/production-holdout-review.csv

uv run python -m scripts.business_review approve \
  --holdout .private-benchmarks/production-holdout.json \
  --decisions .private-benchmarks/production-holdout-review.csv \
  --output .private-benchmarks/production-holdout-reviewed.json

uv run python -m scripts.production_holdout_benchmark run \
  --source-db ../data/meetings.db \
  --source-vector-dir ../data/vectordb \
  --holdout .private-benchmarks/production-holdout-reviewed.json \
  --output .private-benchmarks/production-rag-result.json \
  --judge-model independent/judge-model \
  --judge-repeats 3
```

`prepare` 使用独占创建，避免覆盖进行中的人工审核；每行绑定精确 case hash。
`validate` 会检查 ID、指纹、时区时间、审核人、域标签、三项证据确认、域配额、
会议数和跨会议案例数。`approve` 只有在审核完整且覆盖达标时才生成新的 reviewed
数据集，审核人姓名是责任声明而不是身份认证。

## 结果与发布边界

保留原始失败报告，不覆盖旧结果来获得高分。生产报告还必须通过质量阈值、
judge 解析完整性和全部 reranker 执行检查，之后才能用
`build_production_quality_evidence.py` 生成不含私有问答的紧凑发布证明。
`check_release_readiness.py` 会把它与人工审核、SLO、安全证据以及当前实现指纹
绑定，并在内部检查仓库状态。

`backend/evaluation/datasets/meeting_workflow_review.json` 仍是
`human_reviewed=false` 的合成草稿。旧的私有 CSV 或 2026-09-07 结果是历史材料，
不能代表当前代码。Memory 抽取可用
`uv run python -m scripts.benchmark memory-pipeline` 重新诊断，但自动测试和合成
高分均不能替代上述真实材料审核。

真实用户可用性测试继续使用
`docs/validation/usability-session-template.json`。参与者应在无实现提示的条件下
完成任务并记录耗时、求助、来源核验和实际观察；空字段保持 `null`，浏览器自动化
结果不能冒充真人结果。
