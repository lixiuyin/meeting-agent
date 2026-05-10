"""Manually backfill session summaries.

Usage:
    uv run python -m scripts.backfill_session_summaries --max-batch 20 --until-empty
"""

from __future__ import annotations

import argparse
import asyncio


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backfill session summaries in batches")
    parser.add_argument("--user-id", default=None, help="Optional user_id scope")
    parser.add_argument("--max-batch", type=int, default=20, help="Maximum sessions per sweep")
    parser.add_argument(
        "--until-empty",
        action="store_true",
        help="Keep running sweeps until there are no unsummarized sessions left",
    )
    return parser


async def _run(user_id: str | None, max_batch: int, until_empty: bool) -> int:
    from src.core.database import init_db
    from src.services.memory import session_summary_service

    init_db()
    total = 0
    sweep = 0
    while True:
        sweep += 1
        count = await session_summary_service.summarize_unsummarized(
            user_id=user_id,
            max_batch=max_batch,
        )
        total += count
        print(f"sweep={sweep} generated={count} total={total}")
        if not until_empty or count == 0:
            break
    return total


def main() -> None:
    args = _build_parser().parse_args()
    total = asyncio.run(_run(args.user_id, args.max_batch, args.until_empty))
    print(f"done: generated={total}")


if __name__ == "__main__":
    main()
