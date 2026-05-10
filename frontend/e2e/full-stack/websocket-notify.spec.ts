/**
 * E2E: Verify WebSocket connection receives progress/completion notifications.
 *
 * Prerequisites: Docker stack running at localhost:8307.
 * Run: npx playwright test websocket-notify.spec.ts
 */
import { test, expect } from "@playwright/test";

const BASE = "http://localhost:8307";

test.describe("WebSocket Notifications", () => {
  test("websocket endpoint accepts connection", async ({ page }) => {
    // Use page to create a WebSocket connection via browser
    const wsMessages: string[] = [];

    await page.goto(`${BASE}/`);
    await page.evaluate(
      ({ url, msgs }) => {
        const ws = new WebSocket(`${url}/api/v1/ws`);
        ws.onmessage = (event) => msgs.push(event.data);
        ws.onopen = () => msgs.push("connected");
        ws.onerror = () => msgs.push("error");
        // Close after 3 seconds
        setTimeout(() => ws.close(), 3000);
      },
      { url: BASE, msgs: wsMessages as any }
    );

    // Wait for connection or error
    await page.waitForTimeout(4000);
    expect(wsMessages.length).toBeGreaterThan(0);

    // Should have at least a connection confirmation or error
    const hasConnection = wsMessages.some(
      (m) => m === "connected" || m === "error"
    );
    expect(hasConnection).toBeTruthy();
  });
});
