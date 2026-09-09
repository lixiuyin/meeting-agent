"""Unit tests for benchmark trace aggregation logic."""

from scripts._bench_aggregate import (
    aggregate,
    format_chat_performance_markdown,
    format_evidence_quality_markdown,
    format_rag_quality_markdown,
)


def test_aggregate_percentiles():
    traces = [
        {
            "spans": [
                {"label": "fetch", "phase": "metadata", "duration_ms": 10.0, "status": "success"},
                {"label": "parse", "phase": "extract", "duration_ms": 100.0, "status": "success"},
            ]
        },
        {
            "spans": [
                {"label": "fetch", "phase": "metadata", "duration_ms": 20.0, "status": "success"},
                {"label": "parse", "phase": "extract", "duration_ms": 200.0, "status": "success"},
            ]
        },
        {
            "spans": [
                {"label": "fetch", "phase": "metadata", "duration_ms": 30.0, "status": "success"},
                {"label": "parse", "phase": "extract", "duration_ms": 300.0, "status": "success"},
            ]
        },
    ]

    stats = aggregate(traces)

    fetch = stats["fetch"]
    assert fetch.n == 3
    assert fetch.p50 == 20.0
    assert fetch.mean == 20.0
    assert fetch.status_counts == {"success": 3}

    parse = stats["parse"]
    assert parse.n == 3
    assert parse.p50 == 200.0
    assert parse.mean == 200.0


def test_aggregate_includes_end_to_end_trace_total() -> None:
    stats = aggregate(
        [
            {"total_ms": 120.0, "spans": []},
            {"total_ms": 180.0, "spans": []},
        ]
    )

    assert stats["trace_total"].phase == "total"
    assert stats["trace_total"].n == 2
    assert stats["trace_total"].p50 == 150.0


def test_aggregate_with_missing_durations():
    traces = [
        {
            "spans": [
                {"label": "skip", "phase": "test", "duration_ms": None, "status": "success"},
            ]
        },
    ]
    stats = aggregate(traces)
    assert stats["skip"].n == 0
    assert stats["skip"].p50 == 0.0


def test_format_evidence_quality_markdown_shows_diagnostic_limitations() -> None:
    md = format_evidence_quality_markdown(
        {
            "grade": "diagnostic",
            "release_ready": False,
            "dataset_kind": "synthetic",
            "observed_cases": 10,
            "judge_repeats": 3,
            "reranker_evaluated_queries": 0,
            "limitations": [
                "dataset_is_not_a_production_holdout",
                "reranker_not_executed_for_every_query",
            ],
        }
    )

    assert "## Evidence Quality" in md
    assert "| overall | diagnostic | no | synthetic | 10 | 3 | 0 |" in md
    assert "`dataset_is_not_a_production_holdout`" in md
    assert "`reranker_not_executed_for_every_query`" in md


def test_format_evidence_quality_markdown_supports_rag_all_scopes() -> None:
    md = format_evidence_quality_markdown(
        {
            "retrieval": {
                "grade": "diagnostic",
                "release_ready": False,
                "dataset_kind": "synthetic",
                "observed_cases": 10,
                "judge_repeats": None,
                "reranker_evaluated_queries": 10,
                "limitations": ["dataset_is_not_a_production_holdout"],
            },
            "answer": {
                "grade": "release_candidate",
                "release_ready": True,
                "dataset_kind": "production_holdout",
                "observed_cases": 30,
                "judge_repeats": 3,
                "reranker_evaluated_queries": None,
                "limitations": [],
            },
        }
    )

    assert "| retrieval | diagnostic | no | synthetic | 10 | — | 10 |" in md
    assert "| answer | release_candidate | yes | production_holdout | 30 | 3 | — |" in md


def test_format_chat_performance_markdown_shows_strata_and_gate() -> None:
    md = format_chat_performance_markdown(
        {
            "category_stats": {
                "factual": {
                    "samples": 4,
                    "ttft_p50_ms": 100.0,
                    "ttft_p95_ms": 200.0,
                    "total_p50_ms": 300.0,
                    "total_p95_ms": 400.0,
                    "degraded_count": 1,
                    "degraded_rate": 0.25,
                }
            },
            "performance_gate": {"passed": False, "enforced": True},
        }
    )
    assert "| factual | 4 | 100.00 ms | 200.00 ms" in md
    assert "1/4 (25.0%)" in md
    assert "**FAIL** (enforced)" in md


def test_format_rag_quality_markdown_retrieval_only():
    rag_quality = {
        "retrieval": {
            "stats": {
                "semantic-only@5": {"recall": 0.612},
                "semantic-only@10": {"recall": 0.748, "mrr": 0.521, "ndcg": 0.604},
            }
        }
    }
    md = format_rag_quality_markdown(rag_quality)
    assert "## RAG Quality — Retrieval" in md
    assert "| semantic-only@5 | 0.612 | — | — |" in md
    assert "semantic-only@10" in md
    assert "## RAG Quality — Answer" not in md


def test_format_rag_quality_markdown_answer_only():
    rag_quality = {
        "answer": {
            "stats": {
                "faithfulness": 0.871,
                "answer_relevance": 0.903,
                "context_precision": 0.764,
                "context_recall": None,
                "correctness": 0.812,
                "citation_quality": 0.721,
                "rouge_l_f1": 0.451,
                "answer_similarity": 0.824,
                "parse_failures": 0,
            },
            "rows": [
                {
                    "query_id": "q1",
                    "faithfulness": 0.8,
                    "answer_relevance": 0.9,
                    "context_precision": 0.7,
                    "context_recall": None,
                    "correctness": 0.8,
                    "citation_quality": 0.7,
                    "rouge_l_f1": 0.4,
                    "answer_similarity": 0.8,
                }
            ],
        }
    }
    md = format_rag_quality_markdown(rag_quality)
    assert "## RAG Quality — Answer" in md
    assert "| 0.871 | 0.903 | 0.764 | — | 0.812 | 0.721 | 0.451 | 0.824 | 0 |" in md
    assert "Per-query breakdown" in md
    assert "q1" in md


def test_format_rag_quality_markdown_full_rag_all():
    rag_quality = {
        "retrieval": {
            "stats": {
                "hybrid@10": {"recall": 0.821, "mrr": 0.598, "ndcg": 0.673},
            }
        },
        "answer": {
            "stats": {
                "faithfulness": 0.9,
                "answer_relevance": 0.95,
                "context_precision": 0.85,
                "context_recall": 0.88,
                "correctness": 0.91,
                "citation_quality": 0.89,
                "rouge_l_f1": 0.5,
                "answer_similarity": 0.92,
                "parse_failures": 1,
            },
            "rows": [],
        },
    }
    md = format_rag_quality_markdown(rag_quality)
    assert "## RAG Quality — Retrieval" in md
    assert "## RAG Quality — Answer" in md
    assert "| 0.900 | 0.950 | 0.850 | 0.880 | 0.910 | 0.890 | 0.500 | 0.920 | 1 |" in md
    assert "Per-query breakdown" not in md
