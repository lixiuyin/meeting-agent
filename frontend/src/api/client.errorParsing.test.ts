import { describe, expect, it } from "vitest";

import { ApiError, formatApiErrorMessage, parseApiErrorPayload } from "./client";

describe("parseApiErrorPayload", () => {
  it("parses backend unified error envelope", () => {
    const parsed = parseApiErrorPayload({
      code: "HTTP_404",
      message: "Session not found",
      request_id: "req-abc",
      details: { session_id: "s1" },
    });

    expect(parsed).toEqual({
      message: "Session not found",
      code: "HTTP_404",
      requestId: "req-abc",
      details: { session_id: "s1" },
    });
  });

  it("falls back to legacy detail field", () => {
    const parsed = parseApiErrorPayload({ detail: "Legacy detail message" });
    expect(parsed).toEqual({ message: "Legacy detail message" });
  });

  it("falls back to provided default message", () => {
    const parsed = parseApiErrorPayload({ foo: "bar" }, "Network error");
    expect(parsed).toEqual({ message: "Network error" });
  });
});

describe("formatApiErrorMessage", () => {
  it("appends request id for ApiError", () => {
    const err = new ApiError(500, "Server unavailable", { requestId: "req-xyz" });
    expect(formatApiErrorMessage(err, "Fallback")).toBe("Server unavailable (Request ID: req-xyz)");
  });

  it("returns fallback for unknown values", () => {
    expect(formatApiErrorMessage(null, "Fallback")).toBe("Fallback");
  });
});
