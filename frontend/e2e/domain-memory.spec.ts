import { test, expect } from "@playwright/test";
import { installReadOnlyApiMock } from "./fixtures/mock-api";

test("recorded task paging carries its revision fence and offers recovery after changes", async ({
  page,
}) => {
  await installReadOnlyApiMock(page);
  const requests: Record<string, unknown>[] = [];
  await page.route("**/api/v1/memory/facts/query", async (route) => {
    const body = route.request().postDataJSON();
    requests.push(body);
    if (body.offset)
      return route.fulfill({ status: 409, json: { detail: "Facts changed; refresh the list" } });
    return route.fulfill({
      json: {
        items: [
          {
            key: "task.release",
            value: "Ship after review",
            fact_type: "action_item",
            action_status: "open",
            project_id: "atlas",
          },
        ],
        total: 26,
        returned: 1,
        next_offset: 25,
        snapshot: "fence-v1",
        recorded_set_complete: false,
        extraction_complete: false,
      },
    });
  });
  await page.goto("/memory");
  await page.getByRole("tab", { name: "Decisions & tasks", exact: true }).click();
  await expect(page.getByText("Ship after review")).toBeVisible();
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect(page.getByText("Facts changed; refresh the list")).toBeVisible();
  expect(requests[1]).toMatchObject({ offset: 25, snapshot: "fence-v1" });
  await page.getByRole("button", { name: "Apply / refresh", exact: true }).first().click();
  await expect(page.getByText("Ship after review")).toBeVisible();
  expect(requests[2].offset).toBe(0);
  expect(requests[2].snapshot).toBeUndefined();
});

test("state comparisons show before and after values without claiming source completeness", async ({
  page,
}) => {
  await installReadOnlyApiMock(page);
  let request: Record<string, unknown> | undefined;
  await page.route("**/api/v1/memory/facts/changes", async (route) => {
    request = route.request().postDataJSON();
    return route.fulfill({
      json: {
        items: [
          {
            key: "task.release",
            kind: "changed",
            changed_fields: ["value", "action_status"],
            before: { key: "task.release", value: "Alice to ship", action_status: "open" },
            after: { key: "task.release", value: "Bob shipped", action_status: "done" },
          },
        ],
        total: 1,
        next_offset: null,
        snapshot: "v1",
        extraction_complete: false,
      },
    });
  });
  await page.goto("/memory");
  await page.getByRole("tab", { name: "State changes", exact: true }).click();
  await page.getByLabel("Before (local time)", { exact: true }).fill("2026-01-01T09:00");
  await page.getByLabel("After (local time)", { exact: true }).fill("2026-09-01T09:00");
  await page.getByRole("button", { name: "Compare states", exact: true }).click();
  await expect(page.getByText("Alice to ship")).toBeVisible();
  await expect(page.getByText("Bob shipped")).toBeVisible();
  await expect(page.getByText("1 recorded changes")).toBeVisible();
  expect(new Date(String(request?.before)).getTime()).toBeLessThan(
    new Date(String(request?.after)).getTime(),
  );
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByText("Alice to ship")).toBeVisible();
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    .toBe(true);
});
