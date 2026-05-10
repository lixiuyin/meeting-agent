import { expect, test } from "@playwright/test";

test.setTimeout(120_000);

test("upload -> index -> ask -> verify", async ({ page, request }) => {
  const meetingTitle = `E2E Sample Meeting ${Date.now()}`;
  const uploadResponse = await request.post("/api/v1/meetings/upload", {
    multipart: {
      title: meetingTitle,
      file: {
        name: "sample.txt",
        mimeType: "text/plain",
        buffer: Buffer.from("This is a sample meeting note for full-stack E2E verification."),
      },
    },
  });
  expect(uploadResponse.ok()).toBeTruthy();
  const uploadPayload = await uploadResponse.json();
  const meetingId = uploadPayload.meeting_id as number;

  try {
    await expect
      .poll(
        async () => {
          const detail = await request.get(`/api/v1/meetings/${meetingId}`);
          if (!detail.ok()) return "missing";
          const payload = await detail.json();
          return payload.status;
        },
        { timeout: 60_000, interval: 2_000 },
      )
      .toBe("ready");

    await expect
      .poll(
        async () => {
          const list = await request.get("/api/v1/meetings?status=ready&limit=100");
          if (!list.ok()) return false;
          const payload = await list.json();
          return (payload.meetings ?? []).some((m: { id: number }) => m.id === meetingId);
        },
        { timeout: 30_000, interval: 2_000 },
      )
      .toBeTruthy();

    await page.goto("/");
    await expect(page.getByPlaceholder(/ask anything about your meetings/i)).toBeVisible();

    const meetingSelect = page.getByRole("combobox").first();
    await expect(meetingSelect).toBeVisible();
    await meetingSelect.click();
    await meetingSelect.fill(meetingTitle);
    const meetingOption = page
      .locator(".ant-select-dropdown:visible .ant-select-item-option")
      .filter({ hasText: meetingTitle })
      .first();
    await expect(meetingOption).toBeVisible({ timeout: 15_000 });
    await meetingOption.click();
    // Antd multi-mode keeps the dropdown open after selection — close it so
    // subsequent assertions don't collide with the still-visible option row.
    await page.keyboard.press("Escape");
    await expect(
      page.locator(".ant-tag").filter({ hasText: meetingTitle }),
    ).toBeVisible();

    await page
      .getByPlaceholder(/ask anything about your meetings/i)
      .fill("Summarize sample meeting");
    const streamReq = page.waitForResponse(
      (resp) => resp.url().includes("/api/v1/chat/stream") && resp.status() === 200,
      { timeout: 60_000 },
    );
    await page.keyboard.press("Enter");
    await streamReq;
    await expect(page.getByPlaceholder(/ask anything about your meetings/i)).toHaveValue("");
    await expect(page.getByRole("heading", { name: /how can i help you today/i })).not.toBeVisible();
  } finally {
    await request.delete(`/api/v1/meetings/${meetingId}`);
  }
});
