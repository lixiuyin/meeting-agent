# ADR-006: Single-Instance Deployment Constraint

## Status

Accepted

## Context

The meeting-agent backend uses SQLite (WAL mode) for persistence and a local filesystem directory for uploaded files and Chroma vector store. These storage backends are inherently single-writer and local to a single process.

The Helm chart includes `autoscaling.enabled` and `backend.replicaCount` fields that could be misconfigured to run multiple replicas, which would cause:

1. **SQLite WAL corruption** — concurrent writers from different pods on a shared PVC lead to `SQLITE_BUSY` errors and potential data loss.
2. **Upload file splits** — files uploaded to one pod are not visible to others unless PVC access mode is `ReadWriteMany` (not guaranteed on all cloud providers).
3. **Vector store divergence** — Chroma's local directory is not designed for concurrent multi-process access.

## Decision

- Enforce `backend.replicaCount == 1` and `autoscaling.enabled == false` via Helm template validation (`_helpers.tpl`).
- Any attempt to install with `replicaCount > 1` or `autoscaling.enabled: true` will fail at `helm install`/`helm upgrade` time with a descriptive error message.
- The Helm chart's `values.yaml` already defaults both to single-instance values.

## Consequences

- **Cannot horizontally scale** the backend without migrating to distributed storage (PostgreSQL + object storage + managed vector DB).
- Frontend can still scale (stateless, serves static assets).
- If higher availability is needed, use pod-level health checks + fast restart rather than multiple replicas.

## Future Direction

A migration to PostgreSQL + S3-compatible storage + managed vector DB (Qdrant/Weaviate/Milvus) would enable multi-replica deployments. That is tracked as a separate epic and requires its own ADR.
