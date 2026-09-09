"""Generate golden query/answer sets from indexed AMI corpus chunks via LLM.

This is intended to be run once to produce:
  - backend/tests/fixtures/benchmark/amicorpus_golden_scoped.json
  - backend/tests/fixtures/benchmark/amicorpus_golden_unscoped.json

If the files already exist, benchmark runners will use them directly.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from ._bench_amicorpus import ingest_all_amicorpus
from ._bench_chunk_configs import apply_chunk_config, lock_benchmark_settings
from ._bench_env import bench_environment
from ._bench_map_golden import load_chunks_from_vectorstore

logger = logging.getLogger(__name__)
FIXTURE_DIR = Path(__file__).parent.parent / "tests" / "fixtures" / "benchmark"


def _build_scoped_prompt(chunks_text: str, n: int) -> str:
    return (
        f"You are a test-data generator for a meeting QA system.\n"
        f"Given the following meeting transcript chunks:\n\n{chunks_text}\n\n"
        f"Generate {n} realistic user questions that can be answered solely from the text above.\n"
        f"Requirements:\n"
        f"1. Each question must be answerable using only the provided chunks.\n"
        f"2. Cover factual, speaker-specific, temporal, and summary query types.\n"
        f"3. For each question, you MUST specify which meeting_id and file_id "
        f"the answer comes from.\n"
        f"   The chunks above are prefixed with [meeting_id=X, file_id=Y]. Use these exact IDs.\n"
        f"4. Answer length must be mixed: about half should be SHORT (1-3 sentences), "
        f"   and about half should be DESCRIPTIVE (a full paragraph with details).\n"
        f"5. Output as JSON array with this exact schema:\n"
        f"   [{{"
        f'     "query":"...",'
        f'     "expected_answer":"...",'
        f'     "query_type":"factual|speaker|temporal|summary",'
        f'     "expected_meeting_ids":[1],'
        f'     "expected_file_ids":[1]'
        f"   }}]"
    )


def _build_unscoped_prompt(summaries_text: str, n: int) -> str:
    return (
        f"You are a test-data generator for a meeting QA system.\n"
        f"Given summaries of multiple meetings:\n\n{summaries_text}\n\n"
        f"Generate {n} cross-meeting queries.\n"
        f"Requirements:\n"
        f"1. Answers must be distributed across multiple meetings.\n"
        f"2. Types: comparison, summary, cross-meeting factual.\n"
        f"3. For each question, you MUST list all meeting_id and file_id values "
        f"involved in the answer.\n"
        f"   The summaries above are prefixed with [meeting_id=X, file_id=Y]. "
        f"Use these exact IDs.\n"
        f"4. Answer length must be mixed: about half should be SHORT (1-3 sentences), "
        f"   and about half should be DESCRIPTIVE (a full paragraph with details).\n"
        f"5. Output as JSON array with this exact schema:\n"
        f"   [{{"
        f'     "query":"...",'
        f'     "expected_answer":"...",'
        f'     "query_type":"comparison|summary|factual",'
        f'     "expected_meeting_ids":[1, 2],'
        f'     "expected_file_ids":[1, 2]'
        f"   }}]"
    )


async def generate_queries_from_chunks(
    chunks: list[dict[str, Any]],
    scope_type: str,
    num_queries: int,
) -> list[dict[str, Any]]:
    """Call LLM to generate query + answer pairs (chunk-agnostic)."""
    from src.services.llm import get_llm

    # Group chunks by (meeting_id, file_id) and join 3-5 adjacent chunks
    grouped: dict[tuple[int, int | None], list[dict]] = {}
    for c in chunks:
        key = (c.get("meeting_id", 0), c.get("file_id"))
        grouped.setdefault(key, []).append(c)

    # Sort by chunk index within each group and form sliding windows
    per_group_windows: dict[tuple[int, int | None], list[str]] = {}
    for key, group in grouped.items():
        mid, fid = key
        group.sort(key=lambda x: x.get("chunk_index", 0))
        group_windows: list[str] = []
        for i in range(0, len(group), 3):
            window = group[i : i + 5]
            text = "\n---\n".join(w["text"] for w in window)
            header = f"[meeting_id={mid}, file_id={fid}]"
            group_windows.append(f"{header}\n{text}")
        per_group_windows[key] = group_windows

    # Sample evenly across all meetings so the LLM sees every meeting,
    # not just the first few windows of the first meeting.
    windows: list[str] = []
    group_keys = list(per_group_windows.keys())
    max_per_group = max(len(w) for w in per_group_windows.values())
    for idx in range(max_per_group):
        for key in group_keys:
            group_w = per_group_windows[key]
            if idx < len(group_w):
                windows.append(group_w[idx])

    # Cap at ~30 windows to keep prompt size reasonable
    sampled = windows[:30]

    if scope_type == "scoped":
        prompt = _build_scoped_prompt("\n\n".join(sampled), num_queries)
    else:
        prompt = _build_unscoped_prompt("\n\n".join(sampled), num_queries)

    llm = get_llm()
    response = await llm.ainvoke(prompt)
    content = getattr(response, "content", str(response))

    # Extract JSON array from response
    try:
        start = content.index("[")
        end = content.rindex("]") + 1
        items = json.loads(content[start:end])
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning("Failed to parse LLM golden generation response: %s", exc)
        return []

    # Validate and enrich items
    for item in items:
        item.setdefault("expected_meeting_ids", [])
        item.setdefault("expected_file_ids", [])
        item.setdefault("source", "Meeting Transcript")

        # Ensure IDs are lists of integers, not strings
        mids = item.get("expected_meeting_ids", [])
        fids = item.get("expected_file_ids", [])
        if isinstance(mids, str):
            item["expected_meeting_ids"] = [
                int(x.strip()) for x in mids.split(",") if x.strip().isdigit()
            ]
        else:
            item["expected_meeting_ids"] = [
                int(x) for x in mids if isinstance(x, (int, str)) and str(x).isdigit()
            ]
        if isinstance(fids, str):
            item["expected_file_ids"] = [
                int(x.strip()) for x in fids.split(",") if x.strip().isdigit()
            ]
        else:
            item["expected_file_ids"] = [
                int(x) for x in fids if isinstance(x, (int, str)) and str(x).isdigit()
            ]

    return items


async def generate_golden_sets(
    num_scoped: int = 10,
    num_unscoped: int = 5,
) -> tuple[list[dict], list[dict]]:
    """Generate both scoped and unscoped golden sets using the default chunk strategy."""
    with bench_environment():
        from src.core.database import close_all_connections, init_db

        close_all_connections()
        init_db()
        lock_benchmark_settings()
        from ._bench_chunk_configs import ChunkConfig

        apply_chunk_config(
            ChunkConfig(
                name="A Native (Segment-Aware)",
                preset="M",
                method="native",
                chunk_size=1024,
                chunk_overlap=128,
                parent_child_enabled=False,
                audio_semantic_boundary_enabled=True,
                audio_semantic_boundary_threshold=0.5,
            )
        )

        fixture_map = await ingest_all_amicorpus()
        meeting_ids = [mid for mid, _ in fixture_map.values()]
        all_chunks = load_chunks_from_vectorstore(meeting_ids=meeting_ids)

        scoped = await generate_queries_from_chunks(all_chunks, "scoped", num_scoped)
        unscoped = await generate_queries_from_chunks(all_chunks, "unscoped", num_unscoped)

    return scoped, unscoped


def save_golden_sets(
    scoped: list[dict],
    unscoped: list[dict],
    output_scoped: Path | None = None,
    output_unscoped: Path | None = None,
) -> None:
    out_s = output_scoped or FIXTURE_DIR / "amicorpus_golden_scoped.json"
    out_u = output_unscoped or FIXTURE_DIR / "amicorpus_golden_unscoped.json"

    out_s.parent.mkdir(parents=True, exist_ok=True)
    out_u.parent.mkdir(parents=True, exist_ok=True)

    def _assign_ids(items: list[dict], prefix: str) -> list[dict]:
        for i, item in enumerate(items, 1):
            item["id"] = f"{prefix}_{i:03d}"
        return items

    with open(out_s, "w", encoding="utf-8") as f:
        json.dump(
            {
                "version": 1,
                "scope_type": "scoped",
                "modality": "audio",
                "items": _assign_ids(scoped, "audio_scoped"),
            },
            f,
            indent=2,
            ensure_ascii=False,
        )

    with open(out_u, "w", encoding="utf-8") as f:
        json.dump(
            {
                "version": 1,
                "scope_type": "unscoped",
                "modality": "audio",
                "items": _assign_ids(unscoped, "audio_unscoped"),
            },
            f,
            indent=2,
            ensure_ascii=False,
        )

    logger.info("Saved golden sets to %s and %s", out_s, out_u)


async def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Generate AMI corpus golden query sets")
    parser.add_argument("--num-scoped", type=int, default=10)
    parser.add_argument("--num-unscoped", type=int, default=5)
    parser.add_argument(
        "--output-scoped", type=Path, default=FIXTURE_DIR / "amicorpus_golden_scoped.json"
    )
    parser.add_argument(
        "--output-unscoped", type=Path, default=FIXTURE_DIR / "amicorpus_golden_unscoped.json"
    )
    args = parser.parse_args()

    scoped, unscoped = await generate_golden_sets(args.num_scoped, args.num_unscoped)
    save_golden_sets(scoped, unscoped, args.output_scoped, args.output_unscoped)


if __name__ == "__main__":
    asyncio.run(main())
