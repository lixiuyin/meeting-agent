import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { useHomeShortcuts } from "../hooks/useHomeShortcuts";

function createMockDeps(overrides: Partial<Parameters<typeof useHomeShortcuts>[0]> = {}) {
  return {
    inputFocused: false,
    textareaRef: { current: { blur: vi.fn(), focus: vi.fn() } } as never,
    handleSendRef: { current: vi.fn() },
    ...overrides,
  };
}

describe("useHomeShortcuts", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("sends message on Cmd+Enter", () => {
    const handleSend = vi.fn();
    const deps = createMockDeps({ handleSendRef: { current: handleSend } });

    renderHook(() => useHomeShortcuts(deps));

    const event = new KeyboardEvent("keydown", {
      key: "Enter",
      metaKey: true,
      bubbles: true,
    });
    vi.spyOn(event, "preventDefault");
    window.dispatchEvent(event);

    expect(handleSend).toHaveBeenCalledOnce();
  });

  it("sends message on Ctrl+Enter", () => {
    const handleSend = vi.fn();
    const deps = createMockDeps({ handleSendRef: { current: handleSend } });

    renderHook(() => useHomeShortcuts(deps));

    const event = new KeyboardEvent("keydown", {
      key: "Enter",
      ctrlKey: true,
      bubbles: true,
    });
    window.dispatchEvent(event);

    expect(handleSend).toHaveBeenCalledOnce();
  });

  it("blurs textarea on Escape when input is focused", () => {
    const blur = vi.fn();
    const deps = createMockDeps({
      inputFocused: true,
      textareaRef: { current: { blur, focus: vi.fn() } } as never,
    });

    renderHook(() => useHomeShortcuts(deps));

    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));

    expect(blur).toHaveBeenCalledOnce();
  });

  it("does not blur on Escape when input is not focused", () => {
    const blur = vi.fn();
    const deps = createMockDeps({
      inputFocused: false,
      textareaRef: { current: { blur, focus: vi.fn() } } as never,
    });

    renderHook(() => useHomeShortcuts(deps));

    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));

    expect(blur).not.toHaveBeenCalled();
  });

  it("focuses textarea on / when no input is focused", () => {
    const focus = vi.fn();
    const deps = createMockDeps({
      inputFocused: false,
      textareaRef: { current: { blur: vi.fn(), focus } } as never,
    });

    renderHook(() => useHomeShortcuts(deps));

    window.dispatchEvent(new KeyboardEvent("keydown", { key: "/" }));

    expect(focus).toHaveBeenCalledOnce();
  });

  it("cleans up event listener on unmount", () => {
    const handleSend = vi.fn();
    const { unmount } = renderHook(() =>
      useHomeShortcuts(createMockDeps({ handleSendRef: { current: handleSend } })),
    );

    unmount();

    // After unmount, keyboard events should not trigger any action
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", metaKey: true }));

    // The listener was removed, so handleSend should not be called
    expect(handleSend).not.toHaveBeenCalled();
  });
});
