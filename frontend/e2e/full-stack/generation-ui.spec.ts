import { expect, test, type Page } from "./fixtures";
import { deleteMeetingIfPresent, uploadTextFile, waitForMeetingReady } from "./test-data";

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

test("generation controls create and invoke a test-owned skill", async ({
  page,
  request,
  context,
}) => {
  const browserErrors: string[] = [];
  page.on("pageerror", (error) => browserErrors.push(error.message));
  page.on("console", (entry) => {
    if (entry.type() === "error") browserErrors.push(entry.text());
  });
  const suffix = Date.now();
  const title = `E2E Generation ${suffix}`;
  const rawSkillName = `e2e_brief_${suffix}`;
  const skillName = `${rawSkillName}_generator`;
  const displayName = `E2E Mercury Brief ${suffix}`;
  const { meetingId } = await uploadTextFile(request, {
    title,
    name: `generation-${suffix}.txt`,
    content:
      "Mercury rollout verification meeting. The launch owner is Dana Wu. " +
      "The immutable release token is MERCURY-913. The launch date is November 8, 2032. " +
      "The output must preserve these facts and identify the owner explicitly.",
  });

  try {
    await waitForMeetingReady(request, meetingId);
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await page.goto("/generate");

    await page.getByRole("button", { name: /create/i }).click();
    const createDialog = page.getByRole("dialog", { name: /create skill/i });
    await createDialog.getByRole("button", { name: /^create$/i }).click();
    await expect(createDialog.getByText(/please input a skill name/i)).toBeVisible();
    await expect(createDialog.getByText(/please input a display name/i)).toBeVisible();
    await expect(createDialog.getByText(/please input a description/i)).toBeVisible();
    await expect.poll(() => browserErrors).toEqual([]);
    await createDialog.getByLabel(/skill name/i).fill(rawSkillName);
    await createDialog.getByLabel(/display name/i).fill(displayName);
    await createDialog
      .getByLabel(/description/i)
      .fill("Generate a concise verification brief from the selected meeting evidence.");
    await createDialog.getByLabel(/required keywords/i).fill("verification, launch");
    await createDialog.getByLabel(/optional keywords/i).fill("owner, release token");
    await createDialog.getByLabel(/examples/i).fill("Create a verification brief");
    const createResponse = waitForApiResponse(page, "POST", "/api/v1/skills");
    await createDialog.getByRole("button", { name: /^create$/i }).click();
    expect((await createResponse).status()).toBe(201);
    await expect(page.getByText(displayName, { exact: true })).toBeVisible({ timeout: 15_000 });

    const meetingSelect = page.getByRole("combobox");
    await meetingSelect.click();
    await page
      .locator(".ant-select-dropdown:visible .ant-select-item-option")
      .filter({ hasText: title })
      .click();
    await page
      .getByPlaceholder(/add specific instructions/i)
      .fill("State the release token, owner, and date in three bullets with citations.");

    const invokeResponse = waitForApiResponse(page, "POST", "/api/v1/skills/invoke");
    const configCard = page.locator(".ant-card").filter({ hasText: "Additional Instructions" });
    await configCard.getByRole("button", { name: /generate/i }).click();
    const invocation = await invokeResponse;
    expect(invocation.ok(), await invocation.text()).toBeTruthy();
    const payload = (await invocation.json()) as { content: string; skill_name: string };
    expect(payload.skill_name).toBe(skillName);
    expect(payload.content.length).toBeGreaterThan(20);
    await expect(page.locator(".markdown-body")).not.toBeEmpty();

    const outputCard = page.locator(".ant-card").filter({ has: page.locator(".markdown-body") });
    await outputCard.getByRole("button", { name: /copy/i }).click();
    await expect(page.getByText("Copied", { exact: true })).toBeVisible();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toContain(
      payload.content.slice(0, 20),
    );

    const downloadPromise = page.waitForEvent("download");
    await outputCard.getByRole("button", { name: /download/i }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toContain(skillName);
    expect(download.suggestedFilename()).toMatch(/\.md$/i);
  } finally {
    await deleteMeetingIfPresent(request, meetingId);
  }
});
