"""Phase 1 benchmark: compare chunk strategies for audio modality."""

from __future__ import annotations

import asyncio
import json
import logging
import statistics
from pathlib import Path
from typing import Any

from ._bench_amicorpus import ingest_all_amicorpus
from ._bench_chunk_configs import (
    AUDIO_CHUNK_CONFIGS,
    ChunkConfig,
    apply_chunk_config,
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


def _load_golden(path: Path) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("items", [])


def _retrieve_ids(results: list[dict]) -> list[str]:
    """Extract chunk IDs from retrieve() output."""
    ids = []
    for r in results:
        meta = r.get("metadata", {})
        chunk_id = meta.get("chunk_id")
        if not chunk_id:
            # Fallback: build from metadata fields
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


async def _run_single_config_impl(
    cfg: ChunkConfig,
    scoped_items: list[dict],
    unscoped_items: list[dict],
    top_k: int = 10,
) -> dict[str, Any]:
    """Run one chunk config end-to-end inside an isolated bench environment."""
    results: dict[str, Any] = {
        "config": config_to_dict(cfg),
        "scoped": [],
        "unscoped": [],
    }

    with bench_environment():
        from src.core.database import close_all_connections, init_db
        from src.services.rag import retrieve

        # Clear stale thread-local connections from previous benchmark runs
        close_all_connections()
        init_db()
        lock_benchmark_settings()
        apply_chunk_config(cfg)

        # Ingest all 4 AMI meetings
        fixture_map = await ingest_all_amicorpus()
        meeting_ids = [mid for mid, _ in fixture_map.values()]

        # Load chunks and compute dynamic expected chunks
        all_chunks = load_chunks_from_vectorstore(meeting_ids=meeting_ids)

        # Pre-warm embedding query cache so retrieve() does not repeatedly
        # call the embedding API for the same golden queries across configs.
        unique_queries = list({item["query"] for item in scoped_items + unscoped_items})
        if unique_queries:
            from src.services.embedder import get_embeddings

            embeddings = get_embeddings()
            logger.info(
                "Pre-warming embedding cache for %d unique queries ...", len(unique_queries)
            )
            for q in unique_queries:
                try:
                    embeddings.embed_query(q)
                except Exception:
                    logger.warning("Failed to pre-warm embedding for query: %s", q, exc_info=True)
            logger.info("Embedding cache warm-up complete.")

        # --- Scoped queries ---
        for item in scoped_items:
            query = item["query"]
            expected_mids = item.get("expected_meeting_ids", [])
            expected_fids = item.get("expected_file_ids", [])

            # Restrict expected-chunk computation to the same scope as retrieval
            # so that ground truth does not include chunks from meetings/files
            # that scoped retrieve() will never see.
            scoped_chunks = all_chunks
            if expected_mids:
                scoped_chunks = [c for c in scoped_chunks if c.get("meeting_id") in expected_mids]
            if expected_fids:
                scoped_chunks = [c for c in scoped_chunks if c.get("file_id") in expected_fids]

            # Compute expected chunks for this query under current chunk strategy
            expected_chunk_ids = set(compute_expected_chunks(item, scoped_chunks, method="hybrid"))

            # If golden specifies meeting/file scope, use it; otherwise use all
            target_mids = expected_mids if expected_mids else meeting_ids
            target_fids = expected_fids if expected_fids else None

            retrieved, _qa = retrieve(
                query,
                meeting_ids=target_mids,
                file_ids=target_fids,
                top_k=top_k,
                fetch_multiplier=1,
            )
            retrieved_ids = _retrieve_ids(retrieved)

            results["scoped"].append(
                {
                    "query_id": item["id"],
                    "expected_chunks": list(expected_chunk_ids),
                    "retrieved_ids": retrieved_ids,
                    **_evaluate_retrieval(retrieved_ids, expected_chunk_ids, top_k),
                }
            )

        # --- Unscoped queries ---
        for item in unscoped_items:
            query = item["query"]
            expected_chunk_ids = set(compute_expected_chunks(item, all_chunks, method="hybrid"))
            expected_fids = set(item.get("expected_file_ids", []))

            retrieved, _qa = retrieve(
                query,
                meeting_ids=None,
                file_ids=None,
                top_k=top_k,
            )
            retrieved_ids = _retrieve_ids(retrieved)

            # File coverage
            retrieved_file_ids = list(
                dict.fromkeys(
                    r.get("metadata", {}).get("file_id")
                    for r in retrieved
                    if r.get("metadata", {}).get("file_id") is not None
                )
            )
            file_cov = file_recall_at_k(retrieved_file_ids, expected_fids, top_k)

            results["unscoped"].append(
                {
                    "query_id": item["id"],
                    "expected_chunks": list(expected_chunk_ids),
                    "retrieved_ids": retrieved_ids,
                    "file_coverage": file_cov,
                    **_evaluate_retrieval(retrieved_ids, expected_chunk_ids, top_k),
                }
            )

    return results


def _aggregate_metrics(rows: list[dict]) -> dict[str, float]:
    if not rows:
        return {}
    keys = [k for k in rows[0] if k not in ("query_id", "expected_chunks", "retrieved_ids")]
    return {k: float(statistics.mean([r[k] for r in rows if k in r])) for k in keys}


def run_single_config(
    cfg: ChunkConfig,
    scoped_path: Path,
    unscoped_path: Path,
    top_k: int = 10,
) -> dict[str, Any]:
    """Run a single chunk config and return its result."""
    scoped_items = _load_golden(scoped_path)
    unscoped_items = _load_golden(unscoped_path)

    label = f"{cfg.name} {cfg.preset}"
    logger.info("Phase 1 — running %s", label)
    run_result = asyncio.run(_run_single_config_impl(cfg, scoped_items, unscoped_items, top_k))

    scoped_metrics = _aggregate_metrics(run_result["scoped"])
    unscoped_metrics = _aggregate_metrics(run_result["unscoped"])
    combined_recall = 0.5 * scoped_metrics.get(f"recall@{top_k}", 0.0) + 0.5 * unscoped_metrics.get(
        f"recall@{top_k}", 0.0
    )

    return {
        "phase": 1,
        "top_k": top_k,
        "label": label,
        "config": run_result["config"],
        "scoped_metrics": scoped_metrics,
        "unscoped_metrics": unscoped_metrics,
        "combined_recall": combined_recall,
        "rows": run_result,
    }


def run_phase1(
    scoped_path: Path,
    unscoped_path: Path,
    top_k: int = 10,
) -> dict[str, Any]:
    """Run Phase 1 for all audio chunk configs and return results + top-2."""
    scoped_items = _load_golden(scoped_path)
    unscoped_items = _load_golden(unscoped_path)

    config_results: list[dict] = []
    for cfg in AUDIO_CHUNK_CONFIGS:
        label = f"{cfg.name} {cfg.preset}"
        logger.info("Phase 1 — running %s", label)
        run_result = asyncio.run(_run_single_config_impl(cfg, scoped_items, unscoped_items, top_k))

        scoped_metrics = _aggregate_metrics(run_result["scoped"])
        unscoped_metrics = _aggregate_metrics(run_result["unscoped"])

        combined_recall = 0.5 * scoped_metrics.get(
            f"recall@{top_k}", 0.0
        ) + 0.5 * unscoped_metrics.get(f"recall@{top_k}", 0.0)

        config_results.append(
            {
                "label": label,
                "config": run_result["config"],
                "scoped_metrics": scoped_metrics,
                "unscoped_metrics": unscoped_metrics,
                "combined_recall": combined_recall,
                "rows": run_result,
            }
        )
        logger.info("  combined_recall@%d = %.4f", top_k, combined_recall)

    # Select top-2, enforcing diversity (different methods)
    sorted_by_recall = sorted(config_results, key=lambda x: x["combined_recall"], reverse=True)

    top_2: list[dict] = []
    seen_methods: set[str] = set()
    # First pass: pick the best from each distinct method
    for r in sorted_by_recall:
        method = r["config"]["method"]
        if method not in seen_methods:
            top_2.append(r)
            seen_methods.add(method)
        if len(top_2) >= 2:
            break
    # Fallback: if all configs share the same method, fill from top overall
    if len(top_2) < 2:
        for r in sorted_by_recall:
            if r not in top_2:
                top_2.append(r)
            if len(top_2) >= 2:
                break

    return {
        "phase": 1,
        "top_k": top_k,
        "evidence_quality": {
            "grade": "tuning_only",
            "release_ready": False,
            "limitations": ["configuration_selected_and_scored_on_same_dataset"],
        },
        "all_results": config_results,
        "top_2": top_2,
    }
