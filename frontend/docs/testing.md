# Frontend Testing and Quality Gates

The frontend uses Vitest/jsdom for unit and component tests, Playwright for
browser-level validation, and Stryker for mutation testing. Unit and mocked
browser tests must be deterministic and provider-independent. The isolated
full-stack acceptance suite intentionally exercises configured live providers
without touching the developer or production database.

The supported development and CI range is Node 22–24
(`frontend/package.json`: `>=22 <26`, with `.node-version` selecting Node 24).
The OpenAPI-generation CI job and the current frontend Docker build stage still
use Node 20; those are known tooling/build-path mismatches and do not expand the
supported application range. Keep the Node 22/24 matrix authoritative when
interpreting a passing container build.

## Commands

Run from `frontend/`:

```bash
npm ci
npm run type-check
npm run lint
npm run format:check
npm run test:run
npm run test:run -- --coverage
npm run build
npm run e2e
npm run e2e:full-stack
npm run mutation
```

`npm run e2e` builds the application and starts Vite preview on
`http://localhost:4173` unless `PLAYWRIGHT_SKIP_WEBSERVER=1` is set. Use
`PLAYWRIGHT_BASE_URL` to target an already running preview. CI runs Chromium,
records a trace on the first retry, and uses one worker when `CI` is set.

The default E2E command excludes `e2e/full-stack/` and uses Playwright's local
preview configuration. `npm run e2e:full-stack` invokes the repository's
isolated launcher itself: it starts temporary backend/frontend processes on
dedicated ports and can upload/delete only within the validated temporary data
directory. It does not require a pre-started Compose stack.

From the repository root, `make e2e-full-stack` provides the same isolation for
the complete full-stack directory: it creates a temporary
database, upload directory, vector store, and skill directory; starts backend
and frontend services on dedicated ports; runs all functional and resilience
flows serially in Chromium; then stops the services and removes the temporary
state. `make e2e-auth` separately starts production-mode API-key authentication
and verifies unauthenticated, invalid-key, and valid-key behavior.

## Test layers

### Unit and component tests

Tests live beside source files under `src/` and use the jsdom setup in
`src/test/setup.ts`. Cover:

- API error-envelope conversion, request IDs, cancellation, timeout tiers, and
  network-versus-abort behavior;
- SSE parsing for `step`, `token`, `sources`, `status`, `web_results`, `trace`,
  `heartbeat`, unknown events, malformed payloads, and `done`/`error`;
- WebSocket URL/token handling, pings, reconnect backoff, permanent close
  codes, client-ID stability, and maximum retry limits;
- route rendering, loading/empty/error states, mobile layout branches, and
  session or memory cache invalidation;
- source citation metadata, page/slide/timestamp viewers, expired tokens, and
  missing assets;
- Markdown sanitization, safe URL handling, Sentry scrubbing, and failure
  boundaries.

Tests must not assert that tokens arrive at word boundaries. The backend may
emit arbitrary fragments and assigns a monotonic `seq` value for event
ordering. Unknown stream event types must be ignored safely so the protocol can
evolve.

### Browser end-to-end tests

Playwright flows should validate the rendered application, not just HTTP status:

1. open the SPA and verify deep-link fallback and health state;
2. upload a fixture and observe processing refresh/progress behavior;
3. submit a question and verify streaming text, sources, trace, and terminal
   state;
4. open a citation in the correct PDF, slide, image, text, or media viewer;
5. exercise session history, memory, settings, and responsive layouts; for
   full-height workspaces, assert the list bottom stays inside the card and the
   document does not gain unintended overflow;
6. verify failed uploads, expired tokens, API errors, reconnects, and retry UI.
7. verify production-mode API-key rejection and authenticated access.

Use deterministic backend fixtures or a controlled test service. A green
Playwright run with an unavailable provider is not evidence that real ASR,
OCR, LLM, or web-search integrations work.

## Coverage and artifacts

Vitest has a 50% line-coverage floor configured in `vite.config.ts`; mutation
testing independently fails below a 65% mutation score for the two critical
state-management hooks selected in `stryker.conf.json`.
Coverage should be read together with contract and E2E results: high line
coverage does not prove the browser layout, network protocol, or deployment
proxy works. Preserve Playwright traces, screenshots, and videos on failures;
preserve Vitest coverage and mutation reports for quality investigations.

## Change checklist

- [ ] Update the relevant domain client and `src/api/generated.d.ts` when the
      OpenAPI contract changes.
- [ ] Add tests for success, loading, empty, cancellation, error, and retry
      states when adding a network-backed component.
- [ ] Add SSE/WebSocket tests for ordering, unknown events, disconnects, and
      terminal events when changing streaming behavior.
- [ ] Run type-check, lint, format-check, unit tests, build, and affected E2E
      flows before handoff.
- [ ] Confirm that no production API key, file token, or PII is compiled into
      the public bundle or logged by the browser.
- [ ] For layout fixes, assert element geometry at the reported viewport and
      visually inspect the production build; a jsdom test alone is insufficient.
