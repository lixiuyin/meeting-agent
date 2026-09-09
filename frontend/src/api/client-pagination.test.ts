import type { AxiosResponse } from "axios";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "./client-core";
import {
  exportAllMemories,
  listAllEntities,
  listAllMemories,
  recordMemoryFeedback,
  type EntityItem,
  type MemoryItem,
} from "./client-memory";
import { listAllSessionSummaries, type SessionSummaryItem } from "./client-sessions";

function axiosResponse<T>(data: T): AxiosResponse<T> {
  return { data } as AxiosResponse<T>;
}

describe("cursor pagination helpers", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("loads every memory page and forwards the cursor", async () => {
    const first = { key: "first" } as MemoryItem;
    const second = { key: "second" } as MemoryItem;
    const get = vi
      .spyOn(api, "get")
      .mockResolvedValueOnce(
        axiosResponse({ items: [first], memories: [first], total: 2, next_cursor: "cursor-1" }),
      )
      .mockResolvedValueOnce(
        axiosResponse({ items: [second], memories: [second], total: 2, next_cursor: null }),
      );

    await expect(listAllMemories("user", { includeExpired: true })).resolves.toEqual([
      first,
      second,
    ]);
    expect(get.mock.calls[1][1]?.params).toMatchObject({ cursor: "cursor-1", limit: 100 });
  });

  it("exports every page instead of silently downloading the first page", async () => {
    const first = { key: "first" } as MemoryItem;
    const second = { key: "second" } as MemoryItem;
    vi.spyOn(api, "get")
      .mockResolvedValueOnce(
        axiosResponse({ user_id: "user", memories: [first], total: 2, next_cursor: "next" }),
      )
      .mockResolvedValueOnce(
        axiosResponse({ user_id: "user", memories: [second], total: 2, next_cursor: null }),
      );

    await expect(exportAllMemories("user")).resolves.toEqual([first, second]);
  });

  it("loads every entity and session-summary page", async () => {
    const entity = { name: "entity" } as EntityItem;
    const summary = { session_id: "session-1" } as SessionSummaryItem;
    const get = vi
      .spyOn(api, "get")
      .mockResolvedValueOnce(axiosResponse({ entities: [entity], total: 1, next_cursor: null }))
      .mockResolvedValueOnce(
        axiosResponse({ summaries: [summary], items: [summary], total: 1, next_cursor: null }),
      );

    await expect(listAllEntities("user")).resolves.toEqual([entity]);
    await expect(listAllSessionSummaries("user")).resolves.toEqual([
      expect.objectContaining({ session_id: "session-1", topics: [], decisions: [] }),
    ]);
    expect(get).toHaveBeenCalledTimes(2);
  });

  it("sends retry-safe memory feedback", async () => {
    const post = vi.spyOn(api, "post").mockResolvedValue(
      axiosResponse({
        message: "Memory feedback recorded",
        key: "project-owner",
        usefulness_score: 0,
        usefulness_count: 1,
      }),
    );

    await recordMemoryFeedback("project-owner", false);

    expect(post).toHaveBeenCalledWith(
      "/memory/feedback",
      { key: "project-owner", useful: false },
      { headers: { "Idempotency-Key": expect.any(String) } },
    );
  });
});
