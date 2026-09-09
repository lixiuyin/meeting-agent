# Service Level Objectives (SLO)

This document defines the initial production SLO targets for Meeting Agent.
Targets should be reviewed every quarter based on real production telemetry.

## Scope

- API service: `backend/src/main.py` (FastAPI)
- Ingestion pipeline: upload + parse/transcribe + index
- Chat pipeline: retrieval + generation + streaming

## SLO Targets

### 1) Chat Latency

- **SLI**: End-to-end latency for `POST /api/v1/chat`
- **Target**: p95 < 3.0s over rolling 30 days
- **Exclusions**: client/network latency outside server boundary

### 2) Chat Availability

- **SLI**: Successful responses for `POST /api/v1/chat` and `POST /api/v1/chat/stream`
- **Target**: >= 99.5% over rolling 30 days
- **Success criteria**: HTTP 2xx and no terminal `error` event in stream

### 3) Ingestion Success Rate

- **SLI**: Percentage of uploaded files that reach `ready` status
- **Target**: >= 99.0% over rolling 30 days
- **Failure criteria**: final status `failed` after retry/recovery path

### 4) API Error Budget

- **Window**: 30 days
- **Budget**: 0.5% failed requests for critical endpoints
- **Critical endpoints**:
  - `POST /api/v1/chat`
  - `POST /api/v1/chat/stream`
  - `POST /api/v1/meetings/upload`
  - `GET /api/v1/health/ready`

## Alerting Thresholds (Initial)

- p95 chat latency > 3.0s for 15 minutes
- availability < 99.5% for 30 minutes
- ingestion failure rate > 2% for 30 minutes
- circuit breaker in `open` state for > 5 minutes

## Measurement Notes

- Use Prometheus metrics exported by `/metrics`
- Use request IDs and trace IDs for incident correlation
- Reconcile SLI calculations with async/streaming endpoint semantics

## Review Process

- Weekly: review SLI trend and burn rate
- Monthly: evaluate whether objectives remain realistic
- Quarterly: adjust targets after architecture or traffic changes

## Completion-aware collection and acceptance

`chat_completion_total{endpoint,outcome}` records final outcomes, including
`stream_error` and `incomplete` under HTTP 200. `chat_completion_duration_seconds`
measures through the final response body, with a bucket at the 3-second target.
The existing HTTP histogram measures response-header latency and must not be used
as streaming completion latency. The completion metric includes queue waiting. Its current request denominator
includes all POSTs to the two chat endpoints: HTTP 4xx and 5xx both count as
`http_error`. Interpret validation/authentication traffic accordingly; this is
a complete-response success SLI, not a server-5xx-only availability measure.

Load `monitoring/prometheus/alerts.yaml`; validate it with `promtool check rules`
and `promtool test rules monitoring/prometheus/slo-tests.yaml`. Configure the
existing `X-API-Key` scrape header using a secret file and retain at least 31 days
of Prometheus history. The default job name and scrape interval are
`meeting-agent-backend` and 30 seconds.

From `backend`, run:

```sh
.venv/bin/python -m scripts.slo_report \
  --prometheus-url http://127.0.0.1:9090 \
  --output benchmark-results/chat-slo-30d.json
```

This is a read-only report. Missing data, metrics younger than 30 days, or scrape
coverage below 99.5% produce null scores and a nonzero exit status. A passing chat
report does not certify ingestion, human business quality, or release readiness.
Recording-rule values alone do not establish a full 30-day observation window.
These calculations follow the [Prometheus query function definitions](https://prometheus.io/docs/prometheus/latest/querying/functions/).
