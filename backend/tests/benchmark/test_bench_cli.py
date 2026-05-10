"""CLI parser coverage for benchmark commands."""

from scripts.benchmark import _build_parser


def test_rag_all_parser_has_rag_quality_args() -> None:
    args = _build_parser().parse_args(["rag-all"])

    assert args.top_k == 10
    assert args.judge_repeats == 1
    assert args.update_snapshots is False


def test_all_parser_has_rag_quality_args() -> None:
    args = _build_parser().parse_args(["all"])

    assert args.top_k == 10
    assert args.judge_repeats == 1
    assert args.update_snapshots is False
