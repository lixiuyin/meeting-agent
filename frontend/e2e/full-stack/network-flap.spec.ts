/** E2E: a transient API network failure does not strand the History UI. */
import { test, expect } from "./fixtures";
import { deleteSessionIfPresent, seedChatSession } from "./test-data";

test.describe("Network Flap Resilience", () => {
  test("session list recovers after a simulated network flap", async ({ page, request }) => {
    const marker = `NETWORK-FLAP-${Date.now()}`;
    const sessionId = seedChatSession({ title: marker, marker, turns: 1 });
    let shouldAbort = true;
    const sessionsRequest = /\/api\/v1\/sessions(?:\?.*)?$/;

    try {
      await page.route(sessionsRequest, async (route) => {
        if (shouldAbort) {
          shouldAbort = false;
          await route.abort("internetdisconnected");
          return;
        }
        await route.continue();
      });

      await page.goto("/history");
      await expect.poll(() => shouldAbort).toBe(false);

      const retryResponse = page.waitForResponse(
        (response) => response.request().method() === "GET" && sessionsRequest.test(response.url()),
      );
      await page.getByRole("button", { name: /refresh sessions/i }).click();
      expect((await retryResponse).ok()).toBeTruthy();
      await expect(page.getByText(marker, { exact: true })).toBeVisible();

      const messages = await request.get(`/api/v1/sessions/${sessionId}/messages`);
      expect(messages.ok(), await messages.text()).toBeTruthy();
      expect((await messages.json()).total).toBe(2);
    } finally {
      await deleteSessionIfPresent(request, sessionId);
    }
  });
});
