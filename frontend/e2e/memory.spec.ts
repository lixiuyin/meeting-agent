import { test, expect } from "@playwright/test";
import axe from "axe-core";
import { installMemoryApiMock } from "./fixtures/mock-api";

test.describe("Memory management", () => {
  test.beforeEach(async ({ page }) => {
    await installMemoryApiMock(page);
    await page.emulateMedia({ reducedMotion: "reduce" });
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

  test("should use the available desktop viewport for the memory workspace", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 1000 });

    const mainBox = await page.locator("#main-content").boundingBox();
    const cardBox = await page.locator(".memory-page-card").boundingBox();
    const listBox = await page.locator(".memory-list-scroll-region").boundingBox();

    expect(mainBox).not.toBeNull();
    expect(cardBox).not.toBeNull();
    expect(listBox).not.toBeNull();
    expect(cardBox!.width).toBeGreaterThan(mainBox!.width - 50);
    expect(mainBox!.y + mainBox!.height - (cardBox!.y + cardBox!.height)).toBeLessThanOrEqual(24);
    expect(listBox!.height).toBeGreaterThan(350);
    expect(listBox!.y + listBox!.height).toBeLessThanOrEqual(cardBox!.y + cardBox!.height - 20);

    const overflow = await page.evaluate(() => {
      const card = document.querySelector<HTMLElement>(".memory-page-card > .ant-card-body")!;
      const list = document.querySelector<HTMLElement>(".memory-list-scroll-region")!;
      return {
        cardBottom: card.getBoundingClientRect().bottom,
        listBottom: list.getBoundingClientRect().bottom,
        documentScrollHeight: document.documentElement.scrollHeight,
        viewportHeight: window.innerHeight,
      };
    });
    expect(overflow.listBottom).toBeLessThanOrEqual(overflow.cardBottom);
    expect(overflow.documentScrollHeight).toBeLessThanOrEqual(overflow.viewportHeight);
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

  test("should create and display a project action with owner and due time", async ({ page }) => {
    const memoryKey = `e2e-action-${Date.now()}`;
    await page.getByText("Add", { exact: true }).click();
    const modal = page.locator(".ant-modal:visible");
    await modal.getByLabel("Key").fill(memoryKey);
    await modal
      .getByRole("textbox", { name: "Value", exact: true })
      .fill("Complete the Atlas security review");
    const factTypeSelect = modal.getByRole("combobox", { name: "Fact type" });
    await factTypeSelect.scrollIntoViewIfNeeded();
    await factTypeSelect.click();
    for (let index = 0; index < 4; index += 1) await factTypeSelect.press("ArrowDown");
    await factTypeSelect.press("Enter");
    await modal.getByLabel("Project ID (optional)").fill("atlas");
    await modal.getByLabel("Assignee").fill("Alice");
    await modal.getByLabel("Due at").fill("2030-01-02T09:00");
    await modal.getByRole("button", { name: "Create", exact: true }).click();

    const row = page.locator(".ant-list-item").filter({ hasText: memoryKey });
    await expect(row).toContainText("action_item");
    await expect(row).toContainText("atlas");
    await expect(row).toContainText("open");
    await expect(row).toContainText("Alice");
    await expect(row).toContainText(/due/i);
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
    const confirmModal = page.locator(".ant-modal-confirm");
    await expect(confirmModal).toBeVisible();
    const deleteResponsePromise = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === "/api/v1/memory" &&
        response.request().method() === "DELETE",
    );
    await confirmModal.getByRole("button", { name: "OK", exact: true }).click();
    expect((await deleteResponsePromise).ok()).toBe(true);

    await expect(page.getByText("Memory deleted")).toBeVisible({ timeout: 10_000 });
    // Check the memory list no longer contains this key (use list-item scope)
    await expect(page.locator(".ant-list-item").filter({ hasText: memoryKey })).not.toBeVisible({
      timeout: 5_000,
    });
  });

  test("should search memories with local search", async ({ page }) => {
    const uniqueValue = `unique-searchable-value-${Date.now()}`;
    const memoryKey = `search-test-${Date.now()}`;

    await page.getByText("Add", { exact: true }).click();
    const createModal = page.locator(".ant-modal:visible");
    const keyInput = createModal.locator("input").first();
    await keyInput.fill(memoryKey);
    const valueTextarea = createModal.locator("textarea").first();
    await valueTextarea.fill(uniqueValue);
    await expect(createModal.getByText("Create", { exact: true })).toBeEnabled();
    await createModal.locator(".ant-btn-primary").click();
    await expect(page.getByText("Memory created")).toBeVisible({ timeout: 10_000 });
    await expect(createModal).not.toBeVisible({ timeout: 10_000 });
    // Wait for the new memory to appear in the list (confirms creation succeeded).
    await expect(page.getByText(uniqueValue)).toBeVisible({ timeout: 10_000 });

    const searchInput = page.getByPlaceholder(/Search key or value/i);
    await searchInput.fill(uniqueValue);
    // Scope to list items only (avoid textarea in still-open modal)
    await expect(page.locator(".ant-list-item").filter({ hasText: uniqueValue })).toBeVisible({
      timeout: 5_000,
    });
  });

  test("should perform semantic search", async ({ page }) => {
    const searchInput = page.getByPlaceholder(/Search key or value/i);
    await searchInput.waitFor({ state: "visible", timeout: 10_000 });
    await searchInput.fill("project information");
    await searchInput.press("Enter");

    const noMatchesOrResults = page.locator(".ant-empty, .ant-list-item");
    await expect(noMatchesOrResults.first()).toBeVisible({ timeout: 10_000 });
  });

  test("semantic results update immediately after deletion", async ({ page }) => {
    const memoryKey = `semantic-delete-${Date.now()}`;
    await page.getByText("Add", { exact: true }).click();
    const createModal = page.locator(".ant-modal:visible");
    await createModal.locator("input").first().fill(memoryKey);
    await createModal.locator("textarea").first().fill("semantic deletion regression");
    await createModal.locator(".ant-btn-primary").click();
    await expect(page.getByText(memoryKey)).toBeVisible({ timeout: 10_000 });

    const searchInput = page.getByPlaceholder(/Search key or value/i);
    await searchInput.fill(memoryKey);
    await searchInput.press("Enter");
    const memoryRow = page.locator(".ant-list-item").filter({ hasText: memoryKey });
    await expect(memoryRow).toBeVisible();
    await memoryRow.getByRole("button", { name: /delete/i }).click();
    const confirmModal = page.locator(".ant-modal-confirm");
    await expect(confirmModal).toBeVisible();
    const deleteResponsePromise = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === "/api/v1/memory" &&
        response.request().method() === "DELETE",
    );
    await confirmModal.getByRole("button", { name: "OK", exact: true }).click();
    expect((await deleteResponsePromise).ok()).toBe(true);

    await expect(page.getByText("Memory deleted")).toBeVisible({ timeout: 10_000 });
    await expect(memoryRow).not.toBeVisible({ timeout: 10_000 });
  });

  test("populated memory list meets automated WCAG A and AA checks", async ({ page }) => {
    await page.getByText("Add", { exact: true }).click();
    const createModal = page.locator(".ant-modal:visible");
    await createModal.locator("input").first().fill("accessible-memory");
    await createModal.locator("textarea").first().fill("A populated memory with evidence text");
    await createModal.locator(".ant-btn-primary").click();
    await expect(page.getByText("accessible-memory")).toBeVisible();
    await page.waitForTimeout(1_000);
    await page.addScriptTag({ content: axe.source });

    const result = await page.evaluate(async () =>
      window.axe.run(document, {
        runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa"] },
      }),
    );
    expect(
      result.violations,
      result.violations.map((violation) => violation.id).join(", "),
    ).toEqual([]);
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
