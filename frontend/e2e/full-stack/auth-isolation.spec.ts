import { expect, test } from "@playwright/test";

/**
 * Auth isolation E2E spec — verifies API-level access control.
 *
 * These tests use Playwright's `request` context to hit the backend API
 * directly (via the Vite proxy). They require:
 *   - API_KEY set to a non-empty value (production-like auth mode)
 *   - An alternate API key to prove the shared-key deployment exposes only
 *     the configured principal
 *
 * Run through `make e2e-auth`, which starts an isolated production-mode
 * backend and injects the test key into the Vite client.
 */

const PRIMARY_KEY = process.env.E2E_API_KEY ?? "e2e-test-key-primary";
const ALT_KEY = process.env.E2E_ALT_API_KEY ?? "e2e-test-key-alt";
const BACKEND_BASE_URL =
  process.env.E2E_BACKEND_BASE_URL ?? process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:8307";
test.use({ extraHTTPHeaders: {} });

test("unauthenticated request to /meetings returns 401", async ({ request }) => {
  const resp = await request.get(`${BACKEND_BASE_URL}/api/v1/meetings`);
  expect(resp.status()).toBe(401);
});

test("wrong API key returns 401", async ({ request }) => {
  const resp = await request.get(`${BACKEND_BASE_URL}/api/v1/meetings`, {
    headers: { "X-API-Key": "invalid-key-that-does-not-match" },
  });
  expect(resp.status()).toBe(401);
});

test("valid API key returns 200", async ({ request }) => {
  const resp = await request.get(`${BACKEND_BASE_URL}/api/v1/meetings`, {
    headers: { "X-API-Key": PRIMARY_KEY },
  });
  expect(resp.ok()).toBeTruthy();
});

test("alternate API key cannot establish another principal", async ({ request }) => {
  const crossResp = await request.get(`${BACKEND_BASE_URL}/api/v1/meetings`, {
    headers: { "X-API-Key": ALT_KEY },
  });
  expect(crossResp.status()).toBe(401);
});

test("alternate API key is rejected before transcript lookup", async ({ request }) => {
  const transcriptResp = await request.get(`${BACKEND_BASE_URL}/api/v1/meetings/1/transcript`, {
    headers: { "X-API-Key": ALT_KEY },
  });
  expect(transcriptResp.status()).toBe(401);
});
