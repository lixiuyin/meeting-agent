import { expect, test } from "./fixtures";
import { deleteMeetingIfPresent, uploadTextFile, waitForMeetingReady } from "./test-data";

test("delete meeting button removes only the test-owned meeting", async ({ page, request }) => {
  const suffix = Date.now();
  const title = `E2E Delete Meeting ${suffix}`;
  const { meetingId } = await uploadTextFile(request, {
    title,
    name: `delete-meeting-${suffix}.txt`,
    content:
      `Disposable meeting created by browser test ${suffix}. ` +
      "It contains enough deterministic prose for the ingestion pipeline to accept and index. " +
      "The test deletes this exact meeting through the visible confirmation dialog.",
  });

  try {
    await waitForMeetingReady(request, meetingId);
    await page.goto("/materials");
    await page.getByPlaceholder(/search materials/i).fill(title);

    const openButton = page.getByRole("button", { name: `Open meeting ${title}` });
    const card = openButton.locator("..");
    await expect(card).toBeVisible();
    await card.getByRole("button", { name: "Delete meeting", exact: true }).click();

    const dialog = page.getByRole("dialog", { name: /delete meeting/i });
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: /^delete$/i }).click();

    await expect(card).not.toBeVisible();
    const detail = await request.get(`/api/v1/meetings/${meetingId}`);
    expect(detail.status()).toBe(404);
  } finally {
    await deleteMeetingIfPresent(request, meetingId);
  }
});
