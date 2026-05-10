import { describe, it, expect } from "vitest";

/**
 * Unit tests for abort/cancel patterns used by useChatStream.
 *
 * The hook itself is tightly coupled to DOM refs and async generators,
 * making it hard to unit-test in isolation. Instead we verify the key
 * primitives: abort detection, generator teardown, and error classification.
 */

describe("useChatStream abort patterns", () => {
  function isAbortError(err: unknown): boolean {
    if (err instanceof DOMException) {
      return err.name === "AbortError";
    }
    if (err instanceof Error) {
      return err.name === "CanceledError" || err.name === "AbortError";
    }
    return false;
  }

  it("classifies AbortError correctly", () => {
    const abortErr = new DOMException("aborted", "AbortError");
    expect(isAbortError(abortErr)).toBe(true);
  });

  it("classifies CanceledError correctly", () => {
    const cancelErr = new Error("canceled");
    cancelErr.name = "CanceledError";
    expect(isAbortError(cancelErr)).toBe(true);
  });

  it("does not classify regular errors as abort", () => {
    expect(isAbortError(new Error("network failure"))).toBe(false);
    expect(isAbortError(new TypeError("bad"))).toBe(false);
    expect(isAbortError(null)).toBe(false);
    expect(isAbortError(undefined)).toBe(false);
    expect(isAbortError("string")).toBe(false);
  });

  it("aborted signal prevents further generator yields", async () => {
    const controller = new AbortController();
    const yielded: number[] = [];

    async function* stream() {
      for (let i = 0; i < 5; i++) {
        if (controller.signal.aborted) return;
        yielded.push(i);
        yield i;
      }
    }

    const gen = stream();

    // Consume first two yields
    await gen.next();
    await gen.next();
    expect(yielded).toEqual([0, 1]);

    // Abort and continue
    controller.abort();
    const result = await gen.next();
    expect(result.done).toBe(true);
    expect(yielded).toEqual([0, 1]); // no more yields after abort
  });

  it("generator aclose prevents ignored GeneratorExit warning", async () => {
    let closed = false;

    async function* stream() {
      try {
        yield "token1";
        yield "token2";
        // simulate long-running work
        await new Promise(() => {});
      } finally {
        closed = true;
      }
    }

    const gen = stream();
    await gen.next();
    // Properly close the generator
    await gen.return(undefined);
    expect(closed).toBe(true);
  });
});
