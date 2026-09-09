from scripts.production_holdout_benchmark import _retrieval_metrics


def test_retrieval_metrics_distinguish_hit_from_multi_target_recall() -> None:
    results = [
        {"metadata": {"file_id": 10}},
        {"metadata": {"file_id": 99}},
        {"metadata": {"file_id": 10}},
    ]

    metrics = _retrieval_metrics(results, {10, 20}, field="file_id", cutoff=10)

    assert metrics == {"rank": 1, "hit": 1.0, "recall": 0.5, "mrr": 1.0}


def test_reranker_execution_uses_actual_public_trace_and_rejects_fallback():
    from scripts.production_holdout_benchmark import _reranker_executed
    from src.core.trace import TraceSpan

    span = TraceSpan("rerank", "rerank", metadata={"executed": True})
    span.finish()
    assert _reranker_executed(span.to_dict())
    assert _reranker_executed({"status": "success", "metadata": {"executed": True}})
    for executed in (False, "False", None, "", "unknown"):
        assert not _reranker_executed({"status": "success", "metadata": {"executed": executed}})
    for status in ("degraded", "error", "timeout", "running"):
        span.status = status
        assert not _reranker_executed(span.to_dict())
    span.status = "success"
    span.skipped = True
    assert not _reranker_executed(span.to_dict())


def test_missing_chunk_identity_is_not_a_zero_recall_measurement():
    metrics = _retrieval_metrics(
        [{"file_id": 1, "content": "Evidence"}], {"chunk-1"}, field="chunk_id", cutoff=10
    )
    assert metrics == {"rank": None, "hit": None, "recall": None, "mrr": None}


def test_chunk_metrics_exclude_derived_summaries_but_do_not_hide_unidentified_chunks():
    from scripts.production_holdout_benchmark import _retrieval_metrics

    sources = [
        {"source_kind": "file_summary", "chunk_id": None},
        {"source_kind": "text", "chunk_id": "actual"},
    ]
    assert _retrieval_metrics(sources, {"actual"}, field="chunk_id")["recall"] == 1.0
    sources.append({"source_kind": "text", "chunk_id": None})
    assert _retrieval_metrics(sources, {"actual"}, field="chunk_id")["recall"] is None
