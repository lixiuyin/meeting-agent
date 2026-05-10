import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { looksSensitiveStorageKey, useLocalStorage } from "../hooks/useLocalStorage";

function mockLocalStorage() {
  const store: Record<string, string> = {};
  return {
    getItem: vi.fn((key: string) => store[key] ?? null),
    setItem: vi.fn((key: string, value: string) => {
      store[key] = value;
    }),
    removeItem: vi.fn((key: string) => {
      delete store[key];
    }),
    clear: vi.fn(() => {
      Object.keys(store).forEach((k) => delete store[k]);
    }),
    get store() {
      return store;
    },
  };
}

describe("useLocalStorage", () => {
  let storage: ReturnType<typeof mockLocalStorage>;
  let warnSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    storage = mockLocalStorage();
    vi.stubGlobal("localStorage", storage);
    warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
  });

  afterEach(() => {
    warnSpy.mockRestore();
  });

  it("returns initial value when key is not stored", () => {
    const { result } = renderHook(() => useLocalStorage("test-key", "default"));
    expect(result.current[0]).toBe("default");
  });

  it("persists value to localStorage", () => {
    const { result } = renderHook(() => useLocalStorage("test-key", "default"));

    act(() => {
      result.current[1]("updated");
    });

    expect(result.current[0]).toBe("updated");
    expect(storage.setItem).toHaveBeenCalledWith("test-key", '"updated"');
  });

  it("reads existing value from localStorage", () => {
    storage.store["test-key"] = '"existing"';
    const { result } = renderHook(() => useLocalStorage("test-key", "default"));
    expect(result.current[0]).toBe("existing");
  });

  it("supports function updater", () => {
    const { result } = renderHook(() => useLocalStorage("counter", 0));

    act(() => {
      result.current[1]((prev) => prev + 1);
    });

    expect(result.current[0]).toBe(1);
  });

  it("handles JSON parse errors gracefully", () => {
    storage.store["bad-key"] = "not-valid-json{{{";
    const { result } = renderHook(() => useLocalStorage("bad-key", "fallback"));
    expect(result.current[0]).toBe("fallback");
    expect(warnSpy).toHaveBeenCalledWith(
      "useLocalStorage read failed for key %s:",
      "bad-key",
      expect.any(SyntaxError),
    );
  });

  it("does not flag generic storage keys as sensitive", () => {
    expect(looksSensitiveStorageKey("test-key")).toBe(false);
    expect(looksSensitiveStorageKey("materials-view-mode")).toBe(false);
  });

  it("flags explicitly sensitive storage keys", () => {
    expect(looksSensitiveStorageKey("api-key")).toBe(true);
    expect(looksSensitiveStorageKey("access-token")).toBe(true);
  });
});
