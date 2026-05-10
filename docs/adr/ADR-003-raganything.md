# ADR-003: Keep RAGAnything with explicit consistency reconciliation

## Status
Accepted

## Context
The system can index into both native Chroma and optional RAGAnything. Dual-index writes improve
multimodal retrieval quality, but introduce drift risk when one side succeeds and the other fails.

## Decision
Keep RAGAnything as an optional capability and add explicit consistency controls:
1. `index_state` table tracks per-file indexing state for Chroma and RAGAnything.
2. Health endpoint `/api/v1/health/index-consistency` reports drift indicators.
3. Startup and periodic reconciliation jobs backfill/normalize `index_state`.

## Consequences
1. Operational visibility improves for multimodal indexing drift.
2. Repair paths become explicit and auditable.
3. If sustained drift remains high, this ADR can be superseded by a decommission ADR.
