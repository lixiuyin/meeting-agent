import { expect, test } from "@playwright/test";
import axe from "axe-core";

test("persisted expanded history session loads its messages automatically", async ({ page }) => {
  let messageRequests = 0;
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.addInitScript(() => {
    localStorage.setItem("history-expanded-session-id", "persisted-session");
  });
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/api/v1/health") return json({ status: "healthy" });
    if (path === "/api/v1/meetings/file-token") return json({ token: "e2e-file-token" });
    if (path === "/api/v1/ws/token") return json({}, 503);
    if (path === "/api/v1/sessions") {
      return json({
        items: [
          {
            id: "persisted-session",
            user_id: "default",
            title: "Persisted conversation",
            created_at: "2026-09-05T10:00:00Z",
            updated_at: "2026-09-05T10:01:00Z",
          },
        ],
        sessions: [],
        total: 1,
        next_cursor: null,
      });
    }
    if (path === "/api/v1/sessions/persisted-session/messages") {
      messageRequests += 1;
      return json({
        session: { id: "persisted-session" },
        messages: [
          { id: 1, role: "human", content: "restored question", sources: [] },
          { id: 2, role: "ai", content: "restored answer", sources: [] },
        ],
        total: 2,
        next_before_id: null,
      });
    }
    if (path === "/api/v1/sessions/persisted-session/continuation-preview") {
      return json({
        scope: { meeting_ids: [7], file_ids: [9] },
        files: [{ file_id: 9, file_name: "Revised minutes.pdf", status: "changed" }],
        memory_changes: [{ key: "task.release", status: "inactive" }],
        open_questions: ["Who owns release?"],
        saved_snapshot_available: true,
        checkpoint_available: true,
        messages_since_checkpoint: 2,
      });
    }
    return json({ detail: `Unhandled E2E route: ${path}` }, 501);
  });

  await page.goto("/history");
  await expect(page.getByText("restored question")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText("restored answer")).toBeVisible();
  expect(messageRequests).toBe(1);
  await page.getByText("Review context before continuing", { exact: true }).click();
  await expect(
    page.getByTestId("continuation-source-change").filter({ hasText: "Revised minutes.pdf" }),
  ).toBeVisible();
  await expect(page.getByText("Who owns release?")).toBeVisible();
  // Axe must inspect the resting colors rather than alpha-composited colors
  // from the page entrance transition.
  await page.waitForTimeout(1_000);
  await page.addScriptTag({ content: axe.source });
  const result = await page.evaluate(async () =>
    window.axe.run(document, {
      runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa"] },
    }),
  );
  expect(result.violations, result.violations.map((violation) => violation.id).join(", ")).toEqual(
    [],
  );
});
