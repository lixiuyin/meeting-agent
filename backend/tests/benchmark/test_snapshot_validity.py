"""Missing references and missing source identities must not pass snapshots."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from scripts import benchmark


def test_snapshot_semantic_compare_ignores_formatting_and_citations():
    expected = "Customer retention reached an all-time high of 94%."
    current = "- Customer retention reached an all-time high of 94% [1]."
    result = benchmark._snapshot_semantic_compare(expected, current)
    assert result["pass"] is True
    assert result["missing_numeric_claims"] == []


def test_snapshot_semantic_compare_rejects_changed_numeric_claim():
    expected = "Revenue was up 12% and retention was at 94%."
    current = "Revenue was up 9% and retention was at 94%."
    result = benchmark._snapshot_semantic_compare(expected, current)
    assert result["pass"] is False
    assert "12%" in result["missing_numeric_claims"]


def test_snapshot_semantic_compare_accepts_cjk_translation_of_claims():
    expected = (
        "Three blockers were raised: the mobile app waits on final UI designs; "
        "the database migration needs cloud budget approval; the analytics pipeline "
        "depends on a third-party API for production access."
    )
    current = (
        "提出了三个阻碍因素: 移动端应用等待最终设计; 数据库迁移需要云存储预算审批; "
        "分析管道依赖第三方 API 的生产访问权限。"
    )
    result = benchmark._snapshot_semantic_compare(expected, current)
    assert result["pass"] is True


def test_missing_then_approved_snapshot_and_source_loss(monkeypatch, tmp_path):
    (tmp_path / "golden_set.json").write_text(
        json.dumps({"items": [{"id": "q1", "query": "Who?", "fixture_file": "sample.pdf"}]})
    )
    monkeypatch.setattr(benchmark, "FIXTURE_DIR", tmp_path)
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    monkeypatch.setattr(benchmark, "RAG_SNAPSHOTS_DIR", snapshots)
    monkeypatch.setattr(
        "scripts._bench_fixtures.ingest_fixtures", AsyncMock(return_value={"sample.pdf": (1, 42)})
    )
    answer = SimpleNamespace(
        answer="Alice",
        sources=[{"file_id": 42, "chunk_id": "meeting_1_file_42_chunk_0"}],
    )
    monkeypatch.setattr("src.services.chain.ask", AsyncMock(return_value=answer))
    args = SimpleNamespace(update_snapshots=False)
    missing = benchmark.run_rag_snapshot_benchmark(args)
    assert not missing["valid"]
    assert missing["snapshot_candidates"][0]["source_ids"] == ["sample.pdf:chunk_0"]
    (snapshots / "q1.json").write_text(json.dumps(missing["snapshot_candidates"][0]))
    assert benchmark.run_rag_snapshot_benchmark(args)["valid"]
    answer.sources = []
    assert not benchmark.run_rag_snapshot_benchmark(args)["valid"]
