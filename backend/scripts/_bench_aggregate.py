"""Trace aggregation helpers for benchmark reports."""

from __future__ import annotations

import statistics
from dataclasses import dataclass


@dataclass
class SpanStats:
    """Aggregated statistics for a single trace span label."""

    label: str
    phase: str
    n: int
    p50: float
    p95: float
    p99: float
    mean: float
    stdev: float
    status_counts: dict[str, int]

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "phase": self.phase,
            "n": self.n,
            "p50": round(self.p50, 2),
            "p95": round(self.p95, 2),
            "p99": round(self.p99, 2),
            "mean": round(self.mean, 2),
            "stdev": round(self.stdev, 2),
            "status_counts": self.status_counts,
        }


def _percentile(sorted_values: list[float], p: float) -> float:
    """Compute percentile using nearest-rank method."""
    if not sorted_values:
        return 0.0
    k = (len(sorted_values) - 1) * (p / 100.0)
    f = int(k)
    c = f + 1 if f + 1 < len(sorted_values) else f
    if f == c:
        return sorted_values[f]
    return sorted_values[f] * (c - k) + sorted_values[c] * (k - f)


def aggregate(traces: list[dict]) -> dict[str, SpanStats]:
    """Aggregate a list of trace dicts into per-span statistics.

    Args:
        traces: list of serialized TraceContext dicts.

    Returns:
        Mapping from span label to SpanStats.
    """
    by_label: dict[str, list[dict]] = {}
    for trace in traces:
        for span in trace.get("spans", []):
            label = span["label"]
            by_label.setdefault(label, []).append(span)

    stats: dict[str, SpanStats] = {}
    for label, spans in by_label.items():
        durations = [s["duration_ms"] for s in spans if s.get("duration_ms") is not None]
        durations_sorted = sorted(durations)
        status_counts: dict[str, int] = {}
        for s in spans:
            status = s.get("status", "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1

        phase = spans[0].get("phase", "unknown") if spans else "unknown"
        n = len(durations)
        if n == 0:
            stats[label] = SpanStats(
                label=label,
                phase=phase,
                n=0,
                p50=0.0,
                p95=0.0,
                p99=0.0,
                mean=0.0,
                stdev=0.0,
                status_counts=status_counts,
            )
            continue

        mean = statistics.mean(durations_sorted)
        stdev = statistics.stdev(durations_sorted) if n > 1 else 0.0
        stats[label] = SpanStats(
            label=label,
            phase=phase,
            n=n,
            p50=_percentile(durations_sorted, 50),
            p95=_percentile(durations_sorted, 95),
            p99=_percentile(durations_sorted, 99),
            mean=mean,
            stdev=stdev,
            status_counts=status_counts,
        )

    return stats


def format_markdown(stats: dict[str, SpanStats]) -> str:
    """Render aggregated stats as a markdown table."""
    lines = [
        "# Benchmark Results\n",
        "## Latency by Span\n",
        "| label | phase | n | p50 (ms) | p95 (ms) | p99 (ms) | mean (ms) | stdev (ms) | status |",
        "|-------|-------|---|----------|----------|----------|-----------|------------|--------|",
    ]
    for label, s in sorted(stats.items(), key=lambda x: getattr(x[1], "phase", "") + x[0]):
        if not isinstance(s, SpanStats):
            continue
        status_str = ", ".join(f"{k}={v}" for k, v in s.status_counts.items())
        lines.append(
            f"| {s.label} | {s.phase} | {s.n} | {s.p50:.1f} | {s.p95:.1f} | "
            f"{s.p99:.1f} | {s.mean:.1f} | {s.stdev:.1f} | {status_str} |"
        )
    return "\n".join(lines) + "\n"


def format_chunk_benchmark_markdown(payload: dict) -> str:
    """Render Phase 1 or Phase 2 results as markdown tables."""
    lines: list[str] = []

    if payload.get("phase") == 1:
        lines.append("## Phase 1 — Chunk Strategy Comparison\n")
        lines.append("| Method | Preset | Scoped Recall@10 | Unscoped Recall@10 | Combined | Top-2 |")
        lines.append("|--------|--------|------------------|--------------------|----------|-------|")
        all_results = payload.get("all_results")
        if all_results:
            for r in all_results:
                cfg = r["config"]
                sm = r.get("scoped_metrics", {})
                um = r.get("unscoped_metrics", {})
                is_top = "Yes" if r in payload.get("top_2", []) else ""
                lines.append(
                    f"| {cfg['name']} | {cfg['preset']} | "
                    f"{_fmt(sm.get('recall@10'))} | {_fmt(um.get('recall@10'))} | "
                    f"{_fmt(r.get('combined_recall'))} | {is_top} |"
                )
        else:
            # Single-config run
            cfg = payload.get("config", {})
            sm = payload.get("scoped_metrics", {})
            um = payload.get("unscoped_metrics", {})
            lines.append(
                f"| {cfg.get('name', '—')} | {cfg.get('preset', '—')} | "
                f"{_fmt(sm.get('recall@10'))} | {_fmt(um.get('recall@10'))} | "
                f"{_fmt(payload.get('combined_recall'))} | — |"
            )
        lines.append("")

    elif payload.get("phase") == 2:
        lines.append("## Phase 2 — Retrieval Grid Search\n")
        lines.append(
            "| Chunk | Preset | Provider | Rerank | Scoped Rec@10 | Unscoped Rec@10 | File Cov | NDCG | Weighted Score |"
        )
        lines.append(
            "|-------|--------|----------|--------|---------------|-----------------|----------|------|----------------|"
        )
        for r in payload.get("all_results", []):
            cfg = r["config"]
            sm = r.get("scoped_metrics", {})
            um = r.get("unscoped_metrics", {})
            lines.append(
                f"| {cfg['name']} | {cfg['preset']} | {r['provider']} | {r['reranker'] or 'off'} | "
                f"{_fmt(sm.get('recall@10'))} | {_fmt(um.get('recall@10'))} | "
                f"{_fmt(um.get('file_coverage'))} | {_fmt(um.get('ndcg@10'))} | {_fmt(r.get('weighted_score'))} |"
            )
        lines.append("")
        rec = payload.get("recommendation")
        if rec:
            lines.append(
                f"**Recommendation**: {rec['config']['name']} {rec['config']['preset']} + "
                f"{rec['provider']} + rerank={rec['reranker'] or 'off'} "
                f"(weighted_score={_fmt(rec.get('weighted_score'))})\n"
            )

    return "\n".join(lines)


def _fmt(val: float | None) -> str:
    return f"{val:.3f}" if val is not None else "—"


def format_rag_quality_markdown(rag_quality: dict) -> str:
    """Render RAG quality results as markdown tables."""
    lines: list[str] = []

    retrieval = rag_quality.get("retrieval", {})
    if retrieval:
        lines.append("## RAG Quality — Retrieval\n")
        lines.append("| strategy | recall | mrr | ndcg |")
        lines.append("|----------|--------|-----|------|")
        stats = retrieval.get("stats", {})
        for strategy in ["semantic-only@5", "semantic-only@10", "hybrid@10", "hybrid+rerank@10"]:
            s = stats.get(strategy, {})
            lines.append(
                f"| {strategy} | {_fmt(s.get('recall'))} | {_fmt(s.get('mrr'))} | {_fmt(s.get('ndcg'))} |"
            )
        lines.append("")

    answer = rag_quality.get("answer", {})
    if answer:
        lines.append("## RAG Quality — Answer\n")
        lines.append(
            "| faithfulness | answer_rel | ctx_prec | ctx_recall | rouge_l | ans_sim | parse_fail |"
        )
        lines.append(
            "|--------------|-----------|----------|------------|---------|---------|------------|"
        )
        stats = answer.get("stats", {})
        lines.append(
            f"| {_fmt(stats.get('faithfulness'))} | {_fmt(stats.get('answer_relevance'))} | "
            f"{_fmt(stats.get('context_precision'))} | {_fmt(stats.get('context_recall'))} | "
            f"{_fmt(stats.get('rouge_l_f1'))} | {_fmt(stats.get('answer_similarity'))} | "
            f"{stats.get('parse_failures', 0)} |"
        )
        lines.append("")

        rows = answer.get("rows", [])
        if rows:
            lines.append("<details>\n<summary>Per-query breakdown</summary>\n")
            lines.append(
                "| query_id | faithfulness | answer_rel | ctx_prec | ctx_recall | rouge_l | ans_sim |"
            )
            lines.append(
                "|----------|--------------|-----------|----------|------------|---------|---------|"
            )
            for row in rows:
                lines.append(
                    f"| {row.get('query_id', '')} | {_fmt(row.get('faithfulness'))} | "
                    f"{_fmt(row.get('answer_relevance'))} | {_fmt(row.get('context_precision'))} | "
                    f"{_fmt(row.get('context_recall'))} | {_fmt(row.get('rouge_l_f1'))} | "
                    f"{_fmt(row.get('answer_similarity'))} |"
                )
            lines.append("</details>\n")

    return "\n".join(lines)
