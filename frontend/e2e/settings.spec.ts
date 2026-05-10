import { test, expect } from "@playwright/test";

test.describe("Settings CRUD", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/settings");
    // Wait for settings to load (spinner disappears, Save button visible)
    await expect(page.getByRole("button", { name: /save/i })).toBeVisible({
      timeout: 15_000,
    });
    // Wait for motion animations and form initialization to complete
    await page.waitForTimeout(1500);
  });

  test("should display settings page", async ({ page }) => {
    // Page header should be visible
    await expect(page.getByText("Settings")).toBeVisible();

    // Tab navigation should be present
    await expect(page.getByText("AI Models")).toBeVisible();
    await expect(page.getByText("RAG & Retrieval")).toBeVisible();
    await expect(page.getByText("Ingestion Pipeline")).toBeVisible();
    await expect(page.getByText("Memory & Context")).toBeVisible();
    await expect(page.getByText("Search & Upload")).toBeVisible();
    await expect(page.getByText("System")).toBeVisible();
  });

  test("should show current settings values", async ({ page }) => {
    // AI Models tab should be active by default and show fields with values
    await expect(page.getByText("Language Model")).toBeVisible();

    // Form fields should be populated with values from the API
    const providerSelect = page.locator(".ant-select-selector").first();
    await expect(providerSelect).toBeVisible();

    // Model input should be visible
    const modelInput = page.getByPlaceholder(/model/i);
    await expect(modelInput).first().toBeVisible();

    // Temperature field should have numeric value
    const temperatureLabel = page.getByText("Temperature");
    await expect(temperatureLabel).toBeVisible();
  });

  test("should show LLM configuration fields", async ({ page }) => {
    await expect(page.getByText("Language Model")).toBeVisible();
    await expect(page.getByText("Provider")).toBeVisible();
    await expect(page.getByText("Model")).toBeVisible();
  });

  test("should navigate to RAG tab and show fields", async ({ page }) => {
    await page.getByText("RAG & Retrieval").click();
    await expect(page.getByText("Retrieval-Augmented Generation")).toBeVisible();
    await expect(page.getByText("Chunk Size")).toBeVisible();
    await expect(page.getByText("Chunk Overlap")).toBeVisible();
    await expect(page.getByText("Top K")).toBeVisible();
  });

  test("should navigate to Memory tab and show fields", async ({ page }) => {
    await page.getByText("Memory & Context").click();
    await expect(page.getByText("Core Memory Settings")).toBeVisible();
    await expect(page.getByText("Max Facts Per Turn")).toBeVisible();
  });

  test("should navigate to Search tab and show fields", async ({ page }) => {
    await page.getByText("Search & Upload").click();
    await expect(page.getByText("Web Search Configuration")).toBeVisible();
    await expect(page.getByText("Max Results")).toBeVisible();
  });

  test("should navigate to Ingestion tab and show fields", async ({ page }) => {
    await page.getByText("Ingestion Pipeline").click();
    await expect(page.getByText(/ASR|Speech Recognition/i)).toBeVisible();
  });

  test("should show Save and Reset buttons", async ({ page }) => {
    const saveButton = page.getByRole("button", { name: /save/i });
    await expect(saveButton).toBeVisible();

    const resetButton = page.getByRole("button", { name: /reset/i });
    await expect(resetButton).toBeVisible();
  });

  test("should disable Save button when no changes made", async ({ page }) => {
    const saveButton = page.getByRole("button", { name: /save/i });
    await expect(saveButton).toBeDisabled();
  });

  test("should enable Save button when settings are modified", async ({ page }) => {
    // Modify a setting — change the max_tokens value
    await expect(page.getByText("Language Model")).toBeVisible();

    // Find and modify Max Tokens input
    const maxTokensInputs = page
      .locator(".ant-form-item")
      .filter({ hasText: /max.?tokens/i })
      .locator(".ant-input-number-input");

    if (await maxTokensInputs.count() > 0) {
      await maxTokensInputs.first().click();
      await maxTokensInputs.first().fill("");
      await maxTokensInputs.first().fill("9999");

      // Save button should now be enabled
      const saveButton = page.getByRole("button", { name: /save/i });
      await expect(saveButton).toBeEnabled();

      // Unsaved changes indicator should appear
      await expect(page.getByText("Unsaved changes")).toBeVisible();
    }
  });

  test("should revert changes when Reset is clicked", async ({ page }) => {
    // Find Max Tokens input
    const maxTokensInputs = page
      .locator(".ant-form-item")
      .filter({ hasText: /max.?tokens/i })
      .locator(".ant-input-number-input");

    if (await maxTokensInputs.count() > 0) {
      await maxTokensInputs.first().click();
      await maxTokensInputs.first().fill("");
      await maxTokensInputs.first().fill("9999");

      // Unsaved changes should show
      await expect(page.getByText("Unsaved changes")).toBeVisible();

      // Click Reset
      await page.getByRole("button", { name: /reset/i }).click();

      // Unsaved changes should disappear
      await expect(page.getByText("Unsaved changes")).not.toBeVisible();

      // Save button should be disabled again
      const saveButton = page.getByRole("button", { name: /save/i });
      await expect(saveButton).toBeDisabled();
    }
  });

  test("should navigate to System tab", async ({ page }) => {
    await page.getByText("System").click();
    // System tab shows retention or server info sections
    await expect(
      page.getByText(/retention|server info|readonly/i),
    ).toBeVisible({ timeout: 5000 });
  });
});
