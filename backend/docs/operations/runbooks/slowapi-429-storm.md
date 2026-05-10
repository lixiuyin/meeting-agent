# Runbook: 429 Storm from Rate Limiter

## Symptom

- sudden spike of HTTP 429 responses
- healthy upstream services but request success ratio drops
- response body follows the unified `ErrorResponse` envelope
  (`code: "HTTP_429"`, `message: "Rate limit exceeded: …"`, `request_id`,
  legacy `error` / `detail` fields kept for backward compatibility)

## Immediate Checks

1. Confirm whether traffic spike is expected (release, replay, bot activity).
2. Inspect source distribution (single IP, API key, endpoint concentration).
3. Verify current limiter settings and trusted proxy headers.

## Mitigation Steps

1. Temporarily raise or rebalance rate limit for critical endpoints.
2. Block abusive sources if traffic is malicious.
3. If behind reverse proxy, verify `X-Forwarded-For` is correctly propagated.

## Recovery Validation

- 429 rate declines to baseline
- success ratio and p95 latency recover
- no sustained queue/inflight pressure in traffic controller

## Follow-up

- tune per-key and per-IP quotas based on observed production traffic profile
