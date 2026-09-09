import { deploymentAuthHeaders, expect, test, type APIRequestContext, type Page } from "./fixtures";

test.setTimeout(180_000);

async function deleteMemoryIfPresent(request: APIRequestContext, key: string): Promise<void> {
  try {
    const response = await request.delete("/api/v1/memory", { params: { key } });
    if ([200, 404].includes(response.status())) return;
  } catch {
    // The Playwright request fixture closes when a test is interrupted. Fall
    // through to Node fetch so test-owned data is still cleaned up.
  }
  const baseUrl = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:8307";
  const url = new URL("/api/v1/memory", baseUrl);
  url.searchParams.set("key", key);
  const apiKey = process.env.E2E_API_KEY ?? process.env.VITE_API_KEY ?? "";
  const response = await fetch(url, {
    method: "DELETE",
    headers: { ...deploymentAuthHeaders(), ...(apiKey ? { "X-API-Key": apiKey } : {}) },
    signal: AbortSignal.timeout(10_000),
  });
  if (![200, 404].includes(response.status)) {
    throw new Error(`cleanup failed for memory ${key}: HTTP ${response.status}`);
  }
}

function waitForApiMutation(page: Page, method: string, pathname: string) {
  return page.waitForResponse(
    (response) => {
      const request = response.request();
      return request.method() === method && new URL(response.url()).pathname === pathname;
    },
    { timeout: 30_000 },
  );
}

test("memory controls perform real CRUD, search, export, decay, and refresh", async ({
  page,
  request,
}) => {
  const suffix = Date.now();
  const createdKey = `e2e-browser-memory-${suffix}`;
  const importedKey = `e2e-imported-memory-${suffix}`;

  try {
    await page.goto("/memory");

    await page.getByRole("button", { name: /add/i }).click();
    const createDialog = page.getByRole("dialog", { name: /create memory/i });
    await createDialog.getByPlaceholder(/project_deadline/i).fill(createdKey);
    await createDialog.getByPlaceholder(/memory content/i).fill("Original browser memory value");
    await createDialog.getByPlaceholder(/project, person, fact/i).fill("e2e");
    const createFactType = createDialog.getByRole("combobox", { name: "Fact type" });
    await createFactType.scrollIntoViewIfNeeded();
    await createFactType.click();
    for (let index = 0; index < 4; index += 1) await createFactType.press("ArrowDown");
    await createFactType.press("Enter");
    await createDialog.getByLabel("Project ID (optional)").fill("atlas-browser");
    await createDialog.getByLabel("Assignee").fill("Alice");
    await createDialog.getByLabel("Due at").fill("2030-01-02T09:00");
    const createResponse = waitForApiMutation(page, "POST", "/api/v1/memory");
    await createDialog.getByRole("button", { name: /^create$/i }).click();
    expect((await createResponse).ok()).toBeTruthy();
    await expect(page.getByText(createdKey)).toBeVisible({ timeout: 15_000 });

    let row = page.locator(".ant-list-item").filter({ hasText: createdKey });
    await expect(row).toContainText("action_item");
    await expect(row).toContainText("atlas-browser");
    await expect(row).toContainText("Alice");
    await expect(row).toContainText(/due/i);
    await row.getByRole("button", { name: /^edit$/i }).click();
    const editDialog = page.getByRole("dialog", { name: /edit memory/i });
    await editDialog.getByPlaceholder(/memory content/i).fill("Updated browser memory value");
    await editDialog.getByLabel("Project ID (optional)").clear();
    await editDialog.getByLabel("Assignee").clear();
    await editDialog.getByLabel("Due at").clear();
    const editFactType = editDialog.getByRole("combobox", { name: "Fact type" });
    await editFactType.click();
    for (let index = 0; index < 4; index += 1) await editFactType.press("ArrowUp");
    await editFactType.press("Enter");
    const updateResponse = waitForApiMutation(page, "PUT", "/api/v1/memory");
    await editDialog.getByRole("button", { name: /^update$/i }).click();
    expect((await updateResponse).ok()).toBeTruthy();
    await expect(page.getByText("Updated browser memory value")).toBeVisible({ timeout: 15_000 });
    row = page.locator(".ant-list-item").filter({ hasText: createdKey });
    await expect(row).toContainText("fact");
    await expect(row).not.toContainText("atlas-browser");
    await expect(row).not.toContainText("Alice");

    row = page.locator(".ant-list-item").filter({ hasText: createdKey });
    const feedbackResponse = waitForApiMutation(page, "POST", "/api/v1/memory/feedback");
    await row.getByRole("button", { name: /this memory was useful/i }).click();
    expect((await feedbackResponse).ok()).toBeTruthy();
    await expect(page.getByText(/marked this memory as useful/i)).toBeVisible();

    await page.getByRole("button", { name: /import/i }).click();
    const importDialog = page.getByRole("dialog", { name: /import memories/i });
    await importDialog.locator("textarea").fill(
      JSON.stringify([
        {
          key: importedKey,
          value: "Imported through the real browser and API",
          category: "e2e",
          importance: 4,
          fact_type: "decision",
          assertion_status: "pending",
          project_id: "browser-e2e",
        },
      ]),
    );
    const importResponse = waitForApiMutation(page, "POST", "/api/v1/memory/batch");
    await importDialog.getByRole("button", { name: /^import$/i }).click();
    expect((await importResponse).ok()).toBeTruthy();
    await expect(page.getByText(importedKey)).toBeVisible({ timeout: 15_000 });

    const typeFilter = page.getByLabel("Fact type");
    const statusFilter = page.getByLabel("Lifecycle status");
    await typeFilter.click();
    await page.getByRole("option", { name: "decision", exact: true }).click();
    await statusFilter.click();
    await page.getByRole("option", { name: "pending", exact: true }).click();
    row = page.locator(".ant-list-item").filter({ hasText: importedKey });
    await expect(row).toBeVisible();
    await expect(row.getByText("browser-e2e", { exact: true }).first()).toBeVisible();

    const confirmResponse = waitForApiMutation(page, "PUT", "/api/v1/memory");
    await row.getByRole("button", { name: /confirm this fact/i }).click();
    expect((await confirmResponse).ok()).toBeTruthy();
    await expect(row).not.toBeVisible();

    await statusFilter.click();
    await page.getByRole("option", { name: "confirmed", exact: true }).click();
    row = page.locator(".ant-list-item").filter({ hasText: importedKey });
    await expect(row).toBeVisible();
    const historyResponse = waitForApiMutation(page, "GET", "/api/v1/memory/versions");
    await row.getByRole("button", { name: /view version history/i }).click();
    expect((await historyResponse).ok()).toBeTruthy();
    const historyDialog = page.getByRole("dialog", { name: `History: ${importedKey}` });
    await expect(historyDialog.getByText("v2", { exact: true })).toBeVisible();
    await expect(historyDialog.getByText("v1", { exact: true })).toBeVisible();
    await historyDialog.getByRole("button", { name: /close/i }).click();

    const retractResponse = waitForApiMutation(page, "PUT", "/api/v1/memory");
    await row.getByRole("button", { name: /retract this fact/i }).click();
    await page.getByRole("button", { name: /^ok$/i }).click();
    expect((await retractResponse).ok()).toBeTruthy();
    await expect(row).not.toBeVisible();

    for (const filter of [typeFilter, statusFilter]) {
      const select = filter.locator(
        "xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' ant-select ')][1]",
      );
      await select.hover();
      await select.locator(".ant-select-clear").click();
    }

    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: /export/i }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toMatch(/^memories_export_\d{4}-\d{2}-\d{2}\.json$/);

    const decayResponse = waitForApiMutation(page, "POST", "/api/v1/memory/decay");
    await page.getByRole("button", { name: /decay/i }).click();
    const decayResult = await decayResponse;
    expect(
      decayResult.ok(),
      `decay returned HTTP ${decayResult.status()}: ${await decayResult.text()}`,
    ).toBeTruthy();
    await expect(page.getByText(/updated relevance scores/i)).toBeVisible();
    const refreshResponse = page.waitForResponse((response) => {
      const request = response.request();
      return request.method() === "GET" && new URL(response.url()).pathname === "/api/v1/memory";
    });
    await page.getByRole("button", { name: /refresh/i }).click();
    const refreshed = await refreshResponse;
    expect(refreshed.ok()).toBeTruthy();
    const refreshedBody = (await refreshed.json()) as { items: { key: string }[] };
    expect(refreshedBody.items.map((item) => item.key)).toEqual(
      expect.arrayContaining([createdKey, importedKey]),
    );

    const search = page.getByPlaceholder(/search key or value/i);
    await search.fill("Updated browser memory");
    const searchResponse = waitForApiMutation(page, "POST", "/api/v1/memory/search");
    await page.getByRole("button", { name: /^search$/i }).click();
    expect((await searchResponse).ok()).toBeTruthy();
    await expect(page.getByText(createdKey)).toBeVisible({ timeout: 15_000 });
    await page.getByRole("button", { name: /clear semantic results/i }).click();

    await page.getByRole("tab", { name: /entities/i }).click();
    await expect(page.getByText(/filter by type/i)).toBeVisible();
    await expect(page.locator(".ant-collapse-content-active")).toHaveCount(0);
    await page.getByRole("button", { name: /refresh/i }).click();
    await page.getByRole("tab", { name: /past sessions/i }).click();
    await expect(page.getByPlaceholder(/search entities/i)).toHaveCount(0);
    const summariesPanel = page.getByRole("tabpanel", { name: /past sessions/i });
    await expect(summariesPanel).toBeVisible();
    await summariesPanel.getByRole("button", { name: /refresh/i }).click();

    await page.getByRole("tab", { name: /memories/i }).click();
    await page.getByPlaceholder(/search key or value/i).fill(createdKey);
    row = page.locator(".ant-list-item").filter({ hasText: createdKey });
    await expect(row).toBeVisible();
    await row.getByRole("button", { name: /^delete$/i }).click();
    const deleteDialog = page.getByRole("dialog", { name: /delete memory/i });
    const deleteResponse = waitForApiMutation(page, "DELETE", "/api/v1/memory");
    await deleteDialog.getByRole("button", { name: /^ok$/i }).click();
    expect((await deleteResponse).ok()).toBeTruthy();
    await expect(row).not.toBeVisible();
  } finally {
    await deleteMemoryIfPresent(request, createdKey);
    await deleteMemoryIfPresent(request, importedKey);
  }
});

test("confirming a disputed memory resolves its old fact atomically", async ({ page, request }) => {
  const suffix = Date.now();
  const oldKey = `e2e-conflict-old-${suffix}`;
  const candidateKey = `e2e-conflict-candidate-${suffix}`;
  try {
    const seed = await request.post("/api/v1/memory/batch", {
      timeout: 30_000,
      data: {
        memories: [
          {
            key: oldKey,
            value: "Nina owns the incident review",
            fact_type: "project_fact",
            assertion_status: "confirmed",
            project_id: "incident-review",
          },
          {
            key: candidateKey,
            value: "Omar owns the incident review",
            fact_type: "project_fact",
            assertion_status: "disputed",
            project_id: "incident-review",
            conflicts_with: [oldKey],
          },
        ],
      },
    });
    expect(seed.ok()).toBeTruthy();

    await page.goto("/memory");
    await page.getByPlaceholder(/search key or value/i).fill(candidateKey);
    const candidate = page.locator(".ant-list-item").filter({ hasText: candidateKey });
    await expect(candidate).toBeVisible();
    const resolution = waitForApiMutation(page, "POST", "/api/v1/memory/resolve-conflict");
    await candidate.getByRole("button", { name: /confirm this fact/i }).click();
    const resolutionResponse = await resolution;
    expect(resolutionResponse.ok()).toBeTruthy();
    const resolutionBody = (await resolutionResponse.json()) as {
      winner: { key: string; assertion_status: string };
      superseded_keys: string[];
    };
    expect(resolutionBody.winner.key).toBe(candidateKey);
    expect(resolutionBody.winner.assertion_status).toBe("confirmed");
    expect(resolutionBody.superseded_keys).toContain(oldKey);

    // Query each key independently. The shared E2E database can contain more
    // than one page of similarly prefixed records from interrupted runs.
    const winnerState = await request.get("/api/v1/memory", {
      params: { q: candidateKey, include_expired: true },
    });
    const loserState = await request.get("/api/v1/memory", {
      params: { q: oldKey, include_expired: true },
    });
    expect(winnerState.ok()).toBeTruthy();
    expect(loserState.ok()).toBeTruthy();
    const winnerItems = (await winnerState.json()) as {
      items: { key: string; assertion_status: string }[];
    };
    const loserItems = (await loserState.json()) as {
      items: { key: string; assertion_status: string; superseded_by?: string | null }[];
    };
    expect(winnerItems.items.find((item) => item.key === candidateKey)?.assertion_status).toBe(
      "confirmed",
    );
    const loser = loserItems.items.find((item) => item.key === oldKey);
    expect(loser?.assertion_status).toBe("superseded");
    expect(loser?.superseded_by).toBe(candidateKey);
  } finally {
    await deleteMemoryIfPresent(request, oldKey);
    await deleteMemoryIfPresent(request, candidateKey);
  }
});

test("Chinese Meeting Review renders and confirms a real pending fact", async ({
  page,
  request,
}) => {
  const key = `e2e-meeting-review-${Date.now()}`;
  try {
    const seeded = await request.post("/api/v1/memory/batch", {
      data: {
        memories: [
          {
            key,
            value: "发布负责人是林晓，目标日期为 2031 年 10 月 17 日。",
            fact_type: "decision",
            assertion_status: "pending",
            project_id: "meeting-review-e2e",
          },
        ],
      },
    });
    expect(seeded.ok()).toBeTruthy();

    await page.addInitScript(() => window.localStorage.setItem("locale", "zh"));
    await page.goto("/memory");
    await page.getByRole("tab", { name: "会议审核" }).click();

    const card = page.locator(".meeting-review-card").filter({ hasText: key });
    await expect(card).toBeVisible({ timeout: 15_000 });
    await expect(card).toContainText("候选事实");
    await expect(card).toContainText("发布负责人是林晓");
    await expect(card).toContainText("meeting-review-e2e");

    const confirmation = waitForApiMutation(page, "PUT", "/api/v1/memory");
    await card.getByRole("button", { name: "确认事实" }).click();
    expect((await confirmation).ok()).toBeTruthy();
    await expect(card).not.toBeVisible();
  } finally {
    await deleteMemoryIfPresent(request, key);
  }
});
