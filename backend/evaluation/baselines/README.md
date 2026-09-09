# Archived evaluation baselines

`current.json` is created only by an explicit `--update-baseline` run over versioned synthetic fixtures. It is intentionally tracked separately from transient reports in `backend/benchmark-results/`.

Do not archive outputs produced from user uploads. A valid baseline records the protocol/dataset fingerprints, non-secret runtime configuration, raw per-case results, aggregate metrics, timestamp, and source revision.

A tracked baseline is a regression reference, not proof that the same model or
provider route still produces those results. Public comparisons must rerun the
current harness and disclose model, route, sample count, configuration, gates,
and evaluated/skipped metrics. See
[`backend/docs/benchmarking.md`](../../docs/benchmarking.md#publishing-benchmark-and-model-results).
