import { expect, test } from "@playwright/test";

test("stream abort removes empty agent placeholder", async ({ page }) => {
  await page.route("**/api/v1/meetings*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ meetings: [] }),
    });
  });

  await page.route("**/api/v1/chat/stream", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 2000));
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: "",
    });
  });

  await page.goto("/");
  await page.getByPlaceholder("Ask anything about your meetings...").fill("abort stream test");
  await page.keyboard.press("Enter");
  await page.getByRole("button", { name: /new chat/i }).click();
  await expect(page.getByText("How can I help you today?")).toBeVisible();
});
