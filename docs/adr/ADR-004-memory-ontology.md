# ADR-004: Stabilize memory extraction ontology and single-pass parsing

## Status
Accepted

## Context
Memory extraction previously mixed parsing, validation, contradiction handling, and persistence in one
flow. That made extraction behavior harder to reason about and increased risk of duplicated logic when
adding new extraction routes.

## Decision
Introduce a dedicated memory extraction helper (`services/memory/_extractor.py`) as the canonical
single-pass parser/validator for LLM fact output:
1. Parse the model response once.
2. Apply schema and support checks once.
3. Emit normalized fact candidates for persistence and contradiction resolution.

## Consequences
1. The extraction pipeline has a clearer boundary between parsing and persistence.
2. Future ontology evolution (fields, categories, TTL policy) is localized.
3. Contradiction handling remains unchanged but now receives normalized candidates.

