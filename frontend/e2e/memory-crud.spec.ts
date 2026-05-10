import { expect, test } from "@playwright/test";

test("memory CRUD baseline flow", async ({ page }) => {
  const memories: Array<{ id: number; key: string; value: string; importance: number }> = [];

  await page.route("**/api/v1/memory/entities**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ entities: [] }),
    });
  });
  await page.route("**/api/v1/sessions/summaries**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ summaries: [] }),
    });
  });
  await page.route("**/api/v1/memory/search", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ results: [] }),
    });
  });
  await page.route("**/api/v1/memory/decay", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) });
  });
  await page.route("**/api/v1/memory**", async (route, request) => {
    if (request.method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ memories }),
      });
      return;
    }
    if (request.method() === "POST") {
      const payload = request.postDataJSON() as { key: string; value: string };
      memories.push({ id: Date.now(), key: payload.key, value: payload.value, importance: 3 });
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({}),
      });
      return;
    }
    if (request.method() === "DELETE") {
      let key = "";
      try {
        const payload = request.postDataJSON() as { key?: string } | null;
        key = payload?.key ?? "";
      } catch {
        // noop
      }
      if (!key) {
        const reqUrl = new URL(request.url());
        key = reqUrl.searchParams.get("key") ?? "";
      }
      const idx = memories.findIndex((m) => m.key === key);
      if (idx >= 0) memories.splice(idx, 1);
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({}),
      });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) });
  });

  await page.goto("/memory");
  await expect(page.getByRole("tab", { name: /memories/i })).toBeVisible();
  await expect(page.getByRole("button", { name: /add/i })).toBeVisible();

  const key = `e2e-memory-${Date.now()}`;
  await page.getByRole("button", { name: /add/i }).click();
  await page.locator(".ant-modal input").first().fill(key);
  await page.locator(".ant-modal textarea").first().fill("E2E memory value");
  await page.getByRole("button", { name: /create/i }).click();
  await expect(page.getByText(key)).toBeVisible();

  const row = page.locator(".ant-list-item").filter({ hasText: key });
  await row.getByRole("button", { name: /delete/i }).click();
  const deleteDialog = page.getByRole("dialog", { name: /delete memory/i });
  await deleteDialog.getByRole("button", { name: /^ok$/i }).click();
  await expect(deleteDialog).not.toBeVisible();
});

test("memory semantic search can be cleared", async ({ page }) => {
  let searchCalled = 0;

  await page.route("**/api/v1/memory/entities**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ entities: [] }),
    });
  });
  await page.route("**/api/v1/sessions/summaries**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ summaries: [] }),
    });
  });
  await page.route("**/api/v1/memory**", async (route, request) => {
    if (request.method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ memories: [] }),
      });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) });
  });
  await page.route("**/api/v1/memory/search", async (route) => {
    searchCalled += 1;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        memories: [
          {
            key: "semantic-key",
            value: "semantic-value",
            importance: 3,
            category: null,
            combined_score: 0.9,
            decay_score: 1.0,
          },
        ],
      }),
    });
  });

  await page.goto("/memory");
  await page.getByPlaceholder("Search key or value...").fill("semantic");
  await page.getByRole("button", { name: "search" }).click();

  await expect.poll(() => searchCalled).toBeGreaterThan(0);
  await expect(page.getByText("semantic-key")).toBeVisible();
  await page.getByRole("button", { name: /clear semantic results/i }).click();
  await expect(page.getByText("No memories found")).toBeVisible();
});
