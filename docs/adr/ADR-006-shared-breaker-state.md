# ADR-006: Shared Breaker / Rate-Limiter State for Multi-Worker Deployments

**Status:** Proposed
**Date:** 2026-05-06
**Context:** C-5 / N-C4 audit findings

## Context

The application uses process-local state for circuit breaker (`traffic_control.py`), token bucket rate limiter (`slowapi`), and extraction dedup. When deployed with `UVICORN_WORKERS > 1`, each worker maintains independent state:

- **Circuit breaker**: A failure in worker 1 does not trip the breaker in worker 2, allowing cascading failures.
- **Rate limiter**: N workers each enforce the configured limit independently, so the effective limit is N×configured.
- **Extraction dedup**: Duplicate extractions when the same request hits different workers.

Currently, `_check_workers()` raises `SystemExit(1)` in production when `workers > 1` (fail-closed). This is correct but prevents horizontal scaling.

## Decision

### Short-term (current): Fail-closed

Block multi-worker in production (`SystemExit`), allow in dev with warning. This is already implemented.

### Medium-term: SQLite-backed shared state

Migrate breaker state to the existing SQLite database via `get_write_connection()`:

- A `breaker_state` table stores `(name, state, failure_count, last_failure_ts, opened_at)`.
- Each worker reads state before deciding to allow/reject. Writes go through the serialized write lock.
- Latency impact: one SQLite read per LLM call (~0.1ms from WAL cache). Acceptable given LLM calls take 1-30s.
- Rate limiter: keep slowapi per-process but add a SQLite-based sliding window counter for distributed enforcement.

### Long-term: Redis-backed state

When deployment scale demands it (multiple pods, Kubernetes HPA):

- Replace SQLite breaker state with Redis `INCR` + `EXPIRE` for atomic counter updates.
- Use Redis for rate limiter via `slowapi`'s `storage_uri` config (already supports Redis).
- Gate behind a `SHARED_STATE_BACKEND` env var: `"sqlite"` (default) or `"redis"`.

## Consequences

- **Positive**: Enables multi-worker and multi-pod deployments safely.
- **Negative**: SQLite approach adds ~0.1ms latency per LLM call; Redis adds operational dependency.
- **Risk**: SQLite write serialization could become a bottleneck under very high concurrency (>100 concurrent LLM calls).

## Implementation Notes

1. Create `backend/src/core/breaker_state.py` with `BreakerState` protocol and `SqliteBreakerState` implementation.
2. Modify `traffic_control.py` to accept a `BreakerState` instance instead of using module-level dict.
3. Add migration for `breaker_state` table.
4. Add integration test: two concurrent "workers" (threads) sharing SQLite breaker state.
