# ADR-003: Keep RAGAnything with explicit consistency reconciliation

## Status
Accepted

## Context
The system can index into both native Chroma and optional RAGAnything. Dual-index writes improve
multimodal retrieval quality, but introduce drift risk when one side succeeds and the other fails.

## Decision
Keep RAGAnything as an optional capability and add explicit consistency controls:

1. `index_state` tracks each file's Chroma/BM25 generation, active configuration fingerprint, manifest counts/checksum, repair status, and optional RAGAnything document ID.
2. Native replacement writes a new physical generation first and removes the old one only after both Chroma and BM25 are durable.
3. Retrieval always applies the authenticated `user_id`; RAGAnything results are accepted only after authoritative ownership lookup in SQLite.
4. `/api/v1/health/index-consistency` reports drift indicators, while readiness rejects pending/failed/config-mismatched native manifests without invoking paid providers.
5. Startup reconciliation verifies actual store metadata and queues durable file repair when a manifest does not match.

## Consequences
1. Operational visibility improves for native and multimodal indexing drift.
2. Repair paths become explicit and auditable.
3. Physical index generations cost some temporary extra storage during replacement.
4. If sustained multimodal drift remains high, this ADR can be superseded by a decommission ADR.
