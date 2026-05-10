# ADR 0003: ChromaDB as Vector Store (Current Phase)

- **Status**: Accepted
- **Date**: 2026-04-14

## Context

The product requires local-first vector search with simple deployment and predictable developer
setup. Existing retrieval, indexing, and deletion logic already targets Chroma collection APIs and
deterministic chunk IDs.

## Decision

Keep ChromaDB as the primary vector store in the current phase.

- maintain singleton vector store initialization
- preserve deterministic vector IDs for idempotent indexing
- keep per-file deletion support for multi-file meetings

## Consequences

### Positive

- low operational complexity for local and self-hosted deployments
- fast development iteration with minimal infra dependencies
- aligns with current architecture and tests

### Negative

- fewer distributed scaling options than dedicated vector DB services
- production sharding/replication strategy is limited compared with remote stores

## Revisit Criteria

Re-evaluate if:

- corpus size or QPS exceeds Chroma service limits for target SLOs
- multi-tenant isolation requirements require stronger storage boundaries
- operational requirements mandate managed vector infrastructure
