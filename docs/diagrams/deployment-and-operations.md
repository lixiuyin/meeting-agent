# Deployment and Operations Architecture

This diagram describes the supported deployment boundary and the lifecycle
paths that keep SQLite, Chroma, uploaded assets, and background tasks
consistent. It intentionally distinguishes the local Docker Compose topology
from the single-backend-replica Helm topology.

**Verified against implementation:** 2026-09-10.

## Deployment topology

```mermaid
flowchart TB
    Browser["Browser"]
    MCPClient["Local MCP client"] --> MCPAdapter["FastMCP adapter<br/>stdio by default · loopback HTTP optional"]

    subgraph Compose["Docker Compose — local or single-node deployment"]
        Frontend["frontend<br/>Nginx container :8080<br/>Compose host :8307"]
        Backend["backend<br/>FastAPI :8000<br/>host :7008"]
        Data["bind mount ./data<br/>SQLite · Chroma · uploads"]
        Frontend -->|/api proxy| Backend
        Backend --> Data
    end

    Browser --> Frontend
    MCPAdapter -->|canonical /api/v1| Backend

    subgraph Helm["Helm — Kubernetes deployment"]
        Ingress["TLS ingress"] --> KFrontend["frontend Service"]
        KFrontend --> KBackend["backend Service<br/>replicas must remain 1"]
        KBackend --> PVC["optional ReadWriteOnce PVC<br/>/app/data"]
        Secret["Kubernetes Secret<br/>API_KEY + provider secrets"] --> KBackend
        Config["ConfigMap<br/>non-secret settings"] --> KBackend
        Network["NetworkPolicy"] -. restricts .-> KBackend
    end

    Ops["Prometheus / Loki / optional OTEL"] -. observes .-> Backend
    Ops -. observes .-> KBackend

    classDef client fill:#2563eb,stroke:#1e3a8a,color:#fff
    classDef runtime fill:#0f766e,stroke:#115e59,color:#fff
    classDef data fill:#be185d,stroke:#831843,color:#fff
    classDef security fill:#7c3aed,stroke:#4c1d95,color:#fff
    classDef ops fill:#475569,stroke:#1e293b,color:#fff
    class Browser,MCPClient client
    class MCPAdapter security
    class Frontend,Backend,Ingress,KFrontend,KBackend runtime
    class Data,PVC data
    class Secret,Config,Network security
    class Ops ops
```

### Deployment invariants

- Docker Compose exposes the backend on `127.0.0.1:7008` and publishes the
  frontend's Nginx port `8080` on host port `8307`. The proxy path is
  `/api/` → `backend:8000`.
- The Helm chart rejects `backend.replicaCount > 1` and does not expose an HPA.
  The restriction is intentional: SQLite WAL state, local uploads, Chroma
  persistence, token buckets, circuit breakers, and extraction deduplication
  are not distributed.
- A persistent volume is required if data must survive pod replacement. The
  chart uses `ReadWriteOnce`, so it does not turn the backend into a scalable
  shared-storage service.
- Backend secrets come from a Kubernetes Secret; ordinary settings come from a
  ConfigMap. Production ingress requires TLS unless explicitly disabled for a
  local test behind another terminating proxy.

## Startup and shutdown lifecycle

```mermaid
flowchart TD
    Start([Process starts]) --> Workers{Production workers > 1?}
    Workers -->|yes| Refuse["Fail closed<br/>process-local state is unsafe"]
    Workers -->|no| Migration["Alembic upgrade<br/>dev-only frozen bootstrap fallback"]
    Migration --> Critical["Fail-closed invariants<br/>API key · settings · reranker consistency"]
    Critical --> Capabilities["Best-effort capability pre-warm<br/>LLM · embeddings · vector stores"]
    Capabilities --> Recovery["Best-effort recovery<br/>stale meetings · summaries · vector swaps<br/>pending memory vectors · temp uploads"]
    Recovery --> Jobs["embedded durable-job workers<br/>leases · retries · cancellation · dead-letter"]
    Jobs --> Loops["Supervised loops<br/>stale recovery · BM25 drift · retention<br/>decay · summary backfill · WAL checkpoint"]
    Loops --> Ready["Serve requests"]
    Ready --> Shutdown["SIGTERM / lifespan exit"]
    Shutdown --> StopLoops["cancel background tasks<br/>stop decay and cleanup loops"]
    StopLoops --> Close["close provider clients<br/>flush/close tracing and database resources"]
```

Critical startup failures stop non-development deployments; development may
enter degraded mode for diagnosis. Capability pre-warm and best-effort tasks
record failures without preventing the process from serving. Deployment health probes should
use `/api/v1/health/live` for liveness and `/api/v1/health/ready` for readiness.

## Backup, recovery, and observability

```mermaid
flowchart LR
    SQLite["SQLite WAL database"] --> Backup["backup script<br/>checkpoint + copy db/wal/shm"]
    Chroma["Chroma directory"] --> Backup
    Uploads["data/uploads"] --> Backup
    Backup --> Archive["encrypted, versioned backup"]
    Archive --> Restore["restore runbook<br/>stop writers → restore → migrate → verify"]
    Restore --> Probes["health + index consistency"]

    App["FastAPI + workers"] --> Logs["structured logs<br/>request ID + trace ID"]
    App --> Metrics["Prometheus /metrics"]
    App --> Traces["pipeline trace + optional OpenTelemetry"]
    Logs --> Loki["Loki / Promtail (optional stack)"]
    Metrics --> Prometheus["Prometheus"]
    Traces --> Collector["OTLP collector (optional)"]
```

Backups must include the SQLite database state, the Chroma persistence tree,
and uploaded/derived assets when citations or file previews must remain
recoverable. Restoring only SQLite or only Chroma creates an index/data
mismatch. The detailed procedures are in
[`backend/docs/operations/backup.md`](../../backend/docs/operations/backup.md)
and [`backend/docs/operations/restore.md`](../../backend/docs/operations/restore.md).
