# ADR-007: Circuit-breaker and rate-limit state under multi-worker deployment

**Status:** Partially implemented; production remains single-instance

**Date:** 2026-05-06

## Context

Meeting Agent currently has three different protection states, which cannot be collectively described as a shared circuit breaker:

1. LLM `TrafficController` in `backend/src/services/traffic_control.py` uses in-process
   semaphore, token bucket, `CircuitBreaker` and `ErrorRateTracker`. Each worker has its own state.
2. The generation fallback breaker in `backend/src/services/chain/_fallback.py` uses an in-process fast path and also writes `fallback_breaker_open_until` and `fallback_breaker_failures` to `kv_state`, allowing workers to observe the open state. It is not a complete distributed rate-limiting implementation.
3. The memory vector store and extraction breaker still use in-process caches; `/health/reset-memory-cb` only clears the memory-vector breaker of the current process.

If the production environment runs multiple Uvicorn workers, the effective quota of the first type of rate limiting/circuit breaking will be multiplied by the number of workers.
Provider failure counts are also not shared. To avoid mistaking local state for global state, the current production startup guard rejects multiple workers by default.
Development environments may allow multiple workers but log warnings.

## Decision

### Current deployment boundary

Maintain the single-instance deployment constraint for SQLite + Chroma. Production does not treat `UVICORN_WORKERS > 1` or multiple replicas as a supported high-availability solution. `kv_state` is used only for the implemented fallback breaker and cross-worker coordination keys; it does not change this boundary.

### Implemented shared parts

- Database migration v48 creates `kv_state(key, value, updated_at)`;
- fallback breaker synchronizes failure count and open deadline with `kv_state`;
- Other traffic controller, memory vector and extraction states continue to use in-process locks/cache;
- Check worker configuration at startup and fail closed when production requests multiple workers.

### Follow-up expansion direction

If you need multiple pods or Kubernetes HPA in the future, you should first introduce an explicit shared state backend before relaxing deployment restrictions:

- Redis `INCR`/`EXPIRE` or equivalent atomic operations are used for breaker and sliding-window rate limiting;
- Switch `slowapi` from in-process storage to shared storage;
- Define cross-instance semantics for extraction deduplication, rebuild lock, WebSocket ownership and index coordination;
- Add dual-worker/multi-pod integration tests to verify failure counts, quotas, recovery, and fault isolation.

Do not add or document a shared `breaker_state` implementation until these conditions are met, and do not claim that all circuit breakers are shared across workers.

## Consequences

### Positive

- Current deployment model is consistent with SQLite, WAL, Chroma singleton and WebSocket in-process connection management;
- fallback breaker has gained limited cross-worker visibility;
- Operators can distinguish process-local traffic-controller state from the limited fallback-breaker state recorded in logs and `kv_state`.

### Negative

- Safe horizontal scaling cannot be achieved by simply adding workers or replicas;
- `/api/v1/health/traffic` displays the token, in-flight and traffic breaker of the current process, not the total number of clusters;
- The introduction of Redis requires additional availability, network, lease and data consistency operation and maintenance costs.

## Related implementation

- `backend/src/api/lifespan/__init__.py`: production worker guard;
- `backend/src/services/traffic_control.py`: In-process LLM traffic controller;
- `backend/src/services/chain/_fallback.py`: fallback breaker with `kv_state` coordination;
- `backend/src/services/memory/_vectorstore.py`: memory vector breaker;
- `backend/src/core/database/_migrations_features.py`: `kv_state` table migration;
- [`ADR-006-single-instance-deployment.md`](./ADR-006-single-instance-deployment.md): SQLite single instance constraint.
