import { expect, test } from "@playwright/test";
import { installReadOnlyApiMock } from "./fixtures/mock-api";

const fact = {
  key: "task.report",
  value: "Prepare release report",
  source: "auto_extracted",
  fact_type: "action_item",
  assertion_status: "confirmed",
  action_status: "open",
  assignee: "Alice",
  revision: 1,
  updated_at: "2026-01-01T00:00:00Z",
  project_id: "atlas",
  evidence_excerpt: "Alice will prepare the release report.",
  evidence_refs: [],
};

test("action edits use revision checks and tab changes preserve query state", async ({ page }) => {
  await installReadOnlyApiMock(page);
  let current = { ...fact };
  await page.route("**/api/v1/memory/facts/query", (route) =>
    route.fulfill({
      json: {
        items: [current],
        total: 1,
        returned: 1,
        next_offset: null,
        snapshot: "v1",
        recorded_set_complete: true,
        extraction_complete: false,
        scope: {},
      },
    }),
  );
  await page.route("**/api/v1/memory", async (route) => {
    if (route.request().method() !== "PUT") return route.fallback();
    const body = route.request().postDataJSON();
    expect(body.expected_revision).toBe(1);
    expect(body.assignee).toBe("Bob");
    current = { ...current, ...body, revision: 2 };
    return route.fulfill({ json: current });
  });
  await page.goto("/memory");
  await page.getByRole("tab", { name: "Decisions & tasks" }).click();
  await page.getByRole("button", { name: "Edit action or decision" }).click();
  const editor = page.getByRole("dialog");
  await editor.getByRole("textbox", { name: "Assignee" }).fill("Bob");
  await editor.getByRole("button", { name: "OK", exact: true }).click();
  await expect(editor).not.toBeVisible();
  await expect(page.getByText("Bob", { exact: true })).toBeVisible();
  const query = page.getByRole("textbox", { name: /Status query/i });
  await query.fill("unfinished");
  await page.getByRole("tab", { name: "State changes" }).click();
  await page.getByRole("tab", { name: "Decisions & tasks" }).click();
  await expect(query).toHaveValue("unfinished");
});

test("review shows both assertions and submits the reviewed competing revision", async ({
  page,
}) => {
  await installReadOnlyApiMock(page);
  let accepted = false;
  await page.route("**/api/v1/memory/review/query", (route) =>
    route.fulfill({
      json: {
        items: accepted
          ? []
          : [
              {
                ...fact,
                key: "candidate",
                value: "Bob will prepare the report",
                assertion_status: "disputed",
                conflicts_with: [fact.key],
              },
            ],
        conflicts: { candidate: [{ ...fact, revision: 4 }] },
        total: accepted ? 0 : 1,
        next_offset: null,
        snapshot: "workflow-review-snapshot",
        extraction_progress: { running: 1, completed: 2, unknown: 1 },
      },
    }),
  );
  await page.route("**/api/v1/memory/resolve-conflict", (route) => {
    expect(route.request().postDataJSON()).toMatchObject({
      winner_key: "candidate",
      expected_revision: 1,
      expected_conflict_revisions: { "task.report": 4 },
    });
    accepted = true;
    return route.fulfill({ json: { winner: fact, superseded_keys: [fact.key] } });
  });
  await page.goto("/memory");
  await page.getByRole("tab", { name: "Meeting review", exact: true }).click();
  await expect(page.getByText("Extracting: 1 files", { exact: true })).toBeVisible();
  await expect(page.getByText("Bob will prepare the report", { exact: true })).toBeVisible();
  await expect(page.getByText("Prepare release report", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Accept replacement" }).click();
  await expect(page.getByText("No facts awaiting review in this scope.")).toBeVisible();
  await expect(
    page.getByText("Extraction coverage unverified: 1 files", { exact: true }),
  ).toBeVisible();
});

test("unfinished preset sends explicit recorded action filters", async ({ page }) => {
  await installReadOnlyApiMock(page);
  let filtered = false;
  await page.route("**/api/v1/memory/facts/query", (route) => {
    const body = route.request().postDataJSON();
    if (body.action_status?.length === 3) {
      expect(body.action_status).toEqual(["open", "in_progress", "blocked"]);
      expect(body.fact_types).toEqual(["action_item"]);
      filtered = true;
    }
    return route.fulfill({
      json: {
        items: [],
        total: 0,
        returned: 0,
        next_offset: null,
        scope: {},
        extraction_complete: false,
      },
    });
  });
  await page.goto("/memory");
  await page.getByRole("tab", { name: "Decisions & tasks", exact: true }).click();
  await page.getByRole("button", { name: "Unfinished tasks", exact: true }).click();
  await expect.poll(() => filtered).toBe(true);
});

test("project and review tab survive a reload", async ({ page }) => {
  await installReadOnlyApiMock(page);
  await page.route("**/api/v1/memory/projects", (route) =>
    route.fulfill({
      json: [{ project_id: "atlas", name: "Atlas", aliases: [], file_ids: [], revision: 1 }],
    }),
  );
  await page.route("**/api/v1/memory/review/query", (route) => {
    expect(route.request().postDataJSON().project_id).toBe("atlas");
    return route.fulfill({
      json: {
        items: [],
        conflicts: {},
        total: 0,
        next_offset: null,
        snapshot: "workflow-project-review-snapshot",
        extraction_progress: { unknown: 1 },
      },
    });
  });
  await page.goto("/memory?project=atlas&projectTab=review");
  await expect(
    page.getByText("Extraction coverage unverified: 1 files", { exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByText("Extraction coverage unverified: 1 files", { exact: true }),
  ).toBeVisible();
});

test("mobile action editor stays within the viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installReadOnlyApiMock(page);
  await page.route("**/api/v1/memory/facts/query", (route) =>
    route.fulfill({
      json: {
        items: [fact],
        total: 1,
        returned: 1,
        next_offset: null,
        snapshot: "v1",
        recorded_set_complete: true,
        extraction_complete: false,
        scope: {},
      },
    }),
  );
  await page.goto("/memory");
  await page.getByRole("combobox", { name: "Memory", exact: true }).click();
  await page
    .locator(".ant-select-dropdown")
    .getByText("Decisions & tasks", { exact: true })
    .click();
  await page.getByRole("button", { name: "Edit action or decision" }).click();
  const editor = page.getByRole("dialog");
  await expect(editor.getByRole("textbox", { name: "Assignee" })).toBeVisible();
  const bounds = await editor.boundingBox();
  expect(bounds).not.toBeNull();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
  await editor.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(editor).not.toBeVisible();
});

test("mobile meeting review keeps a long queue inside its own scroll region", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installReadOnlyApiMock(page);
  const items = Array.from({ length: 25 }, (_, index) => ({
    ...fact,
    key: `task.mobile-review.${index}`,
    value: `Review item ${index} with a long but wrapping description`,
  }));
  await page.route("**/api/v1/memory/review/query", (route) =>
    route.fulfill({
      json: {
        items,
        conflicts: {},
        total: items.length,
        next_offset: null,
        snapshot: "mobile-review-snapshot",
        extraction_progress: {},
      },
    }),
  );

  await page.goto("/memory?memoryTab=review");
  await expect(page.getByText("25 facts awaiting review", { exact: true })).toBeVisible();
  const geometry = await page.evaluate(() => {
    const panel = document.querySelector<HTMLElement>(".meeting-review-panel")!;
    const scroll = document.querySelector<HTMLElement>(".meeting-review-scroll-region")!;
    const bounds = panel.getBoundingClientRect();
    return {
      bodyWidth: document.documentElement.scrollWidth,
      panelBottom: bounds.bottom,
      scrollClientHeight: scroll.clientHeight,
      scrollHeight: scroll.scrollHeight,
    };
  });
  expect(geometry.bodyWidth).toBeLessThanOrEqual(390);
  expect(geometry.panelBottom).toBeLessThanOrEqual(844);
  expect(geometry.scrollHeight).toBeGreaterThan(geometry.scrollClientHeight);
  await expect(page.getByRole("button", { name: "Refresh" })).toBeVisible();
});

test("meeting preparation keeps tasks and recent changes in the selected project", async ({
  page,
}, testInfo) => {
  await installReadOnlyApiMock(page);
  await page.route("**/api/v1/memory/projects", (route) =>
    route.fulfill({
      json: [{ project_id: "atlas", name: "Atlas", aliases: [], file_ids: [], revision: 1 }],
    }),
  );
  let tasksLoaded = false;
  let changesLoaded = false;
  await page.route("**/api/v1/memory/facts/query", (route) => {
    expect(route.request().postDataJSON()).toMatchObject({
      project_id: "atlas",
      fact_types: ["action_item"],
      action_status: ["open", "in_progress", "blocked"],
    });
    tasksLoaded = true;
    return route.fulfill({
      json: {
        items: [fact],
        total: 1,
        returned: 1,
        next_offset: null,
        scope: {},
        extraction_complete: false,
      },
    });
  });
  await page.route("**/api/v1/memory/facts/changes", (route) => {
    const body = route.request().postDataJSON();
    expect(body.project_id).toBe("atlas");
    expect(new Date(body.after).getTime() - new Date(body.before).getTime()).toBe(7 * 86400000);
    changesLoaded = true;
    return route.fulfill({ json: { items: [], total: 0, next_offset: null, scope: {} } });
  });
  await page.goto("/memory?project=atlas&projectTab=preparation");
  await expect(page.getByRole("tab", { name: "Meeting preparation" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect.poll(() => tasksLoaded).toBe(true);
  await page
    .getByRole("button", { name: "collapsed Changes in the last 7 days", exact: true })
    .click();
  await expect.poll(() => changesLoaded).toBe(true);
  await expect(page.getByText("Prepare release report", { exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("preparation-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByText("Prepare release report", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.screenshot({ path: testInfo.outputPath("preparation-mobile.png"), fullPage: true });
});
