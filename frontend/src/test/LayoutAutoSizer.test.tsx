import { render, screen, act } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import LayoutAutoSizer from "../components/materials/file-views/LayoutAutoSizer";

afterEach(() => vi.restoreAllMocks());

it("uses layout width during a scale animation and updates on resize", () => {
  let width = 509;
  let resized: (() => void) | undefined;
  const disconnect = vi.fn();
  vi.spyOn(Element.prototype, "clientWidth", "get").mockImplementation(() => width);
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 0, 106, 100));
  vi.spyOn(window, "ResizeObserver").mockImplementation(function () {
    return { observe: vi.fn(), unobserve: vi.fn(), disconnect };
  });
  vi.mocked(window.ResizeObserver).mockImplementation(function (callback) {
    resized = () => callback([], {} as ResizeObserver);
    return { observe: vi.fn(), unobserve: vi.fn(), disconnect };
  });
  const { unmount } = render(
    <div style={{ transform: "scale(0.2)" }}>
      <LayoutAutoSizer renderProp={({ width }) => <span data-testid="width">{width}</span>} />
    </div>,
  );
  expect(screen.getByTestId("width")).toHaveTextContent("509");
  act(() => {
    width = 700;
    resized?.();
  });
  expect(screen.getByTestId("width")).toHaveTextContent("700");
  unmount();
  expect(disconnect).toHaveBeenCalledOnce();
});
