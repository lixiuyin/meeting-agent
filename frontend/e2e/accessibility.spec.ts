import { expect, test } from "@playwright/test";
import axe from "axe-core";
import { installReadOnlyApiMock } from "./fixtures/mock-api";

const routes = ["/", "/generate", "/materials", "/history", "/memory", "/settings"];
const viewports = [
  { name: "mobile", width: 390, height: 844 },
  { name: "desktop", width: 1440, height: 1000 },
] as const;

test("all primary routes meet WCAG A/AA in light and dark themes", async ({ page }) => {
  test.setTimeout(120_000);
  await installReadOnlyApiMock(page);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.addInitScript(() => {
    if (!localStorage.getItem("meeting-agent-theme")) {
      localStorage.setItem("meeting-agent-theme", "light");
    }
  });

  for (const theme of ["light", "dark"] as const) {
    if (theme === "dark") {
      await page.evaluate(() => localStorage.setItem("meeting-agent-theme", "dark"));
    }
    for (const viewport of viewports) {
      await page.setViewportSize(viewport);
      for (const route of routes) {
        await page.goto(route);
        await page.locator("#main-content").waitFor();
        // Scan the settled UI. Axe computes effective alpha while Framer
        // opacity transitions are running, which would otherwise report a
        // transient color that users never read as the resting state.
        await page.waitForTimeout(1_000);
        await page.addScriptTag({ content: axe.source });
        const result = await page.evaluate(async () => {
          return window.axe.run(document, {
            runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa"] },
          });
        });
        expect(
          result.violations,
          `${theme}/${viewport.name}${route}: ${result.violations
            .map((violation) => `${violation.id} (${violation.nodes.length})`)
            .join(", ")}`,
        ).toEqual([]);
      }
    }
  }
});

test("desktop navigation and logo are keyboard operable", async ({ page }) => {
  await installReadOnlyApiMock(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/generate");

  const logo = page.getByRole("button", { name: "Chat" }).first();
  await logo.focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/$/);

  const chatTab = page.getByRole("tab", { name: "Chat" });
  await chatTab.focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "Generate" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/generate$/);
});

declare global {
  interface Window {
    axe: typeof axe;
  }
}
