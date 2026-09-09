# Operations guide

This is the operator-facing runbook index. It deliberately points to the
backend operations documents for exact procedures and keeps the high-risk
boundaries visible in one place.

## Deployment choices

| Mode | Recommendation | Constraint |
|---|---|---|
| Docker Compose | Default for local and single-node use | Persist `data/` and inject secrets securely |
| Manual process | Development and controlled services | Run backend and frontend separately; manage process restart |
| Helm | Kubernetes packaging | Keep backend at one replica because the default database is SQLite |

The SQLite single-writer constraint is intentional. Read
[`ADR-006-single-instance-deployment.md`](adr/ADR-006-single-instance-deployment.md)
and [`backend/docs/adr/0001-sqlite-over-postgres.md`](../backend/docs/adr/0001-sqlite-over-postgres.md)
before changing replica topology.

## Health and observability

For implementation-level logs, request IDs, pipeline traces, Prometheus metrics, and probe semantics, see
[`backend/docs/observability.md`](../backend/docs/observability.md). For authentication, metrics authentication, and production security boundaries, see
[`backend/docs/security-and-tenancy.md`](../backend/docs/security-and-tenancy.md).

Use probes with distinct purposes:

```bash
curl -sS http://localhost:7008/api/v1/health/live
curl -sS http://localhost:7008/api/v1/health/ready
curl -sS http://localhost:7008/api/v1/health
curl -sS http://localhost:7008/api/v1/health/traffic
curl -sS http://localhost:7008/api/v1/health/index-consistency
```

| Probe | Answers |
|---|---|
| `/health/live` | Is the process alive? |
| `/health/ready` | Can the instance serve requests and required dependencies? |
| `/health` | What do the individual dependency checks report? |
| `/health/traffic` | Is the LLM breaker open, and how much traffic is in flight? |
| `/health/index-consistency` | Are ready files represented in derived indexes? |
| `/metrics` | Prometheus counters/histograms and provider/HTTP telemetry |

The liveness, readiness, health, and traffic probes are intentionally
unauthenticated for load-balancer use. `index-consistency` and `metrics` use
the API/scraper key when authentication is configured; add
`-H "X-API-Key: $MEETING_AGENT_API_KEY"` to those calls in staging/production.

The optional observability stack is started with:

```bash
docker compose -f docker-compose.yaml -f docker-compose.observability.yaml up
```

Prometheus, Grafana, Loki, and Promtail details are in
[`backend/docs/lifespan-and-operations.md`](../backend/docs/lifespan-and-operations.md)
and `monitoring/`.

## Safe maintenance order

For a configuration or index change:

1. Record the current release, configuration snapshot, health output, and backup status.
2. Change one setting group at a time.
3. Determine whether the change requires a vector or multimodal rebuild.
4. Run the rebuild/reprocess while monitoring traffic and disk space.
5. Verify health, index consistency, one scoped retrieval, and one cited answer.
6. Record the result and rollback path.

Runtime settings updates are in-memory unless configuration is explicitly
written through the supported configuration path. A process restart can restore
file/environment defaults. See [`backend/docs/configuration.md`](../backend/docs/configuration.md)
and the settings API reference.

## Backup, restore, and migration

Before migrations or destructive maintenance:

```bash
sqlite3 data/meetings.db ".backup data/meetings.pre-change.db"
sqlite3 data/meetings.pre-change.db "PRAGMA integrity_check;"
```

Then follow the authoritative procedures:

- Backup: [`backend/docs/operations/backup.md`](../backend/docs/operations/backup.md)
- Restore: [`backend/docs/operations/restore.md`](../backend/docs/operations/restore.md)
- Alembic: [`backend/docs/operations/alembic.md`](../backend/docs/operations/alembic.md)
- Database model and legacy migrations: [`backend/docs/database.md`](../backend/docs/database.md)

Do not run ad-hoc schema changes against a live instance. Keep the database,
uploads, and configuration release associated with the same recovery point.

## Incident triage

Start with the request ID, meeting/file/session ID, status, and the first
provider error. Use this order:

1. `health/live` — process issue or restart loop.
2. `health/ready` and `health` — dependency issue.
3. `health/traffic` — rate-limit, breaker, or concurrency issue.
4. `meetings/{id}` — asynchronous ingest state and error message.
5. `health/index-consistency` and `chat/search` — index/recall issue.
6. Structured logs and metrics — correlate timing and provider failures.

Runbooks:

- [`assemblyai-timeout.md`](../backend/docs/operations/runbooks/assemblyai-timeout.md)
- [`breaker-open.md`](../backend/docs/operations/runbooks/breaker-open.md)
- [`chroma-dim-mismatch.md`](../backend/docs/operations/runbooks/chroma-dim-mismatch.md)
- [`slowapi-429-storm.md`](../backend/docs/operations/runbooks/slowapi-429-storm.md)

Do not “fix” missing citations by deleting the database or Chroma directory
before taking a backup and checking index consistency.

## Reindex and recovery commands

Prefer the authenticated API rebuild because it coordinates an atomic shadow
collection swap:

```bash
curl -sS -X POST "$API_BASE/settings/rebuild-vectors" \
  -H "X-API-Key: $MEETING_AGENT_API_KEY"
```

The one-off `backend/scripts/reindex_all_files.py` script performs a full
destructive reindex and re-runs ingestion. Use it only when its documented
purpose matches the incident, after a backup, and in a maintenance window.

## Service objectives and retention

- SLO targets: [`backend/docs/operations/slo.md`](../backend/docs/operations/slo.md)
- SLA commitments: [`backend/docs/operations/sla.md`](../backend/docs/operations/sla.md)
- Retention jobs: [`backend/docs/operations/retention.md`](../backend/docs/operations/retention.md)

These documents describe operational targets; they do not guarantee provider
availability or eliminate the need to define an organization-specific RTO/RPO.
