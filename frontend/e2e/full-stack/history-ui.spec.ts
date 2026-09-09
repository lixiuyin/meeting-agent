import { expect, test, type Page } from "./fixtures";
import { deleteSessionIfPresent, seedChatSession } from "./test-data";

test.setTimeout(180_000);

function waitForApiResponse(page: Page, method: string, pathname: string) {
  return page.waitForResponse(
    (response) => {
      const request = response.request();
      return request.method() === method && new URL(response.url()).pathname === pathname;
    },
    { timeout: 150_000 },
  );
}

test("history controls search, inspect, summarize, continue, and delete live sessions", async ({
  page,
  request,
}) => {
  const suffix = Date.now();
  const marker = `ORCHID-HISTORY-${suffix}`;
  const batchMarker = `TULIP-HISTORY-${suffix}`;
  const primaryTitle = `E2E History Primary ${suffix}`;
  const batchTitle = `E2E History Batch ${suffix}`;
  const primaryId = seedChatSession({
    title: primaryTitle,
    marker,
    turns: 4,
    agentContent: (contentMarker, turn) =>
      turn === 1
        ? `## ${contentMarker} agent answer ${turn}\n\n**Formatted evidence** [1]`
        : `${contentMarker} agent answer ${turn}`,
    agentSources: [
      {
        meeting_id: 1,
        meeting_title: "History role contract source",
        content: "The persisted ai role retains source provenance.",
        score: 0.9,
        chunk_index: 0,
      },
    ],
  });
  const batchId = seedChatSession({ title: batchTitle, marker: batchMarker, turns: 2 });

  try {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/history");
    const refreshResponse = waitForApiResponse(page, "GET", "/api/v1/sessions");
    await page.getByRole("button", { name: /refresh sessions/i }).click();
    expect((await refreshResponse).ok()).toBeTruthy();

    const search = page.getByPlaceholder(/search across sessions/i);
    const searchResponse = waitForApiResponse(page, "POST", "/api/v1/sessions/search");
    await search.fill(marker);
    expect((await searchResponse).ok()).toBeTruthy();
    await expect(page.getByText(primaryTitle)).toBeVisible();

    const primaryCard = page
      .getByRole("button", { name: `Expand conversation ${primaryTitle}` })
      .locator("..");
    await primaryCard
      .getByRole("button", { name: /expand conversation/i })
      .first()
      .click();
    await expect(page.getByText(`${marker} user question 1`, { exact: true })).toBeVisible();
    await expect(
      page.getByRole("heading", { name: `${marker} agent answer 1`, exact: true }),
    ).toBeVisible();
    await expect(page.getByText("Formatted evidence", { exact: true })).toHaveCSS(
      "font-weight",
      /^(600|700|bold)$/,
    );
    await expect(
      page.locator("[data-source-key]").filter({ hasText: "History role contract source" }),
    ).toBeVisible();

    await page.getByRole("button", { name: /^user \(4\)$/i }).click();
    await expect(
      page.getByRole("heading", { name: `${marker} agent answer 1`, exact: true }),
    ).not.toBeVisible();
    await page.getByRole("button", { name: /^agent \(4\)$/i }).click();
    await expect(page.getByText(`${marker} user question 1`, { exact: true })).not.toBeVisible();
    await page.getByRole("button", { name: /^all \(8\)$/i }).click();

    const summaryResponse = waitForApiResponse(
      page,
      "POST",
      `/api/v1/sessions/${primaryId}/summarize`,
    );
    await page.getByRole("button", { name: /summarize/i }).click();
    const summarized = await summaryResponse;
    expect(summarized.ok(), await summarized.text()).toBeTruthy();
    await expect(page.getByText(/session summary generated/i)).toBeVisible({ timeout: 15_000 });

    await page.getByRole("button", { name: /continue conversation/i }).click();
    await expect(page).toHaveURL(new RegExp(`sessionId=${primaryId}`));
    await expect(page.getByText(`${marker} user question 1`, { exact: true })).toBeVisible({
      timeout: 15_000,
    });

    await page.goto("/history");
    const repeatSearchResponse = waitForApiResponse(page, "POST", "/api/v1/sessions/search");
    await search.fill(marker);
    expect((await repeatSearchResponse).ok()).toBeTruthy();
    await expect(page.getByText(primaryTitle)).toBeVisible();
    const refreshedPrimaryCard = page
      .getByRole("button", {
        name: new RegExp(`^(?:Expand|Collapse) conversation ${primaryTitle}$`),
      })
      .first()
      .locator("..");
    await refreshedPrimaryCard.getByRole("button", { name: /delete conversation/i }).click();
    const deleteDialog = page.getByRole("dialog", { name: /delete session/i });
    const deleteResponse = waitForApiResponse(page, "DELETE", `/api/v1/sessions/${primaryId}`);
    await deleteDialog.getByRole("button", { name: /^delete$/i }).click();
    expect((await deleteResponse).ok()).toBeTruthy();

    const batchSearchResponse = waitForApiResponse(page, "POST", "/api/v1/sessions/search");
    await search.fill(batchMarker);
    expect((await batchSearchResponse).ok()).toBeTruthy();
    await expect(page.getByText(batchTitle)).toBeVisible();
    await expect(page.getByText(/1 conversation matching/i)).toBeVisible();
    await page.getByRole("button", { name: /select conversations/i }).click();
    const batchCheckbox = page.getByRole("checkbox", { name: new RegExp(batchTitle, "i") });
    await batchCheckbox.click();
    await expect(page.getByText("1 selected", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: /clear conversation selection/i }).click();
    await expect(batchCheckbox).not.toBeChecked();
    await expect(page.getByText("0 selected", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: /select all conversations/i }).click();
    await expect(batchCheckbox).toBeChecked();
    await expect(page.getByText("1 selected", { exact: true })).toBeVisible();
    const batchDeleteResponse = waitForApiResponse(page, "POST", "/api/v1/sessions/batch-delete");
    await page.getByRole("button", { name: /delete selected conversations/i }).click();
    await page
      .getByRole("dialog", { name: /delete 1 conversations/i })
      .getByRole("button", { name: /^delete$/i })
      .click();
    expect((await batchDeleteResponse).ok()).toBeTruthy();
    await expect(page.getByText(batchTitle)).not.toBeVisible();
  } finally {
    await deleteSessionIfPresent(request, primaryId);
    await deleteSessionIfPresent(request, batchId);
  }
});
