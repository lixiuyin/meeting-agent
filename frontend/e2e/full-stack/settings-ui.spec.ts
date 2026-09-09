import { expect, test, type Page } from "./fixtures";

test.setTimeout(120_000);

function waitForApiResponse(page: Page, method: string, pathname: string) {
  return page.waitForResponse((response) => {
    const request = response.request();
    return request.method() === method && new URL(response.url()).pathname === pathname;
  });
}

test("settings tabs, reset, save, rebuild, and reload controls use the live backend", async ({
  page,
  request,
}) => {
  const initialResponse = await request.get("/api/v1/settings");
  expect(initialResponse.ok()).toBeTruthy();
  const initial = (await initialResponse.json()) as { llm: { temperature: number } };
  const originalTemperature = initial.llm.temperature;
  const alternateTemperature = originalTemperature === 0.2 ? 0.3 : 0.2;

  await page.goto("/settings");
  const saveButton = page.getByRole("button", { name: /save/i });
  const resetButton = page.getByRole("button", { name: /reset/i });
  await expect(saveButton).toBeVisible({ timeout: 15_000 });

  const temperatureInput = page
    .locator(".ant-form-item")
    .filter({ hasText: /^Temperature/ })
    .locator(".ant-input-number-input");
  await temperatureInput.fill(String(alternateTemperature));
  await expect(saveButton).toBeEnabled();
  await expect(page.getByText("Unsaved changes")).toBeVisible();
  await resetButton.click();
  await expect(temperatureInput).toHaveValue(String(originalTemperature));
  await expect(saveButton).toBeDisabled();

  await temperatureInput.fill(String(alternateTemperature));
  const saveAlternate = waitForApiResponse(page, "PUT", "/api/v1/settings");
  await saveButton.click();
  expect((await saveAlternate).ok()).toBeTruthy();
  await expect(page.getByText(/settings saved/i)).toBeVisible();
  let current = await request.get("/api/v1/settings");
  expect(((await current.json()) as { llm: { temperature: number } }).llm.temperature).toBe(
    alternateTemperature,
  );

  await temperatureInput.fill(String(originalTemperature));
  const restoreResponse = waitForApiResponse(page, "PUT", "/api/v1/settings");
  await saveButton.click();
  expect((await restoreResponse).ok()).toBeTruthy();
  current = await request.get("/api/v1/settings");
  expect(((await current.json()) as { llm: { temperature: number } }).llm.temperature).toBe(
    originalTemperature,
  );

  await page.getByRole("tab", { name: /rag & retrieval/i }).click();
  await page.getByRole("button", { name: /advanced rag configuration/i }).click();
  await expect(page.getByText("Retrieval-Augmented Generation")).toBeVisible();
  await page.getByRole("tab", { name: /ingestion pipeline/i }).click();
  await expect(page.getByText(/speech recognition/i)).toBeVisible();
  await page.getByRole("tab", { name: /memory & context/i }).click();
  await page.getByRole("button", { name: /advanced memory configuration/i }).click();
  await expect(page.getByText("Core Memory Settings")).toBeVisible();
  await page.getByRole("tab", { name: /search & upload/i }).click();
  await expect(page.getByText("Web Search Configuration")).toBeVisible();

  await page.getByRole("tab", { name: /^setting system$/i }).click();
  const rebuildVectors = waitForApiResponse(page, "POST", "/api/v1/settings/rebuild-vectors");
  await page.getByRole("button", { name: /rebuild vectors/i }).click();
  expect((await rebuildVectors).ok()).toBeTruthy();
  await expect(page.getByText(/vector rebuild started in the background/i)).toBeVisible();

  await expect
    .poll(
      async () => {
        const status = await request.get("/api/v1/settings/rebuild-status");
        expect(status.ok()).toBeTruthy();
        return (await status.json()).result;
      },
      { timeout: 90_000 },
    )
    .toBe("completed");

  const rebuildMultimodal = waitForApiResponse(page, "POST", "/api/v1/settings/rebuild-multimodal");
  await page.getByRole("button", { name: /rebuild multimodal/i }).click();
  const multimodalResponse = await rebuildMultimodal;
  // A text-only isolated corpus legitimately has no multimodal index to
  // rebuild (400); 409 is also valid while the vector rebuild still owns the
  // shared rebuild lock. Both prove the visible control reached the backend.
  expect([200, 400, 409]).toContain(multimodalResponse.status());

  const reloadResponse = waitForApiResponse(page, "POST", "/api/v1/settings/reload-config");
  await page.getByRole("button", { name: /reload config/i }).click();
  expect((await reloadResponse).ok()).toBeTruthy();
  await expect(page.getByText(/configuration reloaded successfully/i)).toBeVisible();
});
