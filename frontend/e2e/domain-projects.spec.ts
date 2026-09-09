import { expect, test } from "@playwright/test";
import { installReadOnlyApiMock } from "./fixtures/mock-api";

test("project bindings are editable without leaving the Memory workspace", async ({ page }) => {
  await installReadOnlyApiMock(page);
  let saved: Record<string, unknown> | undefined;
  await page.route("**/api/v1/memory/projects", async (route) => {
    if (route.request().method() === "PUT") {
      saved = route.request().postDataJSON();
      return route.fulfill({ json: { project_id: "atlas" } });
    }
    return route.fulfill({ json: saved ? [saved] : [] });
  });
  await page.route("**/api/v1/memory/projects/materials**", (route) =>
    route.fulfill({
      json: [{ id: 9, file_name: "minutes.pdf", meeting_id: 7, meeting_title: "Atlas meeting" }],
    }),
  );
  await page.goto("/memory");
  await page.getByRole("tab", { name: "Projects", exact: true }).click();
  await page.getByRole("button", { name: "Manage project bindings" }).click();
  await page.getByRole("textbox", { name: "Project ID", exact: true }).fill("atlas");
  await page.getByRole("textbox", { name: "Project name", exact: true }).fill("Atlas");
  await page
    .getByRole("combobox", { name: "Materials (search by meeting or file)", exact: true })
    .click();
  await page.getByText("Atlas meeting / minutes.pdf", { exact: true }).click();
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Save project and material bindings" }).click();
  await expect(page.getByText("Project saved", { exact: true })).toBeVisible();
  expect(saved).toEqual({
    project_id: "atlas",
    name: "Atlas",
    aliases: [],
    file_ids: [9],
    expected_revision: 0,
  });
  await expect(page).toHaveURL(
    (url) => url.pathname === "/memory" && url.searchParams.get("memoryTab") === "projects",
  );
});

test("a stale project editor keeps local edits and offers explicit reload", async ({ page }) => {
  await installReadOnlyApiMock(page);
  let revision = 1;
  await page.route("**/api/v1/memory/projects", async (route) => {
    const current = {
      project_id: "atlas",
      name: revision === 1 ? "Atlas" : "Remote",
      aliases: [],
      file_ids: [],
      revision,
    };
    if (route.request().method() === "PUT") {
      expect(route.request().postDataJSON().expected_revision).toBe(1);
      revision = 2;
      return route.fulfill({
        status: 409,
        json: {
          code: "HTTP_409",
          message: "Project changed",
          details: { current: { ...current, name: "Remote", revision } },
        },
      });
    }
    return route.fulfill({ json: [current] });
  });
  await page.goto("/memory");
  await page.getByRole("tab", { name: "Projects", exact: true }).click();
  await page.getByRole("button", { name: "Manage project bindings" }).click();
  await page.getByRole("combobox", { name: "Edit an existing project or create below" }).click();
  await page.getByText("Atlas", { exact: true }).last().click();
  await page.getByRole("textbox", { name: "Project name", exact: true }).fill("Local");
  await page.getByRole("button", { name: "Save project and material bindings" }).click();
  await expect(page.getByText(/Your unsaved edits are retained/)).toBeVisible();
  await expect(page.getByRole("textbox", { name: "Project name", exact: true })).toHaveValue(
    "Local",
  );
  await page.getByRole("button", { name: "Discard local edits and load latest" }).click();
  await expect(page.getByRole("textbox", { name: "Project name", exact: true })).toHaveValue(
    "Remote",
  );
});
