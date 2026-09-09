import { expect, test } from "./fixtures";

test("primary navigation, theme, new-chat, and filter controls work against the live app", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByText("Online")).toBeVisible({ timeout: 15_000 });

  const darkMode = page.getByRole("button", { name: /switch to dark mode/i });
  await darkMode.click();
  await expect(page.getByRole("button", { name: /switch to light mode/i })).toBeVisible();
  await page.getByRole("button", { name: /switch to light mode/i }).click();
  await expect(darkMode).toBeVisible();

  const routes = [
    ["Generate", "/generate"],
    ["Materials", "/materials"],
    ["History", "/history"],
    ["Memory", "/memory"],
    ["Settings", "/settings"],
    ["Chat", "/"],
  ] as const;
  for (const [name, pathname] of routes) {
    await page.getByRole("tab", { name: new RegExp(name, "i") }).click();
    await expect(page).toHaveURL(new RegExp(`${pathname === "/" ? "/$" : `${pathname}$`}`));
  }

  await page.getByRole("button", { name: /new chat/i }).click();
  await expect(page.getByPlaceholder(/ask anything about your meetings/i)).toHaveValue("");

  const filtersButton = page.getByRole("button", { name: /modes & filters/i });
  await filtersButton.click();
  await expect(page.getByText("Retrieval engine", { exact: true })).toBeVisible();
  await expect(page.getByText("RAG mode", { exact: true })).toBeVisible();
  await expect(page.getByText("Memory mode", { exact: true })).toBeVisible();
  await expect(page.getByText("Web search", { exact: true })).toBeVisible();
  await filtersButton.click();
  await expect(page.getByText("Retrieval engine", { exact: true })).not.toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  const meetingSelect = page.getByRole("combobox", { name: /select meetings/i });
  const fileSelect = page.getByRole("combobox", { name: /select files/i });
  await expect(meetingSelect).toBeVisible();
  await expect(fileSelect).toBeVisible();
  for (const control of [meetingSelect, fileSelect]) {
    const box = await control.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(390);
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
});
