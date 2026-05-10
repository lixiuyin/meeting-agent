/**
 * E2E: Network flap handling — client disconnects and reconnects during streaming.
 *
 * Tests that a network interruption during SSE streaming does not corrupt
 * subsequent sessions or cause server-side errors.
 *
 * Prerequisites: Docker stack running at localhost:8307.
 * Run: npx playwright test network-flap.spec.ts
 */
import { test, expect } from "@playwright/test";

const BASE = "http://localhost:8307";

test.describe("Network Flap Resilience", () => {
  const headers = {
    "X-API-Key": process.env.VITE_API_KEY || "",
    "Content-Type": "application/json",
  };

  test("chat session survives a simulated network flap", async ({ request }) => {
    // Step 1: Create a session with a successful request
    const resp1 = await request.post(`${BASE}/api/v1/chat`, {
      headers,
      data: { question: "What is testing?" },
    });
    expect(resp1.status()).toBe(200);
    const body1 = await resp1.json();
    const sessionId = body1.session_id;

    // Step 2: Simulate flap — abort a streaming request on the same session
    const controller = new AbortController();
    const streamPromise = fetch(`${BASE}/api/v1/chat/stream`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        question: "Tell me more",
        session_id: sessionId,
      }),
      signal: controller.signal,
    });

    // Abort mid-stream
    setTimeout(() => controller.abort(), 300);
    try {
      await streamPromise;
    } catch {
      // Expected
    }

    // Step 3: Verify the same session still works after the flap
    await new Promise((resolve) => setTimeout(resolve, 1000));
    const resp2 = await request.post(`${BASE}/api/v1/chat`, {
      headers,
      data: {
        question: "Final question after flap",
        session_id: sessionId,
      },
    });
    expect(resp2.status()).toBe(200);
  });
});
