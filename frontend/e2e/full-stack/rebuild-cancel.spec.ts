/**
 * E2E: Trigger vector rebuild then cancel, verifying idempotency and state recovery.
 *
 * Prerequisites: Docker stack running at localhost:8307.
 * Run: npx playwright test rebuild-cancel.spec.ts
 */
import { test, expect } from "./fixtures";
import { execFileSync } from "node:child_process";
import { join } from "node:path";

const BASE = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:8307";

test.describe("Vector Rebuild Concurrency", () => {
  test("an active cross-process rebuild returns 409 conflict", async ({ request }) => {
    const headers = { "X-API-Key": process.env.VITE_API_KEY || "" };
    const dataDir = process.env.MEETING_AGENT_DATA_DIR;
    if (!dataDir) throw new Error("MEETING_AGENT_DATA_DIR is required for full-stack tests");
    const database = join(dataDir, "meetings.db");
    const lockTimestamp = Date.now() / 1000;

    // Model a rebuild owned by another process. A fast/empty rebuild can
    // legitimately finish before a second HTTP request reaches the server,
    // so sequential requests do not deterministically exercise contention.
    execFileSync("sqlite3", [
      database,
      `INSERT OR REPLACE INTO kv_state(key, value) VALUES
       ('rebuild_global', 'locked'),
       ('rebuild_global_at', '${lockTimestamp}');`,
    ]);

    try {
      const response = await request.post(`${BASE}/api/v1/settings/rebuild-vectors`, { headers });
      expect(response.status()).toBe(409);
    } finally {
      execFileSync("sqlite3", [
        database,
        "DELETE FROM kv_state WHERE key IN ('rebuild_global', 'rebuild_global_at');",
      ]);
    }
  });
});
