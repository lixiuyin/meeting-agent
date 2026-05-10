import { expect, test } from "@playwright/test";

test("session resume loads prior messages from URL sessionId", async ({ page }) => {
  await page.route("**/api/v1/sessions/test-session/messages*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        messages: [
          { role: "human", content: "previous user question" },
          { role: "ai", content: "previous agent answer" },
        ],
      }),
    });
  });
  await page.route("**/api/v1/meetings*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ meetings: [] }),
    });
  });

  await page.goto("/?sessionId=test-session");
  await expect(page.getByText("previous user question")).toBeVisible();
  await expect(page.getByText("previous agent answer")).toBeVisible();
});
