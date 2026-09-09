# Frontend Architecture and Runtime Behavior

The frontend in `frontend/` is a React + TypeScript single-page application. It calls the backend through REST, SSE, and WebSocket protocols and never accesses SQLite, Chroma, or the upload directory directly. The backend contract is documented in [`backend/docs/api-reference.md`](../../backend/docs/api-reference.md).

## 1. Stack and entry points

- React 19, Vite 6, TypeScript, and React Router 7.
- Ant Design 6 for components and theming; Framer Motion for selected transitions.
- Axios for REST; `client-chat.ts` implements POST streaming with `fetch` and an SSE frame parser.
- `react-markdown` + `rehype-sanitize` + remark GFM/math for answers; `react-pdf` for PDF; `react-virtuoso` for long lists; Sentry for error monitoring.
- Vitest/jsdom for unit tests, Playwright for end-to-end tests, and Stryker for mutation testing.

The entry sequence is `src/main.tsx` → `src/App.tsx`. `App.tsx` establishes theme, health state, file tokens, `ViewerProvider`, `ChatProvider`, responsive breakpoints, and lazy routes before rendering `components/app/AppRoutes.tsx`.

## 2. Routes and user flows

| URL | Page | Responsibility |
|---|---|---|
| `/` | Home | Questions, RAG sources, streaming answers, and citation preview |
| `/generate` | Skill generation | Select or invoke a Skill to produce structured output |
| `/materials` | Materials | Create meetings, upload files, inspect processing, and view multimodal content |
| `/history` | History | Browse sessions, messages, summaries, and historical search |
| `/memory` | Memory | Projects, governed memory, typed facts/tasks, state changes, meeting review, entities/relations, and past summaries in a bounded workspace |
| `/settings` | Settings | Provider, RAG, memory, upload, and account settings |
| `*` | 404 | Unmatched routes |

```text
page action
  → domain hook/context
  → frontend/src/api/client-*.ts
  → Axios REST or fetch SSE / WebSocket
  → backend /api/v1
  → state update, cache invalidation, notification, viewer refresh
```

Meeting upload state is driven by REST refresh/polling and WebSocket progress/complete events. The chat page incrementally consumes SSE token, source, status, trace, error, and done events. A degraded status is rendered as a localized warning beside the answer, while answer tokens remain user content. Token fragments are not guaranteed to align with words or sentences.

### Memory workspace

The desktop tab bar and the narrow-screen selector expose the same seven
views. `memoryTab` in the URL is the stable top-level selection; project views
also preserve project and subtab state in the query string.

| View | Frontend component | User-visible purpose |
|---|---|---|
| Projects | `ProjectWorkspace` | Project directory, linked materials, meeting preparation, facts, changes, and review scoped to one project |
| Memories | `MemoryList` + `useMemoryActions` | Personal/project/reference libraries; search, filters, CRUD, import/export, feedback, decay, lifecycle, revisions, and vector repair |
| Decisions & tasks | `RecordedFactsPanel` | Deterministic decision/action/project-fact queries, including owner/status/deadline and bitemporal filters |
| State changes | `FactChangesPanel` | Compare authoritative fact state between time boundaries |
| Meeting review | `MeetingReviewPanel` | Review candidates and conflicts with evidence links and revision-safe confirm/retract/edit actions |
| Entities | `EntityGraph` | Browse relations, select/delete entities, and merge aliases |
| Past Sessions | `SessionSummariesTab` | Browse episodic summaries used for cross-session context |

The Memories selector has `personal`, `reference`, and `all` libraries.
Reference material is visible for governance but does not become a personal or
project fact merely because it appears in this list. UI status labels never
override backend lifecycle or evidence-admission rules.

#### Layout contract

`MemoryPage.tsx` owns the page card and tab height. The Memories tab wraps the
library selector and `MemoryList` in `.memory-memories-panel`, a single
full-height flex column. The selector is fixed-height, `MemoryList` receives
only the remaining height, and `.memory-list-scroll-region` is the sole
desktop scrolling surface for virtualized records. Long keys, values, and
evidence may wrap, but they must not enlarge the page beyond the card.

At widths up to 768 px the page returns to document flow and uses an explicit
bounded list height. Changes to selectors, toolbars, tab wrappers, Ant Design
containers, or `Virtuoso` must preserve `min-height: 0` along the desktop flex
chain and be verified at both desktop and mobile breakpoints.

## 3. Configuration and development server

The API client uses `VITE_API_BASE_URL`, defaulting to `/api/v1`. Vite proxies `/api` and WebSocket traffic to `http://localhost:7008`, so the browser normally uses same-origin `http://localhost:8307`.

| Variable | Purpose |
|---|---|
| `VITE_API_BASE_URL` | REST/SSE base path or full URL; default `/api/v1` |
| `VITE_API_KEY` | Injected only for local DEV builds; production builds must not depend on it |
| `VITE_TIMEOUT_READ_MS` | Ordinary read timeout |
| `VITE_TIMEOUT_CHAT_MS` | Synchronous/streaming chat timeout |
| `VITE_TIMEOUT_UPLOAD_MS` | Upload timeout |
| `VITE_SENTRY_DSN` and related variables | Sentry configuration; see `src/utils/monitoring.ts` |

Axios defaults are approximately 30 seconds for ordinary reads, 120 seconds for chat, and 600 seconds for uploads. Each request installs an AbortController; unmount, navigation, or explicit cancellation should abort it so stale responses cannot overwrite new state.

Production authentication should be provided by a reverse proxy, same-origin session, or secure header injection. Do not compile a long-lived API key into a public JavaScript bundle or put it in file/media URLs. Media elements use short-lived backend tokens.

## 4. API client layers

`src/api/` splits clients by domain. The shared core provides:

- base URL, Axios instance, timeout, and request cancellation;
- request/response interceptors;
- conversion of backend error envelopes into `ApiError` (`status`, `code`, `requestId`, `details`);
- default headers and distinction between network and cancellation errors.

The chat client handles `sendChat`, retrieval-only search, SSE parsing, and source metadata. Source text uses `content`; multimodal paths and thumbnails load through backend asset endpoints. Callers should preserve `request_id` and provide actionable handling for `401`, `409`, `429`, and `5xx` responses.

The WebSocket hook connects to `/api/v1/ws` and is designed to use the short-lived token from `POST /ws/token`. It responds to server pings and reconnects with jittered exponential backoff (approximately 1–10 seconds, up to 60 attempts); permanent close codes should not reconnect forever. Each connection needs a stable, valid, unique `client_id`.

## 5. Viewers and security boundary

`ViewerProvider` owns the active citation/file view. The Materials page selects a PDF, slide, audio/video, image, or text viewer by file type. Citation previews use source page, slide, timestamp, and image-path metadata. Every viewer must handle loading failure, processing state, expired token, and missing resource.

Answer Markdown is sanitized with `rehype-sanitize`; Markdown cannot execute scripts or inject arbitrary HTML. The frontend also cleans internal tokens, dangerous URLs, and secrets/PII from Sentry events; CSP styles use a nonce. Never decode or log complete API keys or file tokens in the browser.

## 6. Build output and performance

Vite splits Ant Design, motion, Markdown, PDF, virtualization, vendor, and Sentry code into manual chunks. Pages and heavy viewers use lazy imports. After changing shared dependencies, routes, or viewers, inspect initial and lazy chunks, not only TypeScript compilation.

```bash
cd frontend
npm install
npm run dev
npm run type-check
npm run lint
npm run test
npm run build
npm run preview
```

`npm run format` uses the project formatter. The default `npm run e2e` suite
builds and serves the frontend preview and uses deterministic API mocks; the
separate `npm run e2e:full-stack` launcher starts an isolated real backend.
See [`testing.md`](./testing.md) for frontend CI, browser coverage, and mutation
testing.

## 7. Frontend change checklist

When adding an API field, update the domain client, TypeScript type, loading/error/empty states, and mocks. New stream events must be forward-compatible with unknown events and cover disconnects, duplicate sequence numbers, and error-before-done behavior. Route changes require checks for lazy loading, mobile layout, browser back/refresh, and deep-link fallback. Full-height workspace changes also require geometry checks that the inner scroll surface remains inside its card. Source/viewer changes require PDF, slide, audio/video, image, and missing-resource cases.
