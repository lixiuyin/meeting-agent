/**
 * E2E: Abort a streaming chat response mid-stream, verifying cleanup.
 *
 * Prerequisites: Docker stack running at localhost:8307.
 * Run: npx playwright test stream-abort.spec.ts
 */
import { test, expect } from "@playwright/test";

const BASE = "http://localhost:8307";

test.describe("Stream Abort", () => {
  test("aborted stream does not block subsequent requests", async ({ request }) => {
    const headers = {
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
    await new Promise((resolve) => setTimeout(resolve, 2000));
    const healthResp = await request.get(`${BASE}/api/v1/health`, { headers });
    expect(healthResp.status()).toBe(200);
  });

  test("chat stream returns SSE content-type", async ({ request }) => {
    const headers = {
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
