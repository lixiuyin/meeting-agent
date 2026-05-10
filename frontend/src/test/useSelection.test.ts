import { describe, it, expect } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useSelection } from "../hooks/useSelection";

describe("useSelection", () => {
  it("starts with empty selection and mode off", () => {
    const { result } = renderHook(() => useSelection([1, 2, 3]));
    expect(result.current.selectedIds.size).toBe(0);
    expect(result.current.isSelectionMode).toBe(false);
    expect(result.current.selectedCount).toBe(0);
  });

  it("toggles items in selection", () => {
    const { result } = renderHook(() => useSelection([1, 2, 3]));

    act(() => result.current.toggleSelection(1));
    expect(result.current.selectedIds.has(1)).toBe(true);
    expect(result.current.selectedCount).toBe(1);

    act(() => result.current.toggleSelection(1));
    expect(result.current.selectedIds.has(1)).toBe(false);
    expect(result.current.selectedCount).toBe(0);
  });

  it("selects all items", () => {
    const { result } = renderHook(() => useSelection([1, 2, 3]));

    act(() => result.current.selectAll());
    expect(result.current.selectedCount).toBe(3);
    expect(result.current.selectedIds.has(1)).toBe(true);
    expect(result.current.selectedIds.has(2)).toBe(true);
    expect(result.current.selectedIds.has(3)).toBe(true);
  });

  it("clears selection", () => {
    const { result } = renderHook(() => useSelection([1, 2, 3]));

    act(() => result.current.selectAll());
    expect(result.current.selectedCount).toBe(3);

    act(() => result.current.clearSelection());
    expect(result.current.selectedCount).toBe(0);
  });

  it("toggles selection mode and clears on exit", () => {
    const { result } = renderHook(() => useSelection([1, 2]));

    act(() => result.current.toggleSelectionMode());
    expect(result.current.isSelectionMode).toBe(true);

    act(() => result.current.toggleSelection(1));
    expect(result.current.selectedCount).toBe(1);

    act(() => result.current.toggleSelectionMode());
    expect(result.current.isSelectionMode).toBe(false);
    expect(result.current.selectedCount).toBe(0);
  });

  it("exitSelectionMode resets both mode and selection", () => {
    const { result } = renderHook(() => useSelection([1, 2]));

    act(() => result.current.toggleSelectionMode());
    act(() => result.current.selectAll());

    act(() => result.current.exitSelectionMode());
    expect(result.current.isSelectionMode).toBe(false);
    expect(result.current.selectedCount).toBe(0);
  });
});
