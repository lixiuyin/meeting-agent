/**
 * E2E: Abort SSE stream mid-response and verify no server-side resource leak.
 *
 * This spec complements stream-abort.spec.ts by testing rapid connect/disconnect
 * cycles and verifying the server remains healthy after multiple aborted streams.
 *
 * Prerequisites: Docker stack running at localhost:8307.
 * Run: npx playwright test stream-abort-mid.spec.ts
 */
import { test, expect } from "@playwright/test";

const BASE = "http://localhost:8307";

test.describe("Stream Abort Mid-Response", () => {
  const headers = {
    "X-API-Key": process.env.VITE_API_KEY || "",
    "Content-Type": "application/json",
  };

  test("rapid abort cycles do not degrade server health", async ({ request }) => {
    // Send 5 rapid stream requests, aborting each quickly
    for (let i = 0; i < 5; i++) {
      const controller = new AbortController();
      const streamPromise = fetch(`${BASE}/api/v1/chat/stream`, {
        method: "POST",
        headers,
        body: JSON.stringify({ question: `Quick question ${i}` }),
        signal: controller.signal,
      });

      setTimeout(() => controller.abort(), 200);

      try {
        await streamPromise;
      } catch {
        // Expected — request was aborted
      }
    }

    // After all abort cycles, server should still respond to health check
    await new Promise((resolve) => setTimeout(resolve, 1000));
    const healthResp = await request.get(`${BASE}/api/v1/health`, { headers });
    expect(healthResp.status()).toBe(200);
  });
});
