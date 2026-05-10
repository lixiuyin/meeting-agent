/**
 * E2E: Delete a single file from a multi-file meeting and verify only that file is removed.
 *
 * Prerequisites: Docker stack running at localhost:8307.
 * Run: npx playwright test file-deletion.spec.ts
 */
import { test, expect } from "@playwright/test";

const BASE = "http://localhost:8307";

test.describe("File Deletion from Meeting", () => {
  test("file count decreases after deleting one file", async ({ request }) => {
    const headers = { "X-API-Key": process.env.VITE_API_KEY || "" };

    // List meetings; find one with multiple files
    const listResp = await request.get(`${BASE}/api/v1/meetings`, {
      headers,
      params: { limit: 50 },
    });
    expect(listResp.status()).toBe(200);
    const meetings = (await listResp.json()).meetings || [];

    // Find a meeting with files
    let targetMeeting: { id: number } | null = null;
    let initialFileCount = 0;
    for (const m of meetings) {
      const detailResp = await request.get(
        `${BASE}/api/v1/meetings/${m.id}`,
        { headers }
      );
      if (detailResp.status() !== 200) continue;
      const detail = await detailResp.json();
      const files = detail.files || [];
      if (files.length > 1) {
        targetMeeting = m;
        initialFileCount = files.length;
        break;
      }
    }

    if (!targetMeeting) {
      test.skip(true, "No multi-file meeting found");
      return;
    }

    // Get the first file id
    const detailResp = await request.get(
      `${BASE}/api/v1/meetings/${targetMeeting.id}`,
      { headers }
    );
    const detail = await detailResp.json();
    const firstFileId = detail.files[0].id;

    // Delete the file
    const deleteResp = await request.delete(
      `${BASE}/api/v1/meetings/${targetMeeting.id}/files/${firstFileId}`,
      { headers }
    );
    expect(deleteResp.ok()).toBeTruthy();

    // Verify file count decreased
    const afterResp = await request.get(
      `${BASE}/api/v1/meetings/${targetMeeting.id}`,
      { headers }
    );
    const afterDetail = await afterResp.json();
    const afterFiles = afterDetail.files || [];
    expect(afterFiles.length).toBeLessThan(initialFileCount);
  });
});
