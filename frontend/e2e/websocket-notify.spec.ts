import { expect, test } from "@playwright/test";

test("materials page handles websocket-driven refresh path", async ({ page }) => {
  await page.addInitScript(() => {
    class FakeWebSocket {
      public onopen: ((ev: Event) => void) | null = null;
      public onmessage: ((ev: MessageEvent) => void) | null = null;
      public onclose: ((ev: CloseEvent) => void) | null = null;
      constructor() {
        setTimeout(() => this.onopen?.(new Event("open")), 10);
        setTimeout(
          () =>
            this.onmessage?.(
              new MessageEvent("message", {
                data: JSON.stringify({ type: "complete", meeting_id: 1, status: "ready" }),
              }),
            ),
          100,
        );
      }
      send() {}
      close() {
        this.onclose?.(new CloseEvent("close"));
      }
      addEventListener() {}
      removeEventListener() {}
    }
    // @ts-expect-error test override
    window.WebSocket = FakeWebSocket;
  });

  let callCount = 0;
  await page.route("**/api/v1/meetings*", async (route) => {
    callCount += 1;
    const meetings =
      callCount > 1
        ? [{ id: 1, title: "WS Meeting", status: "ready", file_type: "txt" }]
        : [{ id: 1, title: "WS Meeting", status: "processing", file_type: "txt" }];
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ meetings }),
    });
  });

  await page.goto("/materials");
  await expect(page.getByPlaceholder(/search materials/i)).toBeVisible();
  await expect.poll(() => callCount).toBeGreaterThan(1);
});

test("materials page ignores unrelated websocket events", async ({ page }) => {
  await page.addInitScript(() => {
    class FakeWebSocket {
      public onopen: ((ev: Event) => void) | null = null;
      public onmessage: ((ev: MessageEvent) => void) | null = null;
      constructor() {
        setTimeout(() => this.onopen?.(new Event("open")), 10);
        setTimeout(
          () =>
            this.onmessage?.(
              new MessageEvent("message", {
                data: JSON.stringify({ type: "ping" }),
              }),
            ),
          100,
        );
      }
      send() {}
      close() {}
      addEventListener() {}
      removeEventListener() {}
    }
    // @ts-expect-error test override
    window.WebSocket = FakeWebSocket;
  });

  let callCount = 0;
  await page.route("**/api/v1/meetings*", async (route) => {
    callCount += 1;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        meetings: [{ id: 1, title: "WS Meeting", status: "processing", file_type: "txt" }],
      }),
    });
  });

  await page.goto("/materials");
  await expect(page.getByPlaceholder(/search materials/i)).toBeVisible();
  await expect.poll(() => callCount).toBeGreaterThan(0);
  const baseline = callCount;
  await expect.poll(() => callCount, { timeout: 1000 }).toBe(baseline);
});
