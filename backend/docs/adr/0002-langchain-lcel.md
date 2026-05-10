# ADR 0002: LangChain LCEL as Pipeline Orchestration

- **Status**: Accepted
- **Date**: 2026-04-14

## Context

The system requires composable retrieval, context assembly, generation, memory sync, and
streaming steps across multiple model providers. The team also needs testable pipeline steps
instead of one monolithic prompt call path.

## Decision

Adopt LangChain LCEL as the orchestration layer for chat and retrieval pipelines:

- use explicit pipeline context/result objects
- split retrieval/context/generation/session logic into step modules
- keep provider-specific logic behind service adapters

## Consequences

### Positive

- clear step boundaries and easier targeted tests
- improved extensibility for reranking, query rewrite, and web augmentation
- simpler streaming integration with event bus model

### Negative

- additional abstraction overhead for simple flows
- dependency surface area increases with LangChain ecosystem updates
- requires consistent conventions to avoid fragmented chain construction

## Revisit Criteria

Revisit if:

- LCEL abstractions materially hurt latency or debuggability
- project scope narrows to a much simpler single-model architecture
