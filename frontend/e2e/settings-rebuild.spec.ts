import { expect, test } from "@playwright/test";

test("settings rebuild vectors action works", async ({ page }) => {
  let rebuildCalled = false;
  await page.route("**/api/v1/settings", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        llm: { binding: "openai", model: "gpt-4o-mini", temperature: 0.2, max_tokens: 1024 },
        embedding: { binding: "openai", model: "text-embedding-3-small", dimension: 1536 },
        rag: {
          chunk_size: 1200,
          chunk_overlap: 200,
          top_k: 5,
          score_threshold: 0,
          query_rewrite_enabled: true,
          hybrid_search_enabled: false,
          hybrid_alpha: 0.5,
        },
        memory: {
          auto_extract: true,
          max_facts_per_turn: 3,
          session_max_history: 20,
          decay_enabled: true,
          ttl_days: 30,
        },
        search: { binding: "duckduckgo", max_results: 5, timeout_sec: 10 },
        upload: { max_upload_size_mb: 500 },
      }),
    });
  });
  await page.route("**/api/v1/settings/bindings", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        llm: ["openai"],
        embedding: ["openai"],
        search: ["duckduckgo"],
        reranker: [],
      }),
    });
  });
  await page.route("**/api/v1/settings/rebuild-vectors", async (route) => {
    rebuildCalled = true;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ message: "started" }),
    });
  });

  await page.goto("/settings");
  await page.getByRole("tab", { name: /system/i }).click();
  await expect(page.getByText("Operations", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /rebuild vectors/i }).click();
  await expect.poll(() => rebuildCalled).toBeTruthy();
});
