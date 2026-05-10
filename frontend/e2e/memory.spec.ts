import { test, expect } from "@playwright/test";

test.describe("Memory management", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/memory");
    await expect(page.getByRole("tab", { name: /memories/i })).toBeVisible();
    // Use getByText since Ant Design Button+icon computes accessible name differently
    const addButton = page.getByText("Add", { exact: true });
    await expect(addButton).toBeVisible({ timeout: 15_000 });
    await page.waitForTimeout(500);
  });

  test("should display memory page with stored memories", async ({ page }) => {
    await expect(page.getByRole("tab", { name: /memories/i })).toBeVisible();
    await expect(page.getByRole("tab", { name: /entities/i })).toBeVisible();
    await expect(page.getByRole("tab", { name: /past sessions/i })).toBeVisible();
  });

  test("should show memories tab controls", async ({ page }) => {
    await expect(page.getByText("Add", { exact: true })).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText("Import", { exact: true })).toBeVisible();
    await expect(page.getByText("Export", { exact: true })).toBeVisible();
    await expect(page.getByText("Decay", { exact: true })).toBeVisible();
    await expect(page.getByText("Refresh").last()).toBeVisible();
    // Placeholder uses ASCII dots (regex avoids ellipsis char mismatch)
    await expect(page.getByPlaceholder(/Search key or value/i)).toBeVisible();
  });

  test("should open create memory modal", async ({ page }) => {
    await page.getByText("Add", { exact: true }).click();
    await expect(page.getByText("Create Memory")).toBeVisible();
    // Use exact match to avoid strict-mode collisions with lingering data
    await expect(page.getByText("Key", { exact: true })).toBeVisible();
    await expect(page.getByText("Value", { exact: true })).toBeVisible();
    await expect(page.getByText("Category (optional)")).toBeVisible();
    await expect(page.getByText("Importance (1-5)")).toBeVisible();
    await expect(page.getByText("Create", { exact: true })).toBeDisabled();
  });

  test("should create a new memory", async ({ page }) => {
    const memoryKey = `e2e-test-key-${Date.now()}`;
    const memoryValue = "E2E test memory value created by Playwright";

    await page.getByText("Add", { exact: true }).click();
    await expect(page.getByText("Create Memory")).toBeVisible();

    const createModal = page.locator(".ant-modal:visible");
    const keyInput = createModal.locator("input").first();
    await keyInput.fill(memoryKey);
    const valueTextarea = createModal.locator("textarea").first();
    await valueTextarea.fill(memoryValue);

    await expect(page.getByText("Create", { exact: true })).toBeEnabled();
    await createModal.locator(".ant-btn-primary").click();

    await expect(page.getByText("Memory created")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(memoryKey)).toBeVisible({ timeout: 5_000 });
  });

  test("should edit an existing memory", async ({ page }) => {
    const memoryKey = `e2e-edit-key-${Date.now()}`;
    const updatedValue = "Updated value from E2E test";

    await page.getByText("Add", { exact: true }).click();
    const createModal = page.locator(".ant-modal:visible");
    const keyInput = createModal.locator("input").first();
    await keyInput.fill(memoryKey);
    const valueTextarea = createModal.locator("textarea").first();
    await valueTextarea.fill("Original value");
    await createModal.locator(".ant-btn-primary").click();
    // Wait for modal to close and success toast to appear
    await expect(page.locator(".ant-modal")).not.toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(memoryKey)).toBeVisible({ timeout: 5_000 });

    const memoryRow = page.locator(".ant-list-item").filter({ hasText: memoryKey });
    await memoryRow.getByRole("button", { name: /edit/i }).click();
    await expect(page.getByText("Edit Memory")).toBeVisible();

    const editTextarea = page.locator(".ant-modal").last().locator("textarea").first();
    await editTextarea.fill("");
    await editTextarea.fill(updatedValue);
    await page.locator(".ant-modal .ant-btn-primary").last().click();

    await expect(page.getByText("Memory updated")).toBeVisible({ timeout: 10_000 });
  });

  test("should delete a memory", async ({ page }) => {
    const memoryKey = `e2e-delete-key-${Date.now()}`;

    await page.getByText("Add", { exact: true }).click();
    // Target the LAST (most recent) modal to avoid stacked-modal issues
    const createModal = page.locator(".ant-modal:visible");
    const keyInput = createModal.locator("input").first();
    await keyInput.fill(memoryKey);
    const valueTextarea = createModal.locator("textarea").first();
    await valueTextarea.fill("Memory to be deleted");
    await createModal.locator(".ant-btn-primary").click();
    // Wait for success toast OR the key appearing (either indicates success)
    await expect(page.getByText(memoryKey)).toBeVisible({ timeout: 10_000 });

    const memoryRow = page.locator(".ant-list-item").filter({ hasText: memoryKey });
    await memoryRow.getByRole("button", { name: /delete/i }).click();
    // Confirm deletion — Modal.confirm() shows "Cancel" and "OK" buttons
    await expect(page.locator(".ant-modal-confirm")).toBeVisible();
    await page.locator(".ant-modal-confirm").getByText("OK", { exact: true }).click();

    await expect(page.getByText("Memory deleted")).toBeVisible({ timeout: 10_000 });
    // Check the memory list no longer contains this key (use list-item scope)
    await expect(page.locator(".ant-list-item").filter({ hasText: memoryKey })).not.toBeVisible({ timeout: 5_000 });
  });

  test("should search memories with local search", async ({ page }) => {
    const uniqueValue = `unique-searchable-value-${Date.now()}`;

    await page.getByText("Add", { exact: true }).click();
    const createModal = page.locator(".ant-modal:visible");
    const keyInput = createModal.locator("input").first();
    await keyInput.fill(`search-test-${Date.now()}`);
    const valueTextarea = createModal.locator("textarea").first();
    await valueTextarea.fill(uniqueValue);
    await createModal.locator(".ant-btn-primary").click();
    // Wait for the new memory to appear in list (confirms creation succeeded)
    await expect(page.getByText(uniqueValue)).toBeVisible({ timeout: 10_000 });

    const searchInput = page.getByPlaceholder(/Search key or value/i);
    await searchInput.fill(uniqueValue);
    // Scope to list items only (avoid textarea in still-open modal)
    await expect(page.locator(".ant-list-item").filter({ hasText: uniqueValue })).toBeVisible({ timeout: 5_000 });
  });

  test("should perform semantic search", async ({ page }) => {
    const searchInput = page.getByPlaceholder(/Search key or value/i);
    await searchInput.waitFor({ state: "visible", timeout: 10_000 });
    await searchInput.fill("project information");
    await searchInput.press("Enter");

    const noMatchesOrResults = page.locator(".ant-empty, .ant-list-item");
    await expect(noMatchesOrResults.first()).toBeVisible({ timeout: 10_000 });
  });

  test("should display knowledge graph entities tab", async ({ page }) => {
    await page.getByText("Entities").click();
    await expect(page.getByPlaceholder(/Search entity name/i)).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText("Filter by type")).toBeVisible();
    await expect(page.getByText("Refresh").last()).toBeVisible();
  });

  test("should show entity type filter options", async ({ page }) => {
    await page.getByText("Entities").click();
    const typeFilter = page.locator(".ant-select").filter({ hasText: "Filter by type" });
    await typeFilter.click();
    // Verify dropdown options appear (check first few that fit in viewport)
    await expect(page.getByRole("option", { name: "person" })).toBeAttached({ timeout: 5_000 });
    await expect(page.getByRole("option", { name: "project" })).toBeAttached();
    await page.keyboard.press("Escape");
  });

  test("should display past sessions tab", async ({ page }) => {
    await page.getByText("Past Sessions").click();
    await expect(page.getByText("Refresh").last()).toBeVisible({ timeout: 10_000 });
    // Use .filter() to find a visible content element (avoid hidden list items)
    const contentArea = page.locator(".ant-empty:visible, .ant-list-item:visible");
    await expect(contentArea.first()).toBeVisible({ timeout: 10_000 });
  });

  test("should show empty state when no memories exist with filter", async ({ page }) => {
    const searchInput = page.getByPlaceholder(/Search key or value/i);
    await searchInput.waitFor({ state: "visible", timeout: 10_000 });
    await searchInput.fill(`zzz-nonexistent-${Date.now()}`);
    await expect(page.getByText("No memories found")).toBeVisible({ timeout: 5_000 });
  });
});
