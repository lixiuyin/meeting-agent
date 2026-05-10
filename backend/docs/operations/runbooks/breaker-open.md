# Runbook: Circuit Breaker Stuck Open

## Symptom

- `GET /api/v1/health/traffic` shows breaker state `open`
- chat endpoints return elevated 429/503 or fast-fail responses

## Immediate Checks

1. Verify upstream LLM provider health and credentials.
2. Check recent error-rate spikes in logs and metrics.
3. Confirm rate-limit and concurrency settings are not overly strict.

## Mitigation Steps

1. Reduce request load (temporary rate-limit tightening).
2. Validate provider key rotation or endpoint outages.
3. If provider recovered, trigger a low-rate synthetic probe to allow half-open recovery.

## Recovery Validation

- breaker transitions `open -> half_open -> closed`
- error rate returns below configured threshold
- p95 chat latency returns to normal range

## Escalation

- Escalate if breaker remains open for > 15 minutes after provider recovery.
