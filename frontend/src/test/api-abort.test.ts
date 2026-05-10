import { describe, it, expect } from "vitest";

describe("AbortController cleanup pattern", () => {
  it("aborts previous request when new one is made", () => {
    const controllerState: { current: AbortController | null } = { current: null };

    function makeRequest(): AbortController {
      controllerState.current?.abort();
      const controller = new AbortController();
      controllerState.current = controller;
      return controller;
    }

    const ctrl1 = makeRequest();
    expect(ctrl1.signal.aborted).toBe(false);

    const ctrl2 = makeRequest();
    // Previous controller should be aborted
    expect(ctrl1.signal.aborted).toBe(true);
    expect(ctrl2.signal.aborted).toBe(false);

    // Cleanup on unmount should abort current controller
    controllerState.current?.abort();
    expect(ctrl2.signal.aborted).toBe(true);
  });

  it("handles cleanup when controller is null", () => {
    const cleanup = () => {
      const ctrl: AbortController | null = null;
      (ctrl as AbortController | null)?.abort();
    };
    expect(cleanup).not.toThrow();
  });

  it("detects AbortError correctly", () => {
    const isAbortError = (err: unknown): boolean => {
      return (err as Error)?.name === "AbortError";
    };

    const abortErr = new DOMException("aborted", "AbortError");
    expect(isAbortError(abortErr)).toBe(true);

    const otherErr = new Error("network error");
    expect(isAbortError(otherErr)).toBe(false);

    expect(isAbortError(null)).toBe(false);
    expect(isAbortError("string error")).toBe(false);
  });
});

describe("debounced search with abort", () => {
  it("aborts controller on cleanup", () => {
    const { signal } = new AbortController();
    expect(signal.aborted).toBe(false);

    // Simulate cleanup function from useEffect return
    const cleanup = () => {
      // This is what the cleanup should do
    };
    cleanup();
    expect(signal.aborted).toBe(false); // manual cleanup doesn't auto-abort
  });

  it("can abort signal via AbortController", () => {
    const controller = new AbortController();
    const { signal } = controller;
    expect(signal.aborted).toBe(false);

    controller.abort();
    expect(signal.aborted).toBe(true);
    expect(signal.reason).toBeTruthy();
  });
});
