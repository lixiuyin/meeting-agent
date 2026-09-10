# Meeting Agent Frontend Documentation

`frontend/docs/` is the authoritative documentation area for the React client.
It describes browser-side architecture, API-client behavior, streaming and
WebSocket handling, viewers, security boundaries, build output, and frontend
quality gates. Backend contracts remain in [`../../backend/docs/`](../../backend/docs/README.md);
system-wide behavior remains in [`../../docs/`](../../docs/README.md).

**Last implementation reconciliation:** 2026-09-10. Route, Memory workspace,
API-client, and test descriptions were checked against the current React source
and backend OpenAPI contract. Files under `audits/` remain dated evidence.

## Reading order

1. [`architecture.md`](./architecture.md) — application entry points, routes,
   state ownership, API clients, viewers, authentication boundaries, and build
   behavior.
2. [`testing.md`](./testing.md) — Vitest, Playwright, coverage, CI, and
   frontend regression strategy.
3. [`../../backend/docs/api-reference.md`](../../backend/docs/api-reference.md)
   — REST, SSE, and WebSocket contracts consumed by the client.
4. [`../../docs/diagrams/architecture.md`](../../docs/diagrams/architecture.md)
   — full-stack deployment and service boundaries.

## Frontend source map

| Area               | Source                                                                       | Responsibility                                                                              |
| ------------------ | ---------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| Application shell  | `src/main.tsx`, `src/App.tsx`, `src/components/app/`                         | Providers, theme, health state, routing, and error boundaries                               |
| Pages              | `src/pages/`                                                                 | Home, generation, materials, history, memory, and settings flows                            |
| Memory workspace   | `src/pages/MemoryPage.tsx`, `src/components/memory/`, `src/styles/index.css` | Bounded desktop/mobile layout, virtualized records, review controls, and provenance actions |
| API clients        | `src/api/`                                                                   | Axios REST calls, SSE parsing, WebSocket token/URL helpers, and generated OpenAPI types     |
| Hooks and contexts | `src/hooks/`, `src/contexts/`                                                | Server interaction, cancellation, stream state, viewer state, and session state             |
| Viewers            | `src/components/materials/file-views/`                                       | PDF, slide, image, text, audio, video, page, and timestamp previews                         |
| Chat rendering     | `src/components/home/`, `src/components/history/`                            | Markdown, citations, source metadata, trace panels, and web results                         |
| Localization       | `src/i18n/`                                                                  | English and Chinese message catalogs and locale selection                                   |
| Monitoring         | `src/utils/monitoring.ts`                                                    | Optional Sentry initialization and event scrubbing                                          |
| Browser tests      | `src/**/*.test.ts(x)`, `e2e/`                                                | Unit/component tests and Playwright browser flows                                           |

## Contract ownership

The backend OpenAPI document and Pydantic schemas are authoritative for route,
request, response, error, and event shapes. The frontend may add compatibility
handling, but must not silently reinterpret fields such as `request_id`, source
metadata, stream sequence numbers, or terminal `done`/`error` events. When an
API contract changes, update the backend schema, regenerate or update
`src/api/generated.d.ts`, update the domain client, and add a client regression
test.
