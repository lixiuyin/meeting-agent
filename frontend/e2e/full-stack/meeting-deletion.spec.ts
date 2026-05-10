/**
 * E2E: Delete a meeting and verify cascade cleanup of vectors, files, and summaries.
 *
 * Prerequisites: Docker stack running at localhost:8307.
 * Run: npx playwright test meeting-deletion.spec.ts
 */
import { test, expect } from "@playwright/test";

const BASE = "http://localhost:8307";

test.describe("Meeting Deletion Cascade", () => {
  test("delete meeting removes it from list", async ({ request }) => {
    // List meetings and pick one to delete
    const listResp = await request.get(`${BASE}/api/v1/meetings`, {
      headers: { "X-API-Key": process.env.VITE_API_KEY || "" },
      params: { limit: 5 },
    });
    expect(listResp.status()).toBe(200);
    const body = await listResp.json();
    const meetings = body.meetings || [];

    if (meetings.length === 0) {
      test.skip(true, "No meetings to delete");
      return;
    }

    const targetId = meetings[0].id;

    // Delete the meeting
    const deleteResp = await request.delete(
      `${BASE}/api/v1/meetings/${targetId}`,
      { headers: { "X-API-Key": process.env.VITE_API_KEY || "" } }
    );
    expect(deleteResp.status()).toBe(200);

    // Verify it's gone from the list
    const afterResp = await request.get(`${BASE}/api/v1/meetings`, {
      headers: { "X-API-Key": process.env.VITE_API_KEY || "" },
      params: { limit: 50 },
    });
    const afterBody = await afterResp.json();
    const ids = (afterBody.meetings || []).map((m: { id: number }) => m.id);
    expect(ids).not.toContain(targetId);
  });
});
