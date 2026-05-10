# ADR 0001: SQLite over Postgres (Current Phase)

- **Status**: Accepted
- **Date**: 2026-04-14

## Context

The project is currently optimized for local-first deployment and low operational overhead.
Current backend architecture already uses SQLite with WAL mode, write-lock serialization, and
schema migration support. Team size and expected traffic do not yet justify a managed Postgres
operational footprint.

## Decision

Use SQLite as the primary transactional store in this phase, with strict operational controls:

- WAL mode enabled by default
- bounded write concurrency via write lock
- retention and checkpoint tasks in operations lifecycle
- documented backup/restore procedures

## Consequences

### Positive

- simpler deployment and onboarding
- lower infra cost and fewer moving parts
- deterministic local/CI parity

### Negative

- limited horizontal write scalability
- more careful file-level backup and lock handling required
- future migration to Postgres will require data migration planning

## Revisit Criteria

Re-evaluate this decision if any of the following are true:

- sustained write contention affects p95 latency
- single-node file durability constraints become unacceptable
- product requires multi-region write capability
