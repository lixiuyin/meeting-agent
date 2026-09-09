/**
 * E2E: Abort a streaming chat response mid-stream, verifying cleanup.
 *
 * Prerequisites: Docker stack running at localhost:8307.
 * Run: npx playwright test stream-abort.spec.ts
 */
import { deploymentAuthHeaders, test, expect } from "./fixtures";

const BASE = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:8307";

test.describe("Stream Abort", () => {
  test("aborted stream does not block subsequent requests", async ({ request }) => {
    const headers = {
      ...deploymentAuthHeaders(),
      "X-API-Key": process.env.VITE_API_KEY || "",
      "Content-Type": "application/json",
    };

    // Send a chat stream request and abort after first data
    const controller = new AbortController();
    const streamPromise = fetch(`${BASE}/api/v1/chat/stream`, {
      method: "POST",
      headers,
      body: JSON.stringify({ question: "Tell me a long story about everything" }),
      signal: controller.signal,
    });

    // Abort after a short delay
    setTimeout(() => controller.abort(), 500);

    try {
      await streamPromise;
    } catch {
      // Expected — request was aborted
    }

    // Verify the server still accepts new requests
    // Readiness can briefly degrade while the preceding settings reload
    // reopens native indexes. Require bounded recovery, including check details.
    await expect
      .poll(
        async () => {
          const healthResp = await request.get(`${BASE}/api/v1/health`, { headers });
          return { status: healthResp.status(), body: await healthResp.json() };
        },
        { timeout: 30_000 },
      )
      .toMatchObject({ status: 200, body: { status: "ok" } });
  });

  test("chat stream returns SSE content-type", async ({ request }) => {
    const headers = {
      ...deploymentAuthHeaders(),
      "X-API-Key": process.env.VITE_API_KEY || "",
      "Content-Type": "application/json",
    };

    const resp = await fetch(`${BASE}/api/v1/chat/stream`, {
      method: "POST",
      headers,
      body: JSON.stringify({ question: "hello" }),
    });

    expect(resp.headers.get("content-type")).toContain("text/event-stream");
    await resp.body?.cancel();
  });
});
