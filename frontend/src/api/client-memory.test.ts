import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./client-core";
import { triggerDecay } from "./client-memory";

describe("memory API client", () => {
  afterEach(() => vi.restoreAllMocks());

  it("sends decay as JSON so content-type enforcement accepts it", async () => {
    const post = vi.spyOn(api, "post").mockResolvedValue({ data: { decayed_count: 0 } });

    await triggerDecay("test-user");

    expect(post).toHaveBeenCalledWith("/memory/decay", {}, { params: { user_id: "test-user" } });
  });
});
