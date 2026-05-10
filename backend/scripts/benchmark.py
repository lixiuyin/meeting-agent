"""End-to-end performance benchmark harness for meeting-agent.

Usage:
    uv run python -m scripts.benchmark chat --iterations 10
    uv run python -m scripts.benchmark ingest --iterations 5
    uv run python -m scripts.benchmark micro
    uv run python -m scripts.benchmark all
    uv run python -m scripts.benchmark chat --baseline
    uv run python -m scripts.benchmark chat --update-baseline
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPTS_DIR = Path(__file__).parent.resolve()
BACKEND_DIR = SCRIPTS_DIR.parent.resolve()
RESULTS_DIR = BACKEND_DIR / "benchmark-results"
BASELINE_PATH = RESULTS_DIR / "baseline.json"
RAG_SNAPSHOTS_DIR = RESULTS_DIR / "rag_snapshots"
FIXTURE_DIR = BACKEND_DIR / "tests" / "fixtures" / "benchmark"
QUERIES_PATH = FIXTURE_DIR / "queries.json"

RESULTS_DIR.mkdir(parents=True, exist_ok=True)
RAG_SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Baseline helpers
# ---------------------------------------------------------------------------


def _load_baseline() -> dict:
    if BASELINE_PATH.exists():
        with open(BASELINE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _compare_baseline(current: dict, baseline: dict, threshold: float) -> list[str]:
    regressions: list[str] = []
    baseline_stats = baseline.get("stats", {})
    current_stats = current.get("stats", {})
    for label, stats in current_stats.items():
        base = baseline_stats.get(label)
        if not base:
            continue
        base_p95 = base.get("p95", 0)
        curr_p95 = stats.get("p95", 0)
        if base_p95 > 0 and curr_p95 > base_p95 * (1 + threshold):
            regressions.append(
                f"{label}: p95 {curr_p95:.1f}ms > baseline {base_p95:.1f}ms "
                f"(+{(curr_p95 / base_p95 - 1) * 100:.1f}%)"
            )

    base_judge = baseline.get("judge_config")
    cur_judge = current.get("judge_config")
    if base_judge and cur_judge and base_judge != cur_judge:
        print(
            f"JUDGE CONFIG DRIFT: baseline={base_judge} current={cur_judge} "
            "— scores are not directly comparable"
        )

    return regressions


# ---------------------------------------------------------------------------
# Report writing
# ---------------------------------------------------------------------------


def _write_report(name: str, payload: dict) -> tuple[Path, Path]:
    ts = datetime.now(UTC).isoformat().replace(":", "-").replace("+", "_")
    payload["timestamp"] = ts
    payload["name"] = name

    from ._bench_aggregate import SpanStats, format_markdown

    class _Encoder(json.JSONEncoder):
        def default(self, o):
            if isinstance(o, SpanStats):
                return o.to_dict()
            return super().default(o)

    md_path = RESULTS_DIR / f"{name}_{ts}.md"
    with open(md_path, "w", encoding="utf-8") as f:
        if payload.get("phase") in (1, 2):
            from ._bench_aggregate import format_chunk_benchmark_markdown
            f.write(format_chunk_benchmark_markdown(payload))
        elif "phase1" in payload and "phase2" in payload:
            # Summary for rag-chunk-full
            p1 = payload["phase1"]
            p2 = payload["phase2"]
            from ._bench_aggregate import format_chunk_benchmark_markdown
            f.write("# RAG Chunk Benchmark — Full Run\n\n")
            f.write(format_chunk_benchmark_markdown(p1))
            f.write("\n")
            f.write(format_chunk_benchmark_markdown(p2))
        else:
            f.write(format_markdown(payload.get("stats", {})))
        if payload.get("rag_quality"):
            from ._bench_aggregate import format_rag_quality_markdown
            f.write("\n")
            f.write(format_rag_quality_markdown(payload["rag_quality"]))
        if payload.get("snapshot_diffs"):
            f.write("\n## Snapshot Diffs\n")
            f.write(json.dumps(payload["snapshot_diffs"], indent=2))
            f.write("\n")

    json_path = RESULTS_DIR / f"{name}_{ts}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, cls=_Encoder)

    return json_path, md_path


# ---------------------------------------------------------------------------
# Chat benchmark
# ---------------------------------------------------------------------------


def run_chat_benchmark(args: argparse.Namespace) -> dict:
    """Run chat pipeline benchmark and return result payload."""

    from ._bench_aggregate import aggregate
    from ._bench_env import bench_environment
    from ._bench_fixtures import ingest_all_fixtures

    with bench_environment():
        # Safe to import src.* now
        from src.core.database import close_all_connections, init_db
        from src.services.chain import ask

        close_all_connections()
        with open(QUERIES_PATH, encoding="utf-8") as f:
            queries_data = json.load(f)
        queries = queries_data.get("queries", [])

        traces: list[dict] = []

        async def _run() -> None:
            init_db()
            fixture_info = await ingest_all_fixtures()
            meeting_ids = list({mid for mid, _ in fixture_info.values()})
            for query_item in queries:
                q = query_item["query"]
                file_types = query_item.get("file_types")
                date_from = query_item.get("date_from")
                date_to = query_item.get("date_to")
                for _ in range(args.iterations):
                    result = await ask(
                        question=q,
                        user_id="benchmark",
                        meeting_ids=meeting_ids,
                        file_types=file_types,
                        date_from=date_from,
                        date_to=date_to,
                    )
                    if result.trace:
                        traces.append(result.trace)

        asyncio.run(_run())

    stats = aggregate(traces)
    payload = {
        "command": "chat",
        "iterations": args.iterations,
        "queries_count": len(queries),
        "trace_count": len(traces),
        "stats": stats,
    }
    return payload


# ---------------------------------------------------------------------------
# Ingest benchmark
# ---------------------------------------------------------------------------


def run_ingest_benchmark(args: argparse.Namespace) -> dict:
    """Run ingest pipeline benchmark and return result payload."""

    from ._bench_aggregate import aggregate
    from ._bench_env import bench_environment
    from ._bench_fixtures import _ingest_fixture_file

    fixtures = ["sample.pdf", "scanned.pdf", "sample.pptx"]
    traces: list[dict] = []

    with bench_environment():
        from src.core.database import close_all_connections, init_db

        close_all_connections()
        init_db()

        async def _run() -> None:
            for fixture in fixtures:
                for _ in range(args.iterations):
                    _, trace = await _ingest_fixture_file(fixture)
                    traces.append(trace.to_dict())

        asyncio.run(_run())

    stats = aggregate(traces)
    payload = {
        "command": "ingest",
        "iterations": args.iterations,
        "fixtures": fixtures,
        "trace_count": len(traces),
        "stats": stats,
    }
    return payload


# ---------------------------------------------------------------------------
# Micro benchmark
# ---------------------------------------------------------------------------


def run_micro_benchmark(_args: argparse.Namespace) -> dict:
    """Run pytest micro-benchmarks."""
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-m",
        "benchmark",
        "--benchmark-json",
        str(RESULTS_DIR / "micro_latest.json"),
    ]
    subprocess.run(cmd, cwd=BACKEND_DIR, check=False)
    return {
        "command": "micro",
        "note": f"See {RESULTS_DIR / 'micro_latest.json'}",
    }


# ---------------------------------------------------------------------------
# RAG quality benchmarks
# ---------------------------------------------------------------------------


def _rouge_l_f1(reference: str, hypothesis: str) -> float:
    """Cheap token-level ROUGE-L F1 using longest-common-subsequence."""
    ref_tokens = reference.lower().split()
    hyp_tokens = hypothesis.lower().split()
    if not ref_tokens or not hyp_tokens:
        return 0.0

    # LCS dynamic programming
    m, n = len(ref_tokens), len(hyp_tokens)
    prev = [0] * (n + 1)
    curr = [0] * (n + 1)
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if ref_tokens[i - 1] == hyp_tokens[j - 1]:
                curr[j] = prev[j - 1] + 1
            else:
                curr[j] = max(prev[j], curr[j - 1])
        prev, curr = curr, prev
    lcs_len = prev[n]
    precision = lcs_len / len(hyp_tokens)
    recall = lcs_len / len(ref_tokens)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _embedding_cosine(reference: str, hypothesis: str) -> float | None:
    """Compute cosine similarity between embedded reference and hypothesis.

    Returns None on any failure so the benchmark stays resilient.
    """
    try:
        from src.services.embedder import get_embeddings

        embeddings = get_embeddings().embed_documents([reference, hypothesis])
        a, b = embeddings[0], embeddings[1]
        dot = sum(x * y for x, y in zip(a, b, strict=False))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return None
        return dot / (norm_a * norm_b)
    except Exception as exc:
        import logging

        logging.getLogger(__name__).debug("Embedding cosine failed: %s", exc, exc_info=True)
        return None


def run_rag_retrieval_benchmark(args: argparse.Namespace) -> dict:

    from ._bench_env import bench_environment
    from ._bench_fixtures import ingest_all_fixtures
    from ._bench_rag_quality import (
        file_precision_at_k,
        file_recall_at_k,
        mrr,
        ndcg_at_k,
        recall_at_k,
    )

    with bench_environment():
        from src.core.config import settings
        from src.core.database import close_all_connections, init_db
        from src.services.rag import retrieve
        from src.services.rag._reranker import rerank
        from src.services.rag._retriever import _build_filters, _vector_retrieve

        async def _run() -> dict:
            close_all_connections()
            init_db()
            fixture_info = await ingest_all_fixtures()
            fixture_to_meeting = {
                name: mid for name, (mid, _) in fixture_info.items()
            }
            fixture_to_file = {
                name: fid for name, (_, fid) in fixture_info.items()
            }

            with open(FIXTURE_DIR / "golden_set.json", encoding="utf-8") as f:
                golden = json.load(f)

            rows = []
            for item in golden.get("items", []):
                fixture = item["fixture_file"]
                meeting_id = fixture_to_meeting.get(fixture)
                if meeting_id is None:
                    continue

                expected = set(item.get("expected_chunks", []))

                filters = _build_filters([meeting_id])
                query = item["query"]
                top_k = args.top_k

                # Semantic-only
                sem_results = _vector_retrieve(query, filters, top_k, threshold=None)
                sem_ids = [
                    r["metadata"].get("chunk_id")
                    or f"meeting_{meeting_id}_chunk_{r['metadata'].get('chunk_index', i)}"
                    for i, r in enumerate(sem_results)
                ]

                # Hybrid
                hybrid_fetch = settings.RAG_RERANK_FETCH_MULTIPLIER if settings.RERANKER_BINDING else 1
                hybrid_results, _qa = retrieve(
                    query,
                    meeting_ids=[meeting_id],
                    top_k=top_k,
                    fetch_multiplier=hybrid_fetch,
                )
                hybrid_ids = [
                    r["metadata"].get("chunk_id")
                    or f"meeting_{meeting_id}_chunk_{r['metadata'].get('chunk_index', i)}"
                    for i, r in enumerate(hybrid_results)
                ]

                # Hybrid + rerank
                if settings.RERANKER_BINDING:
                    reranked = rerank(query, hybrid_results, top_n=top_k)
                    rerank_ids = [
                        r["metadata"].get("chunk_id")
                        or f"meeting_{meeting_id}_chunk_{r['metadata'].get('chunk_index', i)}"
                        for i, r in enumerate(reranked)
                    ]
                else:
                    rerank_ids = hybrid_ids

                row: dict = {
                    "query_id": item["id"],
                    "semantic_recall_5": recall_at_k(sem_ids, expected, 5),
                    "semantic_recall_10": recall_at_k(sem_ids, expected, 10),
                    "semantic_mrr": mrr(sem_ids, expected),
                    "semantic_ndcg_10": ndcg_at_k(sem_ids, expected, 10),
                    "hybrid_recall_10": recall_at_k(hybrid_ids, expected, 10),
                    "hybrid_mrr": mrr(hybrid_ids, expected),
                    "hybrid_ndcg_10": ndcg_at_k(hybrid_ids, expected, 10),
                    "hybrid_rerank_recall_10": recall_at_k(rerank_ids, expected, 10),
                    "hybrid_rerank_mrr": mrr(rerank_ids, expected),
                    "hybrid_rerank_ndcg_10": ndcg_at_k(rerank_ids, expected, 10),
                }

                # File-level metrics when expected_files is specified
                expected_file_names = item.get("expected_files")
                if expected_file_names:
                    expected_file_ids = {
                        fixture_to_file[name]
                        for name in expected_file_names
                        if name in fixture_to_file
                    }
                    if expected_file_ids:
                        hybrid_file_ids = list(dict.fromkeys(
                            r.get("metadata", {}).get("file_id")
                            for r in hybrid_results
                            if r.get("metadata", {}).get("file_id") is not None
                        ))
                        row["file_precision_8"] = file_precision_at_k(
                            hybrid_file_ids, expected_file_ids, 8,
                        )
                        row["file_recall_8"] = file_recall_at_k(
                            hybrid_file_ids, expected_file_ids, 8,
                        )

                rows.append(row)

            def _mean(key: str) -> float:
                vals = [r[key] for r in rows if key in r]
                return sum(vals) / len(vals) if vals else 0.0

            stats = {
                "semantic-only@5": {
                    "recall": _mean("semantic_recall_5"),
                },
                "semantic-only@10": {
                    "recall": _mean("semantic_recall_10"),
                    "mrr": _mean("semantic_mrr"),
                    "ndcg": _mean("semantic_ndcg_10"),
                },
                "hybrid@10": {
                    "recall": _mean("hybrid_recall_10"),
                    "mrr": _mean("hybrid_mrr"),
                    "ndcg": _mean("hybrid_ndcg_10"),
                },
                "hybrid+rerank@10": {
                    "recall": _mean("hybrid_rerank_recall_10"),
                    "mrr": _mean("hybrid_rerank_mrr"),
                    "ndcg": _mean("hybrid_rerank_ndcg_10"),
                },
            }

            # File-level metrics (only populated when expected_files is present)
            file_rows = [r for r in rows if "file_precision_8" in r]
            if file_rows:
                stats["file-level@8"] = {
                    "precision": _mean("file_precision_8"),
                    "recall": _mean("file_recall_8"),
                    "queries_with_file_ground_truth": len(file_rows),
                }

            return {
                "command": "rag-retrieval",
                "stats": stats,
                "rag_quality": {"retrieval": {"stats": stats, "rows": rows}},
            }

        return asyncio.run(_run())


def run_rag_answer_benchmark(args: argparse.Namespace) -> dict:

    from ._bench_env import bench_environment
    from ._bench_fixtures import ingest_all_fixtures
    from ._bench_rag_judge import (
        get_judge_config,
        judge_answer_relevance,
        judge_context_precision,
        judge_context_recall,
        judge_faithfulness,
    )

    if args.judge_model:
        from src.core.config import settings
        from src.services.llm import reset_llm

        settings.LLM_MODEL = args.judge_model
        reset_llm()

    judge_config = get_judge_config()
    print(f"judge_config: {json.dumps(judge_config, indent=2)}")

    with bench_environment():
        from src.core.database import close_all_connections, init_db
        from src.services.chain import ask

        close_all_connections()
        with open(FIXTURE_DIR / "golden_set.json", encoding="utf-8") as f:
            golden = json.load(f)

        rows = []
        parse_failures = 0

        async def _run() -> None:
            nonlocal parse_failures
            close_all_connections()
            init_db()
            fixture_info = await ingest_all_fixtures()
            meeting_ids = list({mid for mid, _ in fixture_info.values()})
            for item in golden.get("items", []):
                query = item["query"]
                result = await ask(
                    question=query,
                    user_id="benchmark",
                    meeting_ids=meeting_ids,
                )
                answer = result.answer
                sources = result.sources or []
                chunks = [s.get("content", "") for s in sources]
                context = "\n\n".join(chunks)

                f_scores = []
                r_scores = []
                c_scores = []
                cr_scores = []

                for _ in range(args.judge_repeats):
                    f = judge_faithfulness(answer, context)
                    r = judge_answer_relevance(query, answer)
                    c = judge_context_precision(query, chunks)
                    if f is None or r is None or c is None:
                        parse_failures += 1
                    f_scores.append(f["score"] if f else None)
                    r_scores.append(r["score"] if r else None)
                    c_scores.append(c["score"] if c else None)

                    expected_answer = item.get("expected_answer")
                    if expected_answer:
                        cr = judge_context_recall(query, expected_answer, chunks)
                        if cr is None:
                            parse_failures += 1
                        cr_scores.append(cr["score"] if cr else None)
                    else:
                        cr_scores.append(None)

                def _avg(scores):
                    valid = [s for s in scores if s is not None]
                    return sum(valid) / len(valid) if valid else None

                rouge = None
                cosine = None
                if item.get("expected_answer"):
                    rouge = _rouge_l_f1(item["expected_answer"], answer)
                    cosine = _embedding_cosine(item["expected_answer"], answer)

                rows.append({
                    "query_id": item["id"],
                    "faithfulness": _avg(f_scores),
                    "answer_relevance": _avg(r_scores),
                    "context_precision": _avg(c_scores),
                    "context_recall": _avg(cr_scores),
                    "rouge_l_f1": rouge,
                    "answer_similarity": cosine,
                })

        asyncio.run(_run())

    def _mean(key: str) -> float | None:
        vals = [r[key] for r in rows if r.get(key) is not None]
        return sum(vals) / len(vals) if vals else None

    stats = {
        "faithfulness": _mean("faithfulness"),
        "answer_relevance": _mean("answer_relevance"),
        "context_precision": _mean("context_precision"),
        "context_recall": _mean("context_recall"),
        "rouge_l_f1": _mean("rouge_l_f1"),
        "answer_similarity": _mean("answer_similarity"),
        "parse_failures": parse_failures,
    }

    return {
        "command": "rag-answer",
        "judge_config": judge_config,
        "stats": stats,
        "rag_quality": {"answer": {"stats": stats, "rows": rows}},
    }


def run_rag_snapshot_benchmark(args: argparse.Namespace) -> dict:

    from ._bench_env import bench_environment
    from ._bench_fixtures import ingest_all_fixtures

    with bench_environment():
        from src.core.database import close_all_connections, init_db
        from src.services.chain import ask

        close_all_connections()
        with open(FIXTURE_DIR / "golden_set.json", encoding="utf-8") as f:
            golden = json.load(f)

        snapshots: dict[str, dict] = {}
        for snap_path in RAG_SNAPSHOTS_DIR.glob("*.json"):
            with open(snap_path, encoding="utf-8") as f:
                data = json.load(f)
                snapshots[data.get("query_id")] = data

        current: list[dict] = []

        async def _run() -> None:
            close_all_connections()
            init_db()
            fixture_info = await ingest_all_fixtures()
            meeting_ids = list({mid for mid, _ in fixture_info.values()})
            for item in golden.get("items", []):
                result = await ask(
                    question=item["query"],
                    user_id="benchmark",
                    meeting_ids=meeting_ids,
                )
                source_ids = sorted({s.get("chunk_id", "") for s in (result.sources or [])})
                current.append({
                    "query_id": item["id"],
                    "answer": result.answer,
                    "source_ids": source_ids,
                })

        asyncio.run(_run())

    diffs = []
    for cur in current:
        qid = cur["query_id"]
        snap = snapshots.get(qid)
        if snap is None:
            diffs.append({"query_id": qid, "diff": "no existing snapshot"})
            continue
        if set(cur["source_ids"]) != set(snap.get("source_ids", [])):
            diffs.append({"query_id": qid, "diff": "source_ids changed"})
        if cur["answer"] != snap.get("answer", ""):
            diffs.append({"query_id": qid, "diff": "answer text changed"})

    if args.update_snapshots:
        for cur in current:
            qid = cur["query_id"]
            snap_path = RAG_SNAPSHOTS_DIR / f"{qid}.json"
            with open(snap_path, "w", encoding="utf-8") as f:
                json.dump(cur, f, indent=2)
        print(f"Updated {len(current)} snapshots in {RAG_SNAPSHOTS_DIR}")

    return {
        "command": "rag-snapshot",
        "stats": {"diffs": len(diffs)},
        "snapshot_diffs": diffs,
    }


def run_rag_all_benchmark(args: argparse.Namespace) -> dict:
    ret = run_rag_retrieval_benchmark(args)
    ans = run_rag_answer_benchmark(args)
    snap = run_rag_snapshot_benchmark(args)
    return {
        "command": "rag-all",
        "judge_config": ans.get("judge_config"),
        "stats": {
            "retrieval": ret.get("stats", {}),
            "answer": ans.get("stats", {}),
            "snapshot": snap.get("stats", {}),
        },
        "rag_quality": {
            "retrieval": ret.get("rag_quality", {}),
            "answer": ans.get("rag_quality", {}),
        },
        "snapshot_diffs": snap.get("snapshot_diffs", []),
    }


def run_rag_chunk_phase1(args: argparse.Namespace) -> dict:
    """Run Phase 1: compare chunk strategies."""
    from ._bench_rag_phase1 import AUDIO_CHUNK_CONFIGS, run_phase1, run_single_config

    if args.list_configs:
        print("Available chunk configs (use --config-index N to run a single one):")
        for i, cfg in enumerate(AUDIO_CHUNK_CONFIGS):
            print(f"  [{i}] {cfg.name} {cfg.preset} | chunk_size={cfg.chunk_size} | method={cfg.method}")
        return {"command": "rag-chunk-phase1", "stats": {}, "list_configs": True}

    scoped_path = Path(args.golden_scoped)
    unscoped_path = Path(args.golden_unscoped)

    if args.config_index is not None:
        if args.config_index < 0 or args.config_index >= len(AUDIO_CHUNK_CONFIGS):
            raise ValueError(
                f"--config-index must be between 0 and {len(AUDIO_CHUNK_CONFIGS) - 1}"
            )
        cfg = AUDIO_CHUNK_CONFIGS[args.config_index]
        print(f"Running single config: [{args.config_index}] {cfg.name} {cfg.preset}")
        payload = run_single_config(cfg, scoped_path, unscoped_path, top_k=args.top_k)
        payload["command"] = "rag-chunk-phase1"
        return payload

    payload = run_phase1(scoped_path, unscoped_path, top_k=args.top_k)
    payload["command"] = "rag-chunk-phase1"
    return payload


def run_rag_chunk_phase2(args: argparse.Namespace) -> dict:
    """Run Phase 2: retrieval grid search on top-2 chunk configs."""
    from ._bench_rag_phase2 import run_phase2

    phase1_path = Path(args.phase1_result)
    with open(phase1_path, encoding="utf-8") as f:
        phase1_data = json.load(f)

    top_2 = phase1_data.get("top_2", [])
    if not top_2:
        raise ValueError("Phase 1 result contains no top_2 configs")

    scoped_path = Path(args.golden_scoped)
    unscoped_path = Path(args.golden_unscoped)
    payload = run_phase2(top_2, scoped_path, unscoped_path, top_k=args.top_k)
    payload["command"] = "rag-chunk-phase2"
    return payload


def run_rag_chunk_full(args: argparse.Namespace) -> dict:
    """Run Phase 1 + Phase 2 end-to-end."""
    p1 = run_rag_chunk_phase1(args)
    # Write Phase 1 report explicitly
    json_path1, md_path1 = _write_report("rag-chunk-phase1", p1)
    print(f"Wrote {json_path1} and {md_path1}")

    # Write Phase 1 result to a temp path so Phase 2 can read it
    tmp_path = RESULTS_DIR / "_phase1_temp.json"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(p1, f, indent=2)

    # Fabricate a Namespace for Phase 2
    p2_args = argparse.Namespace(
        phase1_result=str(tmp_path),
        golden_scoped=args.golden_scoped,
        golden_unscoped=args.golden_unscoped,
        top_k=args.top_k,
    )
    p2 = run_rag_chunk_phase2(p2_args)
    # Write Phase 2 report explicitly
    json_path2, md_path2 = _write_report("rag-chunk-phase2", p2)
    print(f"Wrote {json_path2} and {md_path2}")

    return {
        "command": "rag-chunk-full",
        "stats": {
            "phase1_combined_recall": p1.get("top_2", [{}])[0].get("combined_recall", 0.0) if p1.get("top_2") else 0.0,
            "phase2_weighted_score": p2.get("recommendation", {}).get("weighted_score", 0.0) if p2.get("recommendation") else 0.0,
        },
        "phase1": p1,
        "phase2": p2,
    }


def run_rag_matrix_benchmark(args: argparse.Namespace) -> dict:
    """Run rag-retrieval benchmark across a matrix of configuration combinations."""
    import csv
    import itertools

    scoping_modes = [m.strip() for m in args.scoping_modes.split(",")]
    merge_strategies = [s.strip() for s in args.merge_strategies.split(",")]
    multi_query_flags = [f.strip() for f in args.multi_query.split(",")]

    combinations = list(itertools.product(scoping_modes, merge_strategies, multi_query_flags))
    print(
        f"Matrix: {len(combinations)} combinations "
        f"({len(scoping_modes)} scopes x {len(merge_strategies)} merges "
        f"x {len(multi_query_flags)} multi-query)"
    )

    results: list[dict] = []
    for idx, (scoping, merge, mq_flag) in enumerate(combinations, 1):
        label = f"{scoping}/{merge}/mq={mq_flag}"
        print(f"[{idx}/{len(combinations)}] {label}")

        # Mutate settings for this combination
        _set_rag_matrix_settings(scoping, merge, mq_flag)

        # Reuse rag-retrieval benchmark
        payload = run_rag_retrieval_benchmark(args)

        stats = payload.get("stats", {})
        hybrid = stats.get("hybrid@10", {})

        row = {
            "scoping_mode": scoping,
            "merge_strategy": merge,
            "multi_query": mq_flag,
            "hybrid_recall_10": hybrid.get("recall", 0.0),
            "hybrid_mrr": hybrid.get("mrr", 0.0),
            "hybrid_ndcg_10": hybrid.get("ndcg", 0.0),
        }
        results.append(row)
        print(f"  recall@10={row['hybrid_recall_10']:.4f}  mrr={row['hybrid_mrr']:.4f}  "
              f"ndcg@10={row['hybrid_ndcg_10']:.4f}")

    # Write CSV
    ts = datetime.now(UTC).isoformat()
    csv_path = RESULTS_DIR / f"matrix_{ts}.csv"
    if results:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=results[0].keys())
            writer.writeheader()
            writer.writerows(results)
        print(f"\nWrote {csv_path}")

    # Markdown summary
    md_path = RESULTS_DIR / f"matrix_{ts}.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# RAG Matrix Benchmark\n\n")
        f.write("| scoping_mode | merge_strategy | multi_query | recall@10 | MRR | nDCG@10 |\n")
        f.write("|---|---|---|---|---|---|\n")
        for r in results:
            f.write(f"| {r['scoping_mode']} | {r['merge_strategy']} | {r['multi_query']} "
                    f"| {r['hybrid_recall_10']:.4f} | {r['hybrid_mrr']:.4f} "
                    f"| {r['hybrid_ndcg_10']:.4f} |\n")
    print(f"Wrote {md_path}")

    return {
        "command": "rag-matrix",
        "combinations": len(combinations),
        "results": results,
        "csv_path": str(csv_path),
        "md_path": str(md_path),
    }


def _set_rag_matrix_settings(scoping: str, merge: str, mq_flag: str) -> None:
    """Patch global settings for a matrix combination."""
    from src.core.config import settings

    settings.RAG_FILE_SCOPING_MODE = scoping
    settings.RAG_FUNNEL_MERGE_STRATEGY = merge
    is_mq = mq_flag == "enabled"
    settings.MULTI_QUERY_ENABLED = is_mq
    settings.RAG_BROAD_RECALL_MULTI_QUERY_ENABLED = is_mq


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="benchmark",
        description="Performance benchmark harness for meeting-agent",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # chat
    chat_p = sub.add_parser("chat", help="Benchmark chat pipeline latency")
    chat_p.add_argument("--iterations", type=int, default=5)
    chat_p.add_argument("--baseline", action="store_true")
    chat_p.add_argument("--update-baseline", action="store_true")

    # ingest
    ingest_p = sub.add_parser("ingest", help="Benchmark ingest pipeline latency")
    ingest_p.add_argument("--iterations", type=int, default=3)
    ingest_p.add_argument("--baseline", action="store_true")
    ingest_p.add_argument("--update-baseline", action="store_true")

    # micro
    micro_p = sub.add_parser("micro", help="Run component micro-benchmarks")
    micro_p.add_argument("--baseline", action="store_true")
    micro_p.add_argument("--update-baseline", action="store_true")

    # rag-retrieval
    rag_ret_p = sub.add_parser("rag-retrieval", help="Benchmark retrieval metrics")
    rag_ret_p.add_argument("--top-k", type=int, default=10)
    rag_ret_p.add_argument("--baseline", action="store_true")
    rag_ret_p.add_argument("--update-baseline", action="store_true")

    # rag-answer
    rag_ans_p = sub.add_parser("rag-answer", help="Benchmark answer quality (LLM-as-judge)")
    rag_ans_p.add_argument("--judge-model", type=str, default=None)
    rag_ans_p.add_argument("--judge-repeats", type=int, default=1)
    rag_ans_p.add_argument("--baseline", action="store_true")
    rag_ans_p.add_argument("--update-baseline", action="store_true")

    # rag-snapshot
    rag_snap_p = sub.add_parser("rag-snapshot", help="Compare against answer snapshots")
    rag_snap_p.add_argument("--update-snapshots", action="store_true")
    rag_snap_p.add_argument("--baseline", action="store_true")
    rag_snap_p.add_argument("--update-baseline", action="store_true")

    # rag-all
    rag_all_p = sub.add_parser("rag-all", help="Run all RAG quality benchmarks")
    rag_all_p.add_argument("--top-k", type=int, default=10)
    rag_all_p.add_argument("--judge-repeats", type=int, default=1)
    rag_all_p.add_argument("--update-snapshots", action="store_true")
    rag_all_p.add_argument("--baseline", action="store_true")
    rag_all_p.add_argument("--update-baseline", action="store_true")

    # rag-matrix
    matrix_p = sub.add_parser("rag-matrix", help="Run retrieval across config matrix")
    matrix_p.add_argument(
        "--scoping-modes", type=str,
        default="router_and_funnel,funnel_only,router_only",
        help="Comma-separated scoping modes",
    )
    matrix_p.add_argument("--merge-strategies", type=str, default="rrf,zigzag",
                          help="Comma-separated merge strategies")
    matrix_p.add_argument("--multi-query", type=str, default="disabled,enabled",
                          help="Comma-separated multi-query flags")
    matrix_p.add_argument("--top-k", type=int, default=10)

    # rag-chunk-phase1
    rag_p1 = sub.add_parser("rag-chunk-phase1", help="Phase 1: compare audio chunk strategies")
    rag_p1.add_argument("--golden-scoped", default=str(FIXTURE_DIR / "amicorpus_golden_scoped.json"))
    rag_p1.add_argument("--golden-unscoped", default=str(FIXTURE_DIR / "amicorpus_golden_unscoped.json"))
    rag_p1.add_argument("--top-k", type=int, default=10)
    rag_p1.add_argument("--config-index", type=int, default=None, help="Run only a single config by index (0-based); omit to run all")
    rag_p1.add_argument("--list-configs", action="store_true", help="List all available chunk configs and exit")
    rag_p1.add_argument("--baseline", action="store_true")
    rag_p1.add_argument("--update-baseline", action="store_true")

    # rag-chunk-phase2
    rag_p2 = sub.add_parser("rag-chunk-phase2", help="Phase 2: retrieval grid search on top-2 chunk configs")
    rag_p2.add_argument("--phase1-result", required=True)
    rag_p2.add_argument("--golden-scoped", default=str(FIXTURE_DIR / "amicorpus_golden_scoped.json"))
    rag_p2.add_argument("--golden-unscoped", default=str(FIXTURE_DIR / "amicorpus_golden_unscoped.json"))
    rag_p2.add_argument("--top-k", type=int, default=10)
    rag_p2.add_argument("--baseline", action="store_true")
    rag_p2.add_argument("--update-baseline", action="store_true")

    # rag-chunk-full
    rag_full = sub.add_parser("rag-chunk-full", help="End-to-end Phase 1 + Phase 2")
    rag_full.add_argument("--golden-scoped", default=str(FIXTURE_DIR / "amicorpus_golden_scoped.json"))
    rag_full.add_argument("--golden-unscoped", default=str(FIXTURE_DIR / "amicorpus_golden_unscoped.json"))
    rag_full.add_argument("--top-k", type=int, default=10)
    rag_full.add_argument("--baseline", action="store_true")
    rag_full.add_argument("--update-baseline", action="store_true")

    # all
    all_p = sub.add_parser("all", help="Run chat + ingest + micro + rag-all")
    all_p.add_argument("--iterations", type=int, default=5)
    all_p.add_argument("--top-k", type=int, default=10)
    all_p.add_argument("--judge-repeats", type=int, default=1)
    all_p.add_argument("--update-snapshots", action="store_true")
    all_p.add_argument("--baseline", action="store_true")
    all_p.add_argument("--update-baseline", action="store_true")

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    threshold = float(os.environ.get("BENCH_REGRESSION_THRESHOLD", "0.25"))

    payloads: list[dict] = []

    if args.command == "chat":
        payload = run_chat_benchmark(args)
        payloads.append(payload)
    elif args.command == "ingest":
        payload = run_ingest_benchmark(args)
        payloads.append(payload)
    elif args.command == "micro":
        payload = run_micro_benchmark(args)
        payloads.append(payload)
    elif args.command == "rag-retrieval":
        payload = run_rag_retrieval_benchmark(args)
        payloads.append(payload)
    elif args.command == "rag-answer":
        payload = run_rag_answer_benchmark(args)
        payloads.append(payload)
    elif args.command == "rag-snapshot":
        payload = run_rag_snapshot_benchmark(args)
        payloads.append(payload)
    elif args.command == "rag-all":
        payload = run_rag_all_benchmark(args)
        payloads.append(payload)
    elif args.command == "rag-matrix":
        payload = run_rag_matrix_benchmark(args)
        payloads.append(payload)
    elif args.command == "rag-chunk-phase1":
        payload = run_rag_chunk_phase1(args)
        payloads.append(payload)
    elif args.command == "rag-chunk-phase2":
        payload = run_rag_chunk_phase2(args)
        payloads.append(payload)
    elif args.command == "rag-chunk-full":
        payload = run_rag_chunk_full(args)
        payloads.append(payload)
    elif args.command == "all":
        payloads.append(run_chat_benchmark(args))
        payloads.append(run_ingest_benchmark(args))
        run_micro_benchmark(args)
        payloads.append(run_rag_all_benchmark(args))
    else:
        parser.print_help()
        return 1

    # Write reports
    for payload in payloads:
        if "stats" in payload and payload.get("list_configs"):
            continue
        if "stats" in payload or payload.get("phase") in (1, 2) or ("phase1" in payload and "phase2" in payload):
            json_path, md_path = _write_report(payload["command"], payload)
            print(f"Wrote {json_path} and {md_path}")

    # Baseline handling
    baseline = _load_baseline()
    if args.update_baseline:
        combined = {
            "timestamp": datetime.now(UTC).isoformat(),
            "payloads": payloads,
        }
        with open(BASELINE_PATH, "w", encoding="utf-8") as f:
            json.dump(combined, f, indent=2)
        print(f"Updated baseline: {BASELINE_PATH}")

    if args.baseline:
        all_regressions: list[str] = []
        for payload in payloads:
            regressions = _compare_baseline(payload, baseline, threshold)
            all_regressions.extend(regressions)
        if all_regressions:
            print(f"\nRegressions detected (threshold={threshold:.0%}):")
            for r in all_regressions:
                print(f"  - {r}")
            return 1
        print("\nNo regressions detected against baseline.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
