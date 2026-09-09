# Engineering answer snapshots

The ten JSON references were reviewed by Codex on 2026-09-07 against the existing
handwritten `tests/fixtures/benchmark/golden_set.json`. They preserve a real model
answer and stable `file:chunk` source identities. Per-file metadata binds the
reference to its source run and gold fingerprint. This is an engineering review,
not human business-domain validation or a release approval.

The review record is
`benchmark-results/allfix-20260907/snapshot-baseline-review.json`. Answers were
accepted for factual content and expected-file presence. The q007 list-level
citation layout remains a recorded limitation of the reference.

Run the protected benchmark CLI from `backend`:

```sh
.venv/bin/python ../scripts/run-protected.py -- .venv/bin/python -m scripts.benchmark rag-snapshot
```

Comparison has two layers. Stable source identities and required numeric/core
claims are release gates; citation numbering, bullets, month abbreviations,
common inflections and a constrained set of approved CJK claim aliases are
normalized by the deterministic semantic comparator. The benchmark calls the
full `hybrid` retrieval profile so latency fallback excerpts cannot become a
quality baseline.
Changed wording remains in `literal_diffs` as a diagnostic and no longer fails a
snapshot by itself. Missing references, missing source identities and semantic
claim changes still fail the command. Creating these references is not itself
evidence that an independent later run passed. Do not use `--update-snapshots`
to hide a failed comparison.
