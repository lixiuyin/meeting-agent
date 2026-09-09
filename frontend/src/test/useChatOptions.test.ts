import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useChatOptions } from "../hooks/useChatOptions";

describe("useChatOptions", () => {
  beforeEach(() => {
    const values = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: vi.fn((key: string) => values.get(key) ?? null),
      setItem: vi.fn((key: string, value: string) => values.set(key, value)),
      removeItem: vi.fn((key: string) => values.delete(key)),
      clear: vi.fn(() => values.clear()),
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("uses modes without sending a top-k override", () => {
    const { result } = renderHook(() => useChatOptions());

    expect(result.current.chatOptions.topK).toBeUndefined();
    expect(result.current.chatOptions.retrievalProfile).toBe("balanced");
    expect(result.current.chatOptions.memoryMode).toBe("balanced");

    act(() => {
      result.current.setRetrievalProfile("thorough");
      result.current.setMemoryMode("deep");
    });

    expect(result.current.chatOptions.retrievalProfile).toBe("thorough");
    expect(result.current.chatOptions.memoryMode).toBe("deep");
    expect(result.current.chatOptions.topK).toBeUndefined();
  });

  it("restores the selected modes after a remount", () => {
    const first = renderHook(() => useChatOptions());

    act(() => {
      first.result.current.setRetrievalProfile("fast");
      first.result.current.setMemoryMode("focused");
    });
    first.unmount();

    const second = renderHook(() => useChatOptions());
    expect(second.result.current.retrievalProfile).toBe("fast");
    expect(second.result.current.memoryMode).toBe("focused");
  });

  it("falls back to balanced for stale or invalid stored modes", () => {
    localStorage.setItem("chat-retrieval-profile", '"legacy"');
    localStorage.setItem("chat-memory-mode", '"invalid"');

    const { result } = renderHook(() => useChatOptions());
    expect(result.current.retrievalProfile).toBe("balanced");
    expect(result.current.memoryMode).toBe("balanced");
  });
});
