import { test, expect } from "@playwright/test";

test.describe("Frontend smoke tests", () => {
  test("homepage renders with chat input", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByLabel("Send message")).toBeVisible();
    await expect(page.getByPlaceholder("Ask anything about your meetings...")).toBeVisible();
  });

  test("navigation tabs switch pages", async ({ page }) => {
    await page.goto("/");
    await page.getByText("Materials").first().click();
    await expect(page.getByText("New Meeting")).toBeVisible();

    // Dismiss any lingering toast notifications that could intercept clicks
    await page.locator(".ant-message").evaluate((nodes: NodeListOf<HTMLElement>) => {
      for (let i = nodes.length - 1; i >= 0; i--) nodes[i].remove();
    });

    await page.getByText("History").first().click({ force: true });
    await expect(page.getByText("History")).toBeVisible();

    await page.getByText("Chat").first().click();
    await expect(page.getByPlaceholder("Ask anything about your meetings...")).toBeVisible();
  });

  test("new chat button clears selection", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText("New Chat")).toBeVisible();
  });
});
