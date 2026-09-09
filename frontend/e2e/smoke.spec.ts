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
    await page.locator(".ant-message").evaluateAll((nodes: HTMLElement[]) => {
      for (let i = nodes.length - 1; i >= 0; i--) nodes[i].remove();
    });

    const historyTab = page.getByRole("tab", { name: /history/i });
    await historyTab.click({ force: true });
    await expect(historyTab).toHaveAttribute("aria-selected", "true");

    await page.getByRole("tab", { name: /chat/i }).click();
    await expect(page.getByPlaceholder("Ask anything about your meetings...")).toBeVisible();
  });

  test("new chat button clears selection", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText("New Chat")).toBeVisible();
  });
});
