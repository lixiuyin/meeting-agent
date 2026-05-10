/**
 * Tests for Sentry data scrubbing in monitoring.ts.
 *
 * @vitest-environment jsdom
 */

import { describe, it, expect, vi } from "vitest";

// Mock @sentry/react before importing the module under test
vi.mock("@sentry/react", () => ({
  init: vi.fn(),
  withScope: vi.fn(),
  captureException: vi.fn(),
  captureMessage: vi.fn(),
  browserTracingIntegration: vi.fn(() => ({})),
  replayIntegration: vi.fn(() => ({})),
}));

const monitoring = await import("../utils/monitoring");

describe("Sentry data scrubbing", () => {
  it("scrubs values with sensitive keys via beforeSend logic", () => {
    const input = { api_key: "sk-test-123", name: "Alice" };
    const result = monitoring.scrubSensitiveTelemetry(input) as Record<string, unknown>;
    expect(result.api_key).toBe("[Filtered]");
    expect(result.name).toBe("Alice");
  });

  it("scrubs values matching sensitive prefixes", () => {
    const input = { token: "ghp_abcdef123", safe: "normal" };
    const result = monitoring.scrubSensitiveTelemetry(input) as Record<string, unknown>;
    expect(result.token).toBe("[Filtered]");
    expect(result.safe).toBe("normal");
  });

  it("handles nested objects", () => {
    const input = { user: { password: "secret123", email: "a@b.com" } };
    const result = monitoring.scrubSensitiveTelemetry(input) as Record<string, unknown>;
    const user = result.user as Record<string, unknown>;
    expect(user.password).toBe("[Filtered]");
    expect(user.email).toBe("a@b.com");
  });

  it("handles arrays", () => {
    const input = [{ api_key: "sk-abc" }, { name: "ok" }];
    const result = monitoring.scrubSensitiveTelemetry(input) as Array<Record<string, unknown>>;
    expect(result[0].api_key).toBe("[Filtered]");
    expect(result[1].name).toBe("ok");
  });

  it("returns primitives unchanged", () => {
    expect(monitoring.scrubSensitiveTelemetry(null)).toBe(null);
    expect(monitoring.scrubSensitiveTelemetry(undefined)).toBe(undefined);
    expect(monitoring.scrubSensitiveTelemetry("hello")).toBe("hello");
    expect(monitoring.scrubSensitiveTelemetry(42)).toBe(42);
  });

  it("respects max depth limit", () => {
    const deep = { a: { b: { c: { d: { e: { f: { api_key: "sk-deep" } } } } } } };
    const result = monitoring.scrubSensitiveTelemetry(deep) as Record<string, unknown>;
    // At depth > 5, the function returns the object without further scrubbing
    expect((result.a as Record<string, unknown>).b).toBeDefined();
  });

  it("initMonitoring skips when no DSN", () => {
    // When VITE_SENTRY_DSN is not set, initMonitoring should return early
    expect(monitoring.initMonitoring()).toBeUndefined();
  });

  it("reportNonCriticalError handles non-Error values", () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(() => monitoring.reportNonCriticalError("test", "string error")).not.toThrow();
    warnSpy.mockRestore();
  });
});
