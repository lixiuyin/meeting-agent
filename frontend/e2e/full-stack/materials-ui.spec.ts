import { expect, test } from "./fixtures";
import { deleteMeetingIfPresent, uploadTextFile, waitForMeetingReady } from "./test-data";

test.setTimeout(120_000);

function waitForApiResponse(
  page: import("@playwright/test").Page,
  method: string,
  pathname: string,
) {
  return page.waitForResponse((response) => {
    const request = response.request();
    return request.method() === method && new URL(response.url()).pathname === pathname;
  });
}

test("materials toolbar and detail actions use the live backend", async ({ page, request }) => {
  const suffix = Date.now();
  const title = `E2E Materials ${suffix}`;
  const renamedTitle = `${title} Updated`;
  const fileName = `materials-${suffix}.txt`;
  const { meetingId } = await uploadTextFile(request, {
    title,
    name: fileName,
    content:
      `Browser materials workflow ${suffix}. ` +
      "The Mercury checklist contains deterministic text for parsing, preview, export, and download. " +
      "This record belongs only to the isolated end-to-end test.",
  });

  try {
    await waitForMeetingReady(request, meetingId);
    await page.goto("/materials");

    const refreshResponse = waitForApiResponse(page, "GET", "/api/v1/meetings");
    await page.getByRole("button", { name: /refresh/i }).click();
    expect((await refreshResponse).ok()).toBeTruthy();

    const search = page.getByPlaceholder(/search materials/i);
    await search.fill(title);
    const card = page.getByRole("button", { name: `Open meeting ${title}` });
    await expect(card).toBeVisible();

    const segmentedItems = page.locator(".ant-segmented-item");
    await segmentedItems.nth(1).click();
    await expect(segmentedItems.nth(1)).toHaveClass(/ant-segmented-item-selected/);
    await segmentedItems.nth(0).click();
    await expect(segmentedItems.nth(0)).toHaveClass(/ant-segmented-item-selected/);

    const sortButton = page.getByRole("button", { name: /date/i });
    const originalSortHtml = await sortButton.innerHTML();
    await sortButton.click();
    await expect.poll(() => sortButton.innerHTML()).not.toBe(originalSortHtml);

    await page.getByRole("button", { name: /new meeting/i }).click();
    const newMeetingDialog = page.getByRole("dialog");
    await expect(newMeetingDialog.getByPlaceholder(/product weekly sync/i)).toBeVisible();
    await newMeetingDialog.getByRole("button", { name: /close/i }).click();

    await page.getByRole("button", { name: /add files/i }).click();
    const addFilesDialog = page.getByRole("dialog");
    await expect(
      addFilesDialog.getByRole("combobox", { name: "Material domain", exact: true }),
    ).toBeVisible();
    await addFilesDialog.getByRole("button", { name: /close/i }).click();

    await card.click();
    const detailDialog = page.getByRole("dialog").filter({ hasText: title });
    await expect(detailDialog).toBeVisible();

    await detailDialog.getByRole("button", { name: /edit meeting info/i }).click();
    const editDialog = page.getByRole("dialog", { name: /edit meeting/i });
    const nameInput = editDialog.getByPlaceholder(/meeting title/i);
    await nameInput.fill(renamedTitle);
    const updateResponse = waitForApiResponse(page, "PUT", `/api/v1/meetings/${meetingId}`);
    await editDialog.getByRole("button", { name: /^save$/i }).click();
    expect((await updateResponse).ok()).toBeTruthy();
    await expect(detailDialog.getByRole("heading", { name: renamedTitle })).toBeVisible();

    const exportDownload = page.waitForEvent("download");
    await detailDialog.getByRole("button", { name: /export meeting/i }).click();
    await page.getByText("Export as JSON", { exact: true }).click();
    expect((await exportDownload).suggestedFilename()).toMatch(/\.json$/i);

    const fileItem = detailDialog.locator(".ant-collapse-item").filter({ hasText: fileName });
    await expect(fileItem.getByText("Not summarized")).toBeVisible();
    await fileItem.getByRole("button", { name: /view document/i }).click();
    const documentDialog = page.getByRole("dialog", { name: fileName });
    await expect(documentDialog.getByText(/mercury checklist/i)).toBeVisible({ timeout: 15_000 });
    await documentDialog.getByRole("button", { name: /close/i }).click();

    const rawDownload = page.waitForEvent("download");
    await fileItem.getByRole("button", { name: /download options/i }).click();
    await page.getByText("Download original file", { exact: true }).click();
    expect((await rawDownload).suggestedFilename()).toBe(fileName);

    const detail = await request.get(`/api/v1/meetings/${meetingId}`);
    expect(detail.ok()).toBeTruthy();
    expect(((await detail.json()) as { title: string }).title).toBe(renamedTitle);
  } finally {
    await deleteMeetingIfPresent(request, meetingId);
  }
});

test("evidence rejection requires a reason and records immutable history", async ({
  page,
  request,
}) => {
  const suffix = Date.now();
  const title = `E2E Evidence Review ${suffix}`;
  const fileName = `evidence-review-${suffix}.txt`;
  const rejectionReason = "Superseded by the approved decision log";
  const { meetingId, fileId } = await uploadTextFile(request, {
    title,
    name: fileName,
    content: "Draft proposal: ship on Monday. This proposal was not approved.",
  });

  try {
    await waitForMeetingReady(request, meetingId);
    await page.goto("/materials");
    await page.getByPlaceholder(/search materials/i).fill(title);
    await page.getByRole("button", { name: `Open meeting ${title}` }).click();

    const detailDialog = page.getByRole("dialog").filter({ hasText: title });
    const fileItem = detailDialog.locator(".ant-collapse-item").filter({ hasText: fileName });
    await fileItem.getByRole("button", { name: /edit evidence semantics/i }).click();
    await page.getByText("Rejected", { exact: true }).last().click();

    const rejectionDialog = page.getByRole("dialog", { name: /reject meeting evidence/i });
    const rejectButton = rejectionDialog.getByRole("button", { name: /reject and rebuild/i });
    await expect(rejectButton).toBeDisabled();
    await rejectionDialog.getByRole("textbox", { name: /rejection reason/i }).fill(rejectionReason);
    await expect(rejectButton).toBeEnabled();

    const updateResponse = waitForApiResponse(
      page,
      "PATCH",
      `/api/v1/meetings/${meetingId}/files/${fileId}/semantics`,
    );
    await rejectButton.click();
    const semanticResponse = await updateResponse;
    expect(semanticResponse.ok()).toBeTruthy();
    const semanticUpdate = (await semanticResponse.json()) as { source_revision: number };
    await expect(fileItem.getByText("Rejected", { exact: true })).toBeVisible();

    await fileItem.getByRole("button", { name: /review evidence history/i }).click();
    const historyDialog = page.getByRole("dialog", {
      name: new RegExp(`Evidence history.*${fileName}`),
    });
    await expect(historyDialog.getByText(rejectionReason)).toBeVisible();
    await expect(
      historyDialog.getByText(`revision ${semanticUpdate.source_revision}`, { exact: true }),
    ).toBeVisible();

    const historyResponse = await request.get(
      `/api/v1/meetings/${meetingId}/files/${fileId}/semantics/history`,
    );
    expect(historyResponse.ok()).toBeTruthy();
    const events = (await historyResponse.json()) as Array<{
      source_revision: number;
      approval_status: string;
      approval_reason: string | null;
    }>;
    expect(events[0]).toMatchObject({
      source_revision: semanticUpdate.source_revision,
      approval_status: "rejected",
      approval_reason: rejectionReason,
    });
  } finally {
    await deleteMeetingIfPresent(request, meetingId);
  }
});
