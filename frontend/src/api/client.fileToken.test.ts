import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "./client-core";
import { cleanupFileToken, initFileToken, prefetchMeetingFileUrl } from "./client-meetings";

describe("file token refresh", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    cleanupFileToken();
  });

  afterEach(() => {
    cleanupFileToken();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("deduplicates concurrent application warmups", async () => {
    const post = vi.spyOn(api, "post").mockResolvedValue({ data: { token: "token" } });

    await Promise.all([initFileToken(), initFileToken()]);

    expect(post).toHaveBeenCalledTimes(1);
  });

  it("waits for the rate-limit window before retrying a 429", async () => {
    const post = vi
      .spyOn(api, "post")
      .mockRejectedValueOnce(new ApiError(429, "Too many requests"))
      .mockResolvedValue({ data: { token: "token" } });

    await initFileToken();
    await vi.advanceTimersByTimeAsync(59_999);
    expect(post).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(1);
    expect(post).toHaveBeenCalledTimes(2);
  });

  it("deduplicates concurrent signed URL requests for the same file", async () => {
    const post = vi.spyOn(api, "post").mockResolvedValue({
      data: { url: "/api/v1/meetings/7/files/9?token=signed", expires_at: 4_000_000_000 },
    });

    await Promise.all([
      prefetchMeetingFileUrl(7, 9),
      prefetchMeetingFileUrl(7, 9),
      prefetchMeetingFileUrl(7, 9),
    ]);

    expect(post).toHaveBeenCalledTimes(1);
  });
});
