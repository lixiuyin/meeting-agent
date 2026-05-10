import { useState, useCallback, useMemo } from "react";

export function useSelection(itemIds: number[]) {
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [isSelectionMode, setIsSelectionMode] = useState(false);

  const toggleSelectionMode = useCallback(() => {
    setIsSelectionMode((prev) => {
      if (prev) {
        // Clear selection when exiting mode
        setSelectedIds(new Set());
      }
      return !prev;
    });
  }, []);

  const toggleSelection = useCallback((id: number, e?: React.MouseEvent) => {
    e?.stopPropagation();
    setSelectedIds((prev) => {
      const newSet = new Set(prev);
      if (newSet.has(id)) {
        newSet.delete(id);
      } else {
        newSet.add(id);
      }
      return newSet;
    });
  }, []);

  const selectAll = useCallback(() => {
    setSelectedIds(new Set(itemIds));
  }, [itemIds]);

  const clearSelection = useCallback(() => {
    setSelectedIds(new Set());
  }, []);

  const exitSelectionMode = useCallback(() => {
    setIsSelectionMode(false);
    setSelectedIds(new Set());
  }, []);

  return useMemo(
    () => ({
      selectedIds,
      isSelectionMode,
      toggleSelectionMode,
      toggleSelection,
      selectAll,
      clearSelection,
      exitSelectionMode,
      selectedCount: selectedIds.size,
    }),
    [
      selectedIds,
      isSelectionMode,
      toggleSelectionMode,
      toggleSelection,
      selectAll,
      clearSelection,
      exitSelectionMode,
    ],
  );
}
