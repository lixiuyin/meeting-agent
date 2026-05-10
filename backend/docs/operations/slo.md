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
