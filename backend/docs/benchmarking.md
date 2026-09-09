# Benchmarking

The benchmark system separates four evidence levels instead of treating every
green command as the same claim:

1. deterministic CI/regression tests;
2. isolated synthetic diagnostics for functional, latency, retrieval, memory,
   multi-turn, evidence-governance, process, and reranker behavior;
3. a private, principal-scoped production holdout with human-reviewed references
   and independent repeated judging;
4. release acceptance, which binds production quality, business review, SLO,
   security, implementation fingerprints, and an internally verified clean
   repository state.

Local hardware and provider latency vary, so synthetic benchmark scores are
developer diagnostics unless an explicit gate is enabled. They are never a
substitute for the private holdout or the release manifest.

## Prerequisites

- Backend dependencies installed (`uv sync --dev`)
- A working `.env` with LLM and embedding API keys (for chat / RAG answer benchmarks)
- The backend development dependency group installed (`uv sync --group dev`); it
  includes `reportlab` for deterministic fixture regeneration

## Quick start

```bash
cd backend

# Run chat pipeline latency benchmark
uv run python -m scripts.benchmark chat --iterations 5 --order-seed 20260909

# Fail when the declared chat latency/degradation SLO is exceeded
uv run python -m scripts.benchmark chat --iterations 5 --enforce-slo

# Run ingest pipeline latency benchmark
uv run python -m scripts.benchmark ingest --iterations 3

# Run component micro-benchmarks
uv run python -m scripts.benchmark micro

# Compare the configured reranker against the declared candidate ordering
uv run python -m scripts.benchmark reranker-quality

# Run everything
uv run python -m scripts.benchmark all --iterations 5 \
  --process-report benchmark-results/e2e-smoke.json
```

Quality reports contain a separate `evidence_quality` block. `valid=true`
means that the declared benchmark protocol completed correctly; it does not by
itself make the result release-grade. The bundled datasets are synthetic and
therefore report `grade=diagnostic`. A production-quality claim additionally
requires a principal-scoped production holdout, at least 30 accepted cases
across at least 10 meetings, a declared cross-meeting subset, three judge
passes, an independent judge model, and full reranker execution.

The root README publishes scores from exactly one current artifact:
[`docs/validation/latest-benchmark.json`](../../docs/validation/latest-benchmark.json).
It combines the latest protocol, evidence-governance, RAG, Multi-turn, Memory,
reranker, Vision, full-stack, and Chat verification. When a newer complete run
is approved for publication, replace this artifact and its README table instead
of appending another dated “current” table. Raw timestamped reports stay under
the ignored `backend/benchmark-results/` directory; publish only compact
aggregates, input fingerprints, failed suites, and `null` skipped metrics.

## Publishing benchmark and model results

Public benchmark material should make the evidence reproducible without
turning a local diagnostic into a vendor- or model-wide claim.

Publish:

- the capture date/timezone, benchmark command or protocol version, fixture or
  holdout fingerprint, sample count, model identity, provider route, relevant
  reasoning/token/timeout settings, and declared gates;
- completed, failed, retried, degraded, evaluated, and skipped counts;
- aggregate latency/quality metrics and hashes of ignored raw reports;
- negative results and `release_ready=false` whenever any required gate remains
  unmet.

Do not publish:

- API credentials, authorization headers, account identifiers, private prompts
  or answers, real meeting content, or unredacted provider error payloads;
- ignored raw reports or private holdout artifacts merely to make a public table
  look reproducible;
- “model unavailable”, “model failed”, “model A beats model B”, or equivalent
  universal wording when only one account, route, date, or workload was tested.

OpenRouter may route a model across provider endpoints unless a provider is
pinned. Record that routing boundary. Runs with different sample counts,
reasoning settings, timeouts, provider policies, or fixture versions are
diagnostics, not a controlled head-to-head comparison. A safe conclusion is:
“Under this repository's dated workload and configuration, the tested route did
or did not satisfy the project gate.” A model retained after candidate testing
is only the current engineering choice; it is not certified as universally
better or production-ready.

### Private production holdout

`scripts.production_holdout_benchmark` builds and evaluates a private holdout
from the current real corpus. It snapshots SQLite with the backup API, copies
the vector store into temporary storage, and never writes benchmark sessions
to the production database. Holdout manifests, checkpoints, answers, and
reports belong under the ignored `backend/.private-benchmarks/` directory
because they can contain user material.

```bash
uv run python -m scripts.production_holdout_benchmark curate \
  --source-db ../data/meetings.db \
  --user-id <principal-user-id> \
  --output .private-benchmarks/production-holdout.json \
  --curator-model independent/curator-model \
  --required-domain meeting \
  --cases 30 \
  --minimum-domain-cases 30 \
  --minimum-meetings 10 \
  --minimum-cross-meeting-cases 6

uv run python -m scripts.business_review prepare \
  --holdout .private-benchmarks/production-holdout.json \
  --decisions .private-benchmarks/production-holdout-review.csv

# After a domain reviewer completes the CSV:
uv run python -m scripts.business_review validate \
  --holdout .private-benchmarks/production-holdout.json \
  --decisions .private-benchmarks/production-holdout-review.csv

uv run python -m scripts.business_review approve \
  --holdout .private-benchmarks/production-holdout.json \
  --decisions .private-benchmarks/production-holdout-review.csv \
  --output .private-benchmarks/production-holdout-reviewed.json

uv run python -m scripts.production_holdout_benchmark run \
  --source-db ../data/meetings.db \
  --source-vector-dir ../data/vectordb \
  --holdout .private-benchmarks/production-holdout-reviewed.json \
  --output .private-benchmarks/production-rag-result.json \
  --judge-model independent/judge-model \
  --judge-repeats 3 \
  --min-quality-score 0.7 \
  --min-file-recall 0.8 \
  --min-evidence-recall 0.6

uv run python -m scripts.build_production_quality_evidence \
  --report .private-benchmarks/production-rag-result.json \
  --holdout .private-benchmarks/production-holdout-reviewed.json \
  --reviewer "<release-reviewer>" \
  --output ../docs/validation/evidence/production-quality.json
```

When the database has exactly one ready-file owner, `--user-id` may be omitted;
multi-user corpora require it and are filtered before curation and evaluation.
To evaluate both maintained domains, repeat `--required-domain` for `meeting`
and `course_research`, use at least 60 total cases, and retain 30 accepted cases
per domain. The evidence builder refuses invalid, unreviewed, under-sized,
partially reranked, below-threshold, or judge-incomplete reports; it creates a
new public attestation rather than exposing private questions and answers.

The curator creates single-source and cross-meeting questions, withheld
reference answers, supporting chunk IDs, and verbatim evidence quotes. Every
quote and every declared source/meeting identity must resolve before admission.
The selector enforces case count, domain quotas, meeting diversity, and the
cross-meeting minimum before review. Retrieval and answer generation run over
the selected principal's complete corpus; expected file IDs remain
evaluator-only labels and are never used to narrow generation. A separate judge
scores six dimensions three times. Missing/non-finite
metrics and results below the configured quality or file-recall gates make the
report non-release-ready. Interrupted answer generation resumes from a private checkpoint.
File `Hit@10`, distinct-file `Recall@10`, and file MRR are reported separately;
the earlier implementation's boolean hit is no longer mislabeled as recall.
Supporting-chunk Hit/Recall/MRR provide a stricter evidence coverage gate.
Model-curated references remain `candidate_requires_review` until a human has
reviewed them; provider success and exact quote matching are not substitutes
for expert review.

### Original upload to RAG lineage audit

After a production holdout run, the read-only lineage audit joins four distinct
quality boundaries: original-file readability, parser agreement, chunk
integrity, and the paid RAG report. It reads the existing database and uploads
without invoking an LLM or modifying production data.

```bash
uv run python -m scripts.production_pipeline_benchmark \
  --source-db ../data/meetings.db \
  --uploads-dir ../data/uploads \
  --holdout .private-benchmarks/production-holdout.json \
  --rag-result .private-benchmarks/production-rag-result.json \
  --output .private-benchmarks/production-pipeline-result.json
```

The parser metric is deliberately named an agreement proxy. PDFs are compared
with a second text-layer extractor, spreadsheets with a direct cell projection,
and images with local OCR when enough text is recovered. Audio/video and
scanned documents without an independent transcript are `not_evaluated`; the
audit never turns missing ground truth into a perfect parsing score. A true
parsing-accuracy claim requires human-reviewed page text or time-aligned media
transcripts.

Chunk integrity checks every ready file for indexed coverage, empty chunks,
duplicate chunk IDs, required lineage metadata, and survival of the holdout's
verbatim evidence quotes. The final RAG stage accepts an existing paid report
only when its holdout hash and corpus-content hash still match the current
inputs. Stage scores are reported separately rather than averaged into a
meaningless single number. JSON and Markdown reports are written together and
should remain in the ignored `.private-benchmarks/` directory because filenames
and per-file diagnostics can contain private information.

## Subcommands

### `chat`

Ingests fixture documents into a temporary DB + Chroma directory, then runs
every query in `tests/fixtures/benchmark/queries.json` N times through the real
streaming pipeline. A deterministic seed shuffles execution order to reduce
warm-cache/order bias. It records client-observed `chat_ttft`, `chat_total`,
`trace_total`, per-span percentiles, and per-category latency/degradation. The
performance gate checks p95 TTFT, p95 total latency, and degraded-response rate;
`--enforce-slo` makes it blocking.

The 2026-09-09 main-generation diagnostic completed 20/20 requests with
`z-ai/glm-5.3-flash`, but its 45% degraded rate, 4.17 s TTFT p95, and 6.15 s
total p95 failed the project gate. This result is scoped to that synthetic run,
its OpenRouter route and configuration; it is not a general conclusion about
the model. See the latest benchmark artifact linked above.

```bash
uv run python -m scripts.benchmark chat [--iterations N] [--order-seed N] \
  [--max-degraded-rate 0.05] [--max-ttft-p95-ms 3000] \
  [--max-total-p95-ms 5000] [--enforce-slo] \
  [--baseline] [--update-baseline]
```

### `ingest`

For each fixture file, copies it into the temp upload directory, creates a meeting/file record, and calls `process_meeting_file()` N times. It reports `trace_total` and per-stage timing. This command measures processing time only; the full upload → queued job → `ready` SLI belongs to the bounded full-stack smoke test and must not be inferred from this number.

```bash
uv run python -m scripts.benchmark ingest [--iterations N] [--baseline] [--update-baseline]
```

### `micro`

Runs `pytest -m benchmark` for component-level benchmarks:

- `embedder.embed_documents`
- `rag.retrieve` against a pre-seeded 1k-doc Chroma
- `reranker.rerank_documents`
- `chunking` throughput
- `parser` for a small PDF
- `tokenizer` throughput

```bash
uv run python -m scripts.benchmark micro
```

### `reranker-quality`

Runs a controlled 12-candidate challenge set covering semantic paraphrase,
cross-lingual retrieval, and unknown-entity behavior. Baseline and reranked MRR
and nDCG@10 are both retained, with explicit evaluated/skipped counts. A disabled
reranker produces `null` reranked metrics and a declared skip; a configured
reranker that fails to execute invalidates the run. Because the bundled set is
small and synthetic, even a perfect score remains diagnostic and cannot satisfy
the production-quality gate.

```bash
uv run python -m scripts.benchmark reranker-quality
```

### `protocol-audit`

Validates the versioned methodology contract, required metric families, source mapping, dataset path confinement, withheld-information rules, reference-required metrics, and SHA-256 fingerprints without calling an LLM, ASR, embedding, or reranking provider. Its executable exposure/exploit/mislead checks fail closed when one of those contracts is removed. `valid=true` means the protocol is internally valid; `execution_ready=true` means every declared suite has an implemented runner or audit path.

```bash
make eval-audit
```

### `multi-turn`

Runs every versioned conversation in `evaluation/datasets/multi_turn_cases.json`
against an isolated temporary database and vector store. Turns in one case reuse
the same session and are scoped to the case's exact fixture file IDs, while cases
use separate users so undeclared files, history, and memory cannot leak across
examples. Gold answers, answerability labels, and expected evidence
are withheld from generation and supplied only to a separately instantiated
judge. The report archives each answer, sanitized source, process trace,
session-continuity flag, evidence recall, judge diagnostics, and aggregate
faithfulness, appropriateness, naturalness, and completeness. The command exits
non-zero after writing its diagnostic artifact if any turn returns a source
outside the case's declared fixture corpus.

```bash
uv run python -m scripts.benchmark multi-turn \
  [--judge-model independent/model] [--judge-repeats 3]
```

To archive a completed valid report without repeating any LLM, ASR, embedding,
or reranking calls, import the report explicitly:

```bash
uv run python -m scripts.benchmark baseline-import \
  --report benchmark-results/multi-turn_<timestamp>.json
```

The import fails closed for invalid reports, missing statistics, or missing
dataset, harness, and implementation fingerprints. Multi-turn and memory
reports must explicitly declare `valid=true`, so legacy reports produced before
validity checks cannot be imported accidentally. Command baselines are stored
independently: importing a memory report replaces only the memory payload and
preserves the latest RAG or performance payloads. Comparison still fails closed
on dataset, harness, or implementation fingerprint drift for the command being
compared.

### `memory`

Runs versioned long-horizon cases in isolated temporary storage using paired
`memory_on` and `memory_off` conditions with the same reasoner. Selected cases
also run a `distractor_only` control. A second versioned corpus exercises the
production knowledge-graph entity indexing, relation persistence, semantic
lookup, and relation-expanded context under paired `knowledge_graph_on` and
`knowledge_graph_off` conditions. Relevant facts and deterministic unrelated
events are inserted only at test time for the paired recall control; reference answers and expected context
terms remain evaluator-only. The report records retrieved memories and hashes,
answer correctness, retrieval recall, selective forgetting, long-range
understanding, graph context recall, graph-on answer gain, and the measured
memory gain. The dataset also includes explicit-unknown abstention, negated
updates, temporal validity, and same-name meeting disambiguation, with separate
accuracy aggregates. Judge parse failures or an incomplete paired condition matrix
invalidate the run.

The command also replays the same source events through the configured
production combined-extraction path (or `auto_extract_facts` fallback), including
the extraction model, verbatim-evidence validator, deduplication, contradiction
resolution, lifecycle state, and vector
publication. Separate `pipeline_write_recall`, `pipeline_reference_key_agreement`,
`pipeline_latest_value_accuracy`, and `pipeline_confirmed_evidence_rate` metrics
prevent strong seeded-recall
scores from being presented as proof that source facts were extracted correctly.
Schema-v2 `pipeline_cases` declare an `expected_outcome` for every safety event:
`confirmed`, `pending`, `rejected`, `superseded`, or `no_change`. The evaluator
reads the authoritative rows after every event and verifies the lifecycle state;
it does not equate a write with correctness. `pipeline_events_persisted` and
`pipeline_events_correct` are therefore reported separately, and an omitted
`facts_added` result fails closed. For ordinary positive cases, a skipped or
zero-write event remains incorrect rather than receiving implicit credit. A
`superseded` event additionally requires observable retirement of a previously
confirmed value: an explicit superseded/retracted state or a higher in-place
revision that replaces it. A parallel new fact while the old fact remains active
is scored as a failure.
Pipeline write recall counts only confirmed
facts. Latest-value accuracy also requires that no stale earlier value remains
inside any confirmed authoritative value, including malformed rows that contain
both the old and new answer. When a structured `object_value` exists, it is
scored as the current value rather than concatenated with display text. Evidence
credit requires the authoritative value itself, with compatible polarity, in
the evidence excerpt; a merely
non-empty or topically related quote is not sufficient.
The reference-key metric is intentionally named agreement rather than stability:
production keys may be more structured than the evaluator's logical labels. It
is reported as `null` unless a dataset explicitly declares physical keys, so a
logical label mismatch is never presented as a measured zero. The
report includes each stored value, lifecycle state, evidence excerpt, and
conflict link, plus persisted-event and stale-confirmed-value diagnostics, so
low aggregate scores remain diagnosable. Reports produced by older evaluator
logic must be rerun and cannot be used as evidence for the corrected metrics.

```bash
uv run python -m scripts.benchmark memory \
  [--judge-model independent/model] [--judge-repeats 3]
```

To iterate on the production extractor without rerunning the reasoner, judge,
and knowledge-graph suites, run the same source-event replay independently:

```bash
uv run python -m scripts.benchmark memory-pipeline
```

The long-horizon suite is considered implemented only because both the memory
store and a two-relation knowledge-graph path have versioned paired controls.
The graph score is the graph-on answer correctness multiplied by evaluator-only
context-term recall, so a fluent answer cannot hide a failed graph retrieval.

### `process`

Scores a previously captured full-stack smoke report against the versioned
`process_expectations.json` contract. It verifies required ingest/chat spans,
terminal states, and artifacts without calling an external provider.
`step_accuracy` is the share of required spans observed with a successful
terminal status. The command also executes versioned ingest faults for missing
metadata, extraction, indexing, and persistence, plus chat faults for retrieval,
generation, and message persistence. Each case runs in isolated benchmark
storage and compares the recorded first causal span and exception class with
withheld expectations. It never edits the repository `data/` directory or
invokes an external parser, embedder, LLM, or ASR provider for these injected
cases.

```bash
uv run python -m scripts.benchmark process \
  --report benchmark-results/e2e-smoke_<timestamp>.json
```

The process report is `valid` when both success traces and every supplied fault
trace are structurally evaluable. It is `complete` only when the observed fault
case IDs exactly match the versioned dataset; duplicated, unknown, missing, or
tampered expectations fail closed. `first_error_accuracy` scores causal span
attribution and `error_type_accuracy` scores the recorded exception class. The
versioned process suite is implemented for the primary ingest and chat failure
boundaries; adding new production-critical steps requires adding a predeclared
fault case before the suite contract can continue to claim full coverage.

### Isolated full-stack smoke

`make e2e-smoke` starts a temporary backend and frontend on test-only ports,
with the database, uploads, vector store, and logs under a validated operating
system temporary directory. It never mounts or modifies the repository
`data/` directory. The run uploads the versioned `e2e-smoke.txt` fixture,
waits for the durable worker to make it ready, sends one real streaming chat
request through the browser UI, and fails unless it observes the expected
fact, an inline numeric citation, the matching source identity, a process
trace, a terminal event, five green readiness checks, and zero dead-letter
jobs. The artifact joins the structured ingest trace to the upload by exact
`file_id` and preserves the chat answer, sources, session ID, and ordered spans
for the offline process evaluator.

The ignored `benchmark-results/e2e-smoke_<timestamp>.json` artifact records
`upload_to_ready`, browser-observed `chat_ttft`, and `chat_total`, together with
dataset, harness, and full-stack implementation fingerprints. Summary and fact
extraction are disabled only in this smoke environment so the run makes one
answer-generation call; production defaults are unchanged.

```bash
make e2e-smoke
```

### `rag-retrieval`

Evaluates retrieval quality against `golden_set.json` using deterministic chunk
IDs. The generator-visible retrieval corpus is the case's explicitly declared
corpus, or all declared fixtures by default. Expected answer-bearing file IDs
are evaluator-only labels and never become the retrieval scope. Reports:

- `semantic-only@5` / `semantic-only@10` (Recall, MRR, nDCG)
- `hybrid@10`
- `hybrid+rerank@10` only when the production candidate-pool gate actually runs
  the reranker; otherwise these metrics are `null` with skipped counts

```bash
uv run python -m scripts.benchmark rag-retrieval [--top-k 10]
```

### `rag-answer`

Runs the full `ask()` pipeline for each golden item and records the exact judge configuration. `--judge-model` creates a separate judge instance and no longer changes the model used to generate answers. It scores:

- **Faithfulness** — are claims in the answer supported by retrieved context?
- **Answer relevance** — does the answer actually address the question?
- **Context precision** — are the retrieved chunks relevant?
- **Context recall** — do retrieved chunks cover the reference claims? This is reference-required, not reference-free.
- **Correctness** — does the answer semantically match the withheld reference answer?
- **Citation quality** — are inline citations valid, complete, and entailed by their numbered sources?
- **Source identity recall** — did retrieval include every evaluator-declared source file?
- **Corpus isolation** — did retrieval avoid files outside that case's declared source scope?

It also reports lexical `ROUGE-L F1` and embedding similarity when a reference answer exists. Judge scores outside `[0, 1]` fail parsing instead of being silently accepted.

The in-repository evaluator is explicitly versioned as `ragas-aligned-llm-judge`.
It adapts the RAGAS metric contracts to meeting artifacts; it does not claim to
be the upstream `ragas` Python package. Context precision is computed by the
harness as rank-sensitive average precision from the judge's per-chunk
relevance labels, so the aggregate cannot disagree with those labels.

The JSON report retains an auditable record for every synthetic case: query,
withheld reference answer, generated answer, sanitized source metadata and
content hashes, process trace, score, relevant-chunk indices, judge attempt
count, parse retry count, and parse error type. It never archives runtime
meeting/file IDs or upload paths. `judge_parse_failures` counts metrics that
ultimately could not be parsed; `judge_parse_retries` separately exposes
transient malformed judge responses that succeeded on retry.
The run declares `valid=true` only when every declared golden query has exactly
one non-empty answer, retrieved sources, a process trace, all six unit-interval
judge metric scores, an artifact-recomputed source identity/isolation record,
and the requested number of parsed judge diagnostics. Each query runs over its
declared fixture corpus; expected file IDs remain withheld scoring labels. The
validator recomputes observed and unexpected file names from source artifacts
rather than trusting aggregate scores. Invalid or legacy answer reports cannot
be imported into the regression baseline.

Generator and judge always use separate client instances. The report records
whether they nevertheless resolve to the same provider/model; for strong
independence, pass a distinct `--judge-model`. The default independent judge is
`qwen/qwen3.8-flash`; an explicit flag still overrides it.

```bash
uv run python -m scripts.benchmark rag-answer \
  [--judge-model independent/model] [--judge-repeats 3]
```

### `rag-snapshot`

Compares current answers and source chunk sets against committed snapshots in `benchmark-results/rag_snapshots/`. Flags:

- Source set changes
- Answer text divergence

```bash
# Update snapshots after intentional model / prompt changes
uv run python -m scripts.benchmark rag-snapshot --update-snapshots

# Diff against committed snapshots
uv run python -m scripts.benchmark rag-snapshot
```

### `rag-all`

Runs `rag-retrieval`, `rag-answer`, and `rag-snapshot` in one shot.

### `rag-matrix`

Runs retrieval across a config matrix (varying `chunk_size`, `top_k`, `hybrid_alpha`, etc.) to find optimal settings.

```bash
uv run python -m scripts.benchmark rag-matrix
```

### `rag-chunk-phase1`

Phase 1 compares audio chunk strategies (segment-based vs silence-based vs
fixed-size) on a configuration-selection dataset.

### `rag-chunk-phase2`

Phase 2 grid-searches retrieval settings on the top two Phase 1 configurations.

### `rag-chunk-full`

End-to-end Phase 1 + Phase 2 chunk optimization pipeline. These phases select a
configuration and score it on the same development material, so their output is
`tuning_only`, never a final performance estimate. Freeze the selected config
and evaluate it once on a separate untouched holdout before making a quality
claim.

### `all`

Runs every required synthetic/protocol family, including `chat`, `ingest`,
`micro`, `rag-all`, `reranker-quality`, multi-turn, memory, evidence governance,
and process evaluation. Provider-dependent skips remain explicit.

## Baseline and regression detection

The first controlled run should establish a baseline:

```bash
uv run python -m scripts.benchmark chat --update-baseline
```

Subsequent runs can compare against it:

```bash
uv run python -m scripts.benchmark chat --baseline
```

The default regression threshold is **25%** (`BENCH_REGRESSION_THRESHOLD` env var). Latency p95, snapshot diffs, judge parse failures, and judge parse retries use “lower is better”; Recall/MRR/nDCG and answer-quality scores use “higher is better”. The command exits non-zero when a metric regresses, the judge configuration drifts, the requested command has no matching stored payload, or no comparable metrics exist. Missing baselines also fail closed before an expensive run begins.

## Reading the report

Each run writes two transient files to the ignored `backend/benchmark-results/` directory:

- `<name>_<timestamp>.json` — full raw data
- `<name>_<timestamp>.md` — human-readable markdown table

The explicit baseline is stored at `backend/evaluation/baselines/current.json`, while answer snapshots are stored under `backend/evaluation/snapshots/`. Only synthetic-fixture results may be archived there.

Every report records fingerprints for the protocol, datasets, benchmark harness,
and the measured implementation (`src/`, `skills/`, `config/main.yaml`,
`pyproject.toml`, and `uv.lock`). Baseline updates are incremental by command;
they never discard a different command merely because its inputs were captured
at another revision. Comparison is always command-matched and fails closed when
either payload lacks a fingerprint or any fingerprint drifts, so independently
archived payloads cannot be compared as though they came from the same inputs.

## Fixtures

Synthetic fixtures live in `tests/fixtures/benchmark/`:

| File | Purpose |
|------|---------|
| `sample.pdf` | Text-heavy PDF (exercises marker parser path) |
| `scanned.pdf` | Image-only PDF (forces OCR fallback) |
| `sample.pptx` | 3-slide presentation |
| `sample.wav` | 15s sine sweep (exercises transcriber) |
| `queries.json` | Latency query set |
| `golden_set.json` | Hand-written Q/A pairs for RAG eval |

Regenerate fixtures if needed:

```bash
cd tests/fixtures/benchmark
python generate.py
```

## Adding a new micro-benchmark

1. Add a test class in `tests/benchmark/test_micro.py`
2. Mark it with `@pytest.mark.benchmark`
3. Mock any external API (embedder, reranker, LLM) so the test stays fast and free
4. Run with `pytest -m benchmark`

## Adding a new fixture

1. Update `tests/fixtures/benchmark/generate.py`
2. Run the generator
3. Add the fixture file and any associated queries to `queries.json` or `golden_set.json`
4. Commit the generated files
