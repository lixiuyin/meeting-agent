"""Phase 2 benchmark: retrieval grid search on top-2 chunk configs."""

from __future__ import annotations

import asyncio
import json
import logging
import statistics
from pathlib import Path
from typing import Any

from ._bench_amicorpus import ingest_all_amicorpus
from ._bench_chunk_configs import (
    ChunkConfig,
    apply_chunk_config,
    apply_retrieval_config,
    config_to_dict,
    lock_benchmark_settings,
)
from ._bench_env import bench_environment
from ._bench_map_golden import compute_expected_chunks, load_chunks_from_vectorstore
from ._bench_rag_quality import (
    file_recall_at_k,
    mrr,
    ndcg_at_k,
    recall_at_k,
)

logger = logging.getLogger(__name__)

FIXTURE_DIR = Path(__file__).parent.parent / "tests" / "fixtures" / "benchmark"

RETRIEVAL_GRID = [
    ("vector", ""),
    ("vector", "bge"),
    ("hybrid", ""),
    ("hybrid", "bge"),
]


def _load_golden(path: Path) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("items", [])


def _retrieve_ids(results: list[dict]) -> list[str]:
    ids = []
    for r in results:
        meta = r.get("metadata", {})
        chunk_id = meta.get("chunk_id")
        if not chunk_id:
            mid = meta.get("meeting_id", "unknown")
            fid = meta.get("file_id")
            idx = meta.get("chunk_index")
            if fid is not None and idx is not None:
                chunk_id = f"meeting_{mid}_file_{fid}_chunk_{idx}"
            elif idx is not None:
                chunk_id = f"meeting_{mid}_chunk_{idx}"
            else:
                chunk_id = str(meta)
        ids.append(chunk_id)
    return ids


def _evaluate_retrieval(
    retrieved_ids: list[str],
    expected: set[str],
    top_k: int = 10,
) -> dict[str, float]:
    return {
        f"recall@{top_k}": recall_at_k(retrieved_ids, expected, top_k),
        "mrr": mrr(retrieved_ids, expected),
        f"ndcg@{top_k}": ndcg_at_k(retrieved_ids, expected, top_k),
    }


async def _run_single_combination(
    cfg: ChunkConfig,
    provider: str,
    reranker_binding: str,
    scoped_items: list[dict],
    unscoped_items: list[dict],
    top_k: int = 10,
) -> dict[str, Any]:
    """Run one (chunk config, retrieval strategy, reranker) combination."""
    results: dict[str, Any] = {
        "config": config_to_dict(cfg),
        "provider": provider,
        "reranker": reranker_binding,
        "scoped": [],
        "unscoped": [],
    }

    with bench_environment():
        from src.core.config import settings
        from src.core.database import close_all_connections, init_db
        from src.services.rag import rerank, retrieve

        close_all_connections()
        init_db()
        lock_benchmark_settings()
        apply_chunk_config(cfg)
        apply_retrieval_config(provider, reranker_binding)

        # Over-fetch when reranker is enabled so it has candidates to reorder
        fetch_multiplier = settings.RAG_RERANK_FETCH_MULTIPLIER if reranker_binding else 1

        fixture_map = await ingest_all_amicorpus()
        meeting_ids = [mid for mid, _ in fixture_map.values()]

        all_chunks = load_chunks_from_vectorstore(meeting_ids=meeting_ids)

        # Scoped
        for item in scoped_items:
            query = item["query"]
            expected_mids = item.get("expected_meeting_ids", [])
            expected_fids = item.get("expected_file_ids", [])

            # Restrict expected-chunk computation to the same scope as retrieval
            scoped_chunks = all_chunks
            if expected_mids:
                scoped_chunks = [c for c in scoped_chunks if c.get("meeting_id") in expected_mids]
            if expected_fids:
                scoped_chunks = [c for c in scoped_chunks if c.get("file_id") in expected_fids]
            expected_chunk_ids = set(compute_expected_chunks(item, scoped_chunks, method="hybrid"))

            target_mids = expected_mids if expected_mids else meeting_ids
            target_fids = expected_fids if expected_fids else None

            retrieved, _qa = retrieve(
                query,
                meeting_ids=target_mids,
                file_ids=target_fids,
                top_k=top_k,
                fetch_multiplier=fetch_multiplier,
            )
            if reranker_binding:
                retrieved = rerank(query, retrieved, top_n=top_k)
            retrieved_ids = _retrieve_ids(retrieved)
            results["scoped"].append(
                {
                    "query_id": item["id"],
                    **_evaluate_retrieval(retrieved_ids, expected_chunk_ids, top_k),
                }
            )

        # Unscoped
        for item in unscoped_items:
            query = item["query"]
            expected_chunk_ids = set(compute_expected_chunks(item, all_chunks, method="hybrid"))
            expected_fids = set(item.get("expected_file_ids", []))

            retrieved, _qa = retrieve(
                query,
                meeting_ids=None,
                file_ids=None,
                top_k=top_k,
                fetch_multiplier=fetch_multiplier,
            )
            if reranker_binding:
                retrieved = rerank(query, retrieved, top_n=top_k)
            retrieved_ids = _retrieve_ids(retrieved)

            retrieved_file_ids = list(
                dict.fromkeys(
                    r.get("metadata", {}).get("file_id")
                    for r in retrieved
                    if r.get("metadata", {}).get("file_id") is not None
                )
            )
            file_cov = file_recall_at_k(retrieved_file_ids, expected_fids, top_k)

            metrics = _evaluate_retrieval(retrieved_ids, expected_chunk_ids, top_k)
            metrics["file_coverage"] = file_cov
            results["unscoped"].append(
                {
                    "query_id": item["id"],
                    **metrics,
                }
            )

    return results


def _aggregate(rows: list[dict]) -> dict[str, float]:
    if not rows:
        return {}
    keys = [k for k in rows[0] if k != "query_id"]
    return {k: float(statistics.mean([r[k] for r in rows if k in r])) for k in keys}


def _compute_weighted_score(
    unscoped: dict[str, float],
    scoped: dict[str, float],
    top_k: int = 10,
) -> float:
    """Weighted combined score per benchmark plan."""
    return (
        0.4 * unscoped.get(f"recall@{top_k}", 0.0)
        + 0.3 * scoped.get(f"recall@{top_k}", 0.0)
        + 0.2 * unscoped.get("file_coverage", 0.0)
        + 0.1 * unscoped.get(f"ndcg@{top_k}", 0.0)
    )


def run_phase2(
    top_2_configs: list[dict],
    scoped_path: Path,
    unscoped_path: Path,
    top_k: int = 10,
) -> dict[str, Any]:
    """Run Phase 2 grid search and return results with recommendation."""
    scoped_items = _load_golden(scoped_path)
    unscoped_items = _load_golden(unscoped_path)

    all_results: list[dict] = []
    for cfg_data in top_2_configs:
        cfg = ChunkConfig(**cfg_data["config"])
        for provider, reranker in RETRIEVAL_GRID:
            label = f"{cfg.name} {cfg.preset} | {provider} | rerank={reranker or 'off'}"
            logger.info("Phase 2 — running %s", label)
            run_result = asyncio.run(
                _run_single_combination(
                    cfg, provider, reranker, scoped_items, unscoped_items, top_k
                )
            )

            scoped_metrics = _aggregate(run_result["scoped"])
            unscoped_metrics = _aggregate(run_result["unscoped"])
            score = _compute_weighted_score(unscoped_metrics, scoped_metrics, top_k)

            all_results.append(
                {
                    "label": label,
                    "config": run_result["config"],
                    "provider": provider,
                    "reranker": reranker,
                    "scoped_metrics": scoped_metrics,
                    "unscoped_metrics": unscoped_metrics,
                    "weighted_score": score,
                    "rows": run_result,
                }
            )
            logger.info("  weighted_score = %.4f", score)

    # Pick best
    best = max(all_results, key=lambda x: x["weighted_score"]) if all_results else None

    return {
        "phase": 2,
        "top_k": top_k,
        "evidence_quality": {
            "grade": "tuning_only",
            "release_ready": False,
            "limitations": ["configuration_selected_and_scored_on_same_dataset"],
        },
        "all_results": all_results,
        "recommendation": best,
    }
