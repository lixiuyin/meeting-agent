import { expect, test } from "./fixtures";
import { deleteMeetingIfPresent, uploadTextFile, waitForMeetingReady } from "./test-data";

test.setTimeout(120_000);

test("delete file button removes one test-owned file and preserves its sibling", async ({
  page,
  request,
}) => {
  const suffix = Date.now();
  const title = `E2E Delete File ${suffix}`;
  const first = await uploadTextFile(request, {
    title,
    name: `delete-first-${suffix}.txt`,
    content:
      `First disposable browser-test file ${suffix}. ` +
      "It contains enough deterministic prose for parsing, chunking, and vector indexing. " +
      "The file exists only inside isolated temporary storage and may be deleted safely.",
  });
  await waitForMeetingReady(request, first.meetingId);
  const second = await uploadTextFile(request, {
    meetingId: first.meetingId,
    name: `keep-second-${suffix}.txt`,
    content:
      `Second disposable browser-test file ${suffix}. ` +
      "This sibling contains different deterministic text and must remain after deleting the first file. " +
      "The containing meeting will be removed during final cleanup.",
  });

  try {
    await waitForMeetingReady(request, first.meetingId);
    await page.goto("/materials");
    await page.getByPlaceholder(/search materials/i).fill(title);
    await page.getByRole("button", { name: `Open meeting ${title}` }).click();

    const fileItem = page.locator(".ant-collapse-item").filter({
      hasText: `delete-first-${suffix}`,
    });
    await expect(fileItem).toBeVisible();
    await fileItem.getByRole("button", { name: /delete file/i }).click();
    const confirm = page.locator(".ant-popconfirm:visible");
    await confirm.getByRole("button", { name: /^delete$/i }).click();
    await expect(fileItem).not.toBeVisible();

    const detail = await request.get(`/api/v1/meetings/${first.meetingId}`);
    expect(detail.ok()).toBeTruthy();
    const payload = (await detail.json()) as { files: Array<{ id: number }> };
    expect(payload.files.map((file) => file.id)).toEqual([second.fileId]);
  } finally {
    await deleteMeetingIfPresent(request, first.meetingId);
  }
});
