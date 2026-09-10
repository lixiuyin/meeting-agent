import { expect, test, type Page } from "@playwright/test";
import axe from "axe-core";
import { installReadOnlyApiMock } from "./fixtures/mock-api";

const routes = ["/", "/generate", "/materials", "/history", "/memory", "/settings"];
const viewports = [
  { name: "mobile", width: 390, height: 844 },
  { name: "desktop", width: 1440, height: 1000 },
] as const;

async function waitForSettledUi(page: Page, route: string) {
  if (route === "/") {
    // The application shell is ready before asynchronous chat restoration has
    // swapped its loading view for the home screen.
    await expect(page.getByRole("button", { name: "New Chat" })).toBeVisible();
  }
  await expect
    .poll(
      () =>
        page.locator("#main-content").evaluate((root) => {
          const candidates = root.querySelectorAll<HTMLElement>(
            "button, input, textarea, select, h1, h2, h3, p, .ant-select-placeholder",
          );
          return [...candidates].every((candidate) => {
            let ancestor = candidate.parentElement;
            while (ancestor && ancestor !== root) {
              // Framer keeps opacity transitions for reduced-motion users.
              // Axe must inspect the resting colors, not an effective color
              // blended through a partially transparent parent.
              if (ancestor.style.opacity && Number(getComputedStyle(ancestor).opacity) < 0.99) {
                return false;
              }
              ancestor = ancestor.parentElement;
            }
            return true;
          });
        }),
      { timeout: 15_000 },
    )
    .toBe(true);
}

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
        await waitForSettledUi(page, route);
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
