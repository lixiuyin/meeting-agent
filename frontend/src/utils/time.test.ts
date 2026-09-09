import { afterEach, describe, expect, it, vi } from "vitest";

import { formatRelativeLocalTime, toLocalDateTimeInput } from "./time";

describe("toLocalDateTimeInput", () => {
  it("round-trips an API instant through datetime-local without timezone drift", () => {
    const original = "2026-09-01T00:00:00.000Z";
    const localInput = toLocalDateTimeInput(original);

    expect(localInput).toBeDefined();
    expect(new Date(localInput!).toISOString()).toBe(original);
  });

  it("returns undefined for missing or invalid values", () => {
    expect(toLocalDateTimeInput(undefined)).toBeUndefined();
    expect(toLocalDateTimeInput("invalid")).toBeUndefined();
  });
});

describe("relative local calendar dates", () => {
  afterEach(() => vi.useRealTimers());

  it("labels the previous local calendar day yesterday even across UTC midnight", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 8, 8, 0, 20));
    expect(formatRelativeLocalTime(new Date(2026, 8, 7, 23, 55).toISOString())).toBe(
      "Yesterday at 23:55",
    );
    expect(formatRelativeLocalTime(new Date(2026, 8, 8, 0, 10).toISOString())).toBe(
      "Today at 00:10",
    );
  });

  it("preserves invalid input", () => {
    expect(formatRelativeLocalTime("invalid")).toBe("invalid");
  });
});
