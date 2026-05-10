import { describe, it, expect, beforeEach, afterEach } from "vitest";

// We can't directly import the private anySignal function from client-chat,
// so we replicate its logic here for testing (it must stay in sync).
// The real coverage comes from integration tests hitting sendChatStream.
function anySignal(signals: AbortSignal[]): AbortSignal {
  if (typeof AbortSignal.any === "function") return AbortSignal.any(signals);
  const controller = new AbortController();
  for (const s of signals) {
    if (s.aborted) {
      controller.abort(s.reason);
      return controller.signal;
    }
    s.addEventListener("abort", () => controller.abort(s.reason), { once: true });
  }
  return controller.signal;
}

describe("anySignal polyfill", () => {
  const originalAny = AbortSignal.any;

  beforeEach(() => {
    // Force the polyfill path by temporarily removing native any
    // @ts-expect-error -- intentionally deleting for test
    delete AbortSignal.any;
  });

  afterEach(() => {
    // Restore
    AbortSignal.any = originalAny;
  });

  it("returns an aborted signal when any input is already aborted", () => {
    const c1 = new AbortController();
    const c2 = new AbortController();
    c2.abort("already done");

    const combined = anySignal([c1.signal, c2.signal]);
    expect(combined.aborted).toBe(true);
    expect(combined.reason).toBe("already done");
  });

  it("aborts when first signal aborts", () => {
    const c1 = new AbortController();
    const c2 = new AbortController();
    const combined = anySignal([c1.signal, c2.signal]);

    expect(combined.aborted).toBe(false);
    c1.abort("first");
    expect(combined.aborted).toBe(true);
    expect(combined.reason).toBe("first");
  });

  it("aborts when second signal aborts", () => {
    const c1 = new AbortController();
    const c2 = new AbortController();
    const combined = anySignal([c1.signal, c2.signal]);

    expect(combined.aborted).toBe(false);
    c2.abort("second");
    expect(combined.aborted).toBe(true);
  });

  it("handles empty array (never aborts)", () => {
    const combined = anySignal([]);
    expect(combined.aborted).toBe(false);
  });

  it("handles single signal", () => {
    const c = new AbortController();
    const combined = anySignal([c.signal]);
    expect(combined.aborted).toBe(false);
    c.abort("done");
    expect(combined.aborted).toBe(true);
  });

  it("only fires once when multiple signals abort", () => {
    const c1 = new AbortController();
    const c2 = new AbortController();
    const combined = anySignal([c1.signal, c2.signal]);

    c1.abort("first");
    c2.abort("second");

    // Should use the first abort reason
    expect(combined.aborted).toBe(true);
    expect(combined.reason).toBe("first");
  });
});
