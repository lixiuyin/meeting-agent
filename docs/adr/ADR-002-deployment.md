# ADR-002: SQLite deployment stays single-replica

## Status
Accepted

## Context
The backend uses SQLite with a single writable database file and PVC semantics that are not suitable
for multi-replica writes in Kubernetes.

Previous chart templates included HPA/PDB resources that suggested horizontal scaling, which conflicts
with SQLite constraints and can lead to lock contention and data corruption risks.

## Decision
Deploy backend as a single replica (`replicaCount: 1`) and remove HPA/PDB templates from the chart.
Secrets are injected via Kubernetes Secret references (`envFrom.secretRef`) instead of inline values.

## Consequences
1. Helm deployments are explicit about single-replica operation.
2. Chart users must provide a pre-created Secret (`backend.secretName`).
3. Horizontal scaling requires a future migration to a networked database (e.g. Postgres + pgvector).
