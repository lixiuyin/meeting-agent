/**
 * E2E: Verify WebSocket connection receives progress/completion notifications.
 *
 * Prerequisites: Docker stack running at localhost:8307.
 * Run: npx playwright test websocket-notify.spec.ts
 */
import { test, expect } from "./fixtures";

test.describe("WebSocket Notifications", () => {
  test("authenticated websocket answers ping", async ({ page, request }) => {
    await page.goto("/");
    const response = await request.post("/api/v1/ws/token");
    expect(response.ok()).toBeTruthy();
    const { token } = (await response.json()) as { token: string };
    const result = await page.evaluate(async (token) => {
      const protocol = location.protocol === "https:" ? "wss" : "ws";
      const params = new URLSearchParams({ client_id: crypto.randomUUID(), token });

      return await new Promise<string>((resolve, reject) => {
        const ws = new WebSocket(`${protocol}://${location.host}/api/v1/ws?${params}`);
        const timeout = setTimeout(() => {
          ws.close();
          reject(new Error("websocket pong timed out"));
        }, 5000);
        ws.onopen = () => ws.send("ping");
        ws.onerror = () => reject(new Error("websocket connection failed"));
        ws.onmessage = (event) => {
          const message = JSON.parse(event.data) as { type?: string };
          if (message.type === "pong") {
            clearTimeout(timeout);
            ws.close();
            resolve("pong");
          }
        };
      });
    }, token);

    expect(result).toBe("pong");
  });
});
