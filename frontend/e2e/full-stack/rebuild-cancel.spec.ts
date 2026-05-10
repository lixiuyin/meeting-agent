/**
 * E2E: Trigger vector rebuild then cancel, verifying idempotency and state recovery.
 *
 * Prerequisites: Docker stack running at localhost:8307.
 * Run: npx playwright test rebuild-cancel.spec.ts
 */
import { test, expect } from "@playwright/test";

const BASE = "http://localhost:8307";

test.describe("Vector Rebuild Concurrency", () => {
  test("concurrent rebuild requests return 409 conflict", async ({ request }) => {
    const headers = { "X-API-Key": process.env.VITE_API_KEY || "" };

    // Trigger first rebuild
    const firstResp = await request.post(`${BASE}/api/v1/settings/rebuild-vectors`, {
      headers,
    });
    expect(firstResp.status()).toBe(200);

    // Second rebuild while first is running should get 409
    const secondResp = await request.post(`${BASE}/api/v1/settings/rebuild-vectors`, {
      headers,
    });
    expect(secondResp.status()).toBe(409);
  });
});
