# Documentation index

This folder holds **repository-level** documentation: diagrams and product ADRs. Subsystem implementation details live under [`backend/docs/`](../backend/docs/README.md) (Chinese).

## Diagrams (`docs/diagrams/`)

Visual references (Mermaid flowcharts and ASCII overviews).

| Document | Contents |
|----------|----------|
| [`diagrams/architecture.md`](diagrams/architecture.md) | End-to-end system architecture (ASCII) |
| [`diagrams/rag-pipeline.md`](diagrams/rag-pipeline.md) | RAG chat pipeline: routing, retrieval, context, generation |
| [`diagrams/memory-and-kg.md`](diagrams/memory-and-kg.md) | Memory layers, decay scoring, knowledge graph |
| [`diagrams/architecture.mmd`](diagrams/architecture.mmd) | Mermaid source for architecture (where applicable) |

## Technical Documents (`docs/`)

| Document | Contents |
|----------|----------|
| [`chunk_retrieval.md`](chunk_retrieval.md) | Chunk retrieval strategies: scoring, filtering, fair allocation, and reranking |
| [`benchmark_config_guide.md`](benchmark_config_guide.md) | Benchmark configuration and usage guide |
| [`benchmark_construction_flow.md`](benchmark_construction_flow.md) | Benchmark construction flow |
| [`benchmark_implementation_plan.md`](benchmark_implementation_plan.md) | Benchmark implementation plan |
| [`benchmark_test_plan.md`](benchmark_test_plan.md) | Benchmark test plan |

## Architecture Decision Records (`docs/adr/`)

| ADR | Topic |
|-----|--------|
| [ADR-001](adr/ADR-001-migrations.md) | Database migrations |
| [ADR-002](adr/ADR-002-deployment.md) | Deployment |
| [ADR-003](adr/ADR-003-raganything.md) | RAGAnything multimodal |
| [ADR-004](adr/ADR-004-memory-ontology.md) | Memory ontology |
| [ADR-005](adr/ADR-005-mcp-parity.md) | MCP parity |
| [ADR-006](adr/ADR-006-single-instance-deployment.md) | Single-instance deployment |

## Backend ADRs (`backend/docs/adr/`)

Stack-level decisions (numbered `0001`–`0003`): see [backend/docs/adr/](../backend/docs/adr/).

## Chinese-English documentation cross-reference (M42)

| Topic | English (this repo) | Chinese (`backend/docs/`) |
|-------|---------------------|---------------------------|
| Architecture | [`diagrams/architecture.md`](diagrams/architecture.md) | [`architecture.md`](../backend/docs/architecture.md) |
| RAG pipeline | [`diagrams/rag-pipeline.md`](diagrams/rag-pipeline.md) | [`rag.md`](../backend/docs/rag.md) |
| Memory & KG | [`diagrams/memory-and-kg.md`](diagrams/memory-and-kg.md) | [`memory-and-kg.md`](../backend/docs/memory-and-kg.md) |
| Database | — | [`database.md`](../backend/docs/database.md) |
| Configuration | — | [`configuration.md`](../backend/docs/configuration.md) |
| Chain pipeline | — | [`chain-pipeline.md`](../backend/docs/chain-pipeline.md) |
| LLM & traffic | — | [`llm-and-traffic.md`](../backend/docs/llm-and-traffic.md) |
| Ingest pipeline | — | [`ingest-pipeline.md`](../backend/docs/ingest-pipeline.md) |
| Lifespan & ops | — | [`lifespan-and-operations.md`](../backend/docs/lifespan-and-operations.md) |
| API reference | — | [`api-reference.md`](../backend/docs/api-reference.md) |
| Skills | — | [`SKILLS.md`](../backend/docs/SKILLS.md) |
| Chunk retrieval | [`chunk_retrieval.md`](chunk_retrieval.md) | [`rag.md`](../backend/docs/rag.md) |
| Benchmarking | `benchmark_*.md` (4 files) | [`benchmarking.md`](../backend/docs/benchmarking.md) |

## Other entry points

- Root [README.md](../README.md) — quick start, ports, configuration table, **demo video grid**
- [CLAUDE.md](../CLAUDE.md) — agent-oriented codebase map and commands
- [CONTRIBUTING.md](../CONTRIBUTING.md) — branches, PR process, style
- [CHANGELOG.md](../CHANGELOG.md) — release notes
- [SECURITY.md](../SECURITY.md) — vulnerability reporting and triage SLAs

## Demo videos

Walk-throughs are on the project's YouTube channel: <https://www.youtube.com/@lixiuyin>.

| Title | Watch |
|---|---|
| Full Demo (end-to-end) | <https://youtu.be/IuMp47AY_Do> |
| Invoke Skills | <https://youtu.be/YDGAmJN0t0M> |
| Step by Step | <https://youtu.be/76IJ_jyXTMU> |
| Memory & Knowledge Graph | <https://youtu.be/027BUwJe1lE> |
