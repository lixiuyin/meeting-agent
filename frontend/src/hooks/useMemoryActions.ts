import { useState, useCallback, useEffect, useMemo } from "react";
import { message, Modal } from "antd";
import {
  createMemory,
  deleteMemory,
  listMemories,
  searchMemories,
  updateMemory,
  batchImportMemories,
  exportMemories,
  triggerDecay,
  formatApiErrorMessage,
  type MemoryItem,
} from "../api/client";
import { useUndoStack } from "./useUndoStack";

export function useMemoryActions(userId: string) {
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [semanticResults, setSemanticResults] = useState<MemoryItem[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [decaying, setDecaying] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [editMemory, setEditMemory] = useState<MemoryItem | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState("");
  const { enqueueUndo } = useUndoStack();

  // Selection state for batch operations
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());
  const [isSelectionMode, setIsSelectionMode] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listMemories(userId);
      const active = res.data.memories.filter((m: MemoryItem) => !m.superseded_by);
      setMemories(active);
    } catch (err) {
      message.error(formatApiErrorMessage(err, "Failed to load memories"));
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      void load();
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [load]);

  const filtered = useMemo(() => {
    if (semanticResults !== null) return semanticResults;
    const q = search.toLowerCase();
    return q
      ? memories.filter((m) => m.key.toLowerCase().includes(q) || m.value.toLowerCase().includes(q))
      : memories;
  }, [search, memories, semanticResults]);

  const handleSemanticSearch = async () => {
    if (!search.trim()) return;
    setSearching(true);
    setSemanticResults(null);
    try {
      const res = await searchMemories(search, userId);
      setSemanticResults(res.data.memories);
    } catch (err) {
      message.error(formatApiErrorMessage(err, "Semantic search failed"));
    } finally {
      setSearching(false);
    }
  };

  const clearSemantic = () => {
    setSemanticResults(null);
    setSearch("");
  };

  const handleCreate = async (values: {
    key: string;
    value: string;
    category?: string;
    importance?: number;
    expiresInDays?: number;
  }) => {
    try {
      await createMemory(
        values.key,
        values.value,
        userId,
        values.category,
        values.importance,
        values.expiresInDays,
      );
      message.success("Memory created");
      setCreateOpen(false);
      await load();
    } catch (err) {
      message.error(formatApiErrorMessage(err, "Failed to create memory"));
    }
  };

  const handleEdit = async (values: {
    key: string;
    value: string;
    category?: string;
    importance?: number;
  }) => {
    if (!editMemory) return;
    try {
      await updateMemory(editMemory.key, values.value, userId, values.category, values.importance);
      message.success("Memory updated");
      setEditMemory(null);
      await load();
    } catch (err) {
      message.error(formatApiErrorMessage(err, "Failed to update memory"));
    }
  };

  const handleDelete = async (key: string) => {
    const target = memories.find((m) => m.key === key);
    Modal.confirm({
      width: "min(92vw, 680px)",
      title: `Delete memory "${key}"?`,
      okType: "danger",
      onOk: async () => {
        try {
          await deleteMemory(key, userId);
          await load();
          message.success("Memory deleted");
          if (target) {
            enqueueUndo({
              key: `memory-delete-${key}`,
              content: `Deleted "${key}"`,
              onUndo: async () => {
                await createMemory(
                  target.key,
                  target.value,
                  userId,
                  target.category ?? undefined,
                  target.importance,
                );
                await load();
                message.success(`Restored "${key}"`);
              },
            });
          }
        } catch (err) {
          message.error(formatApiErrorMessage(err, "Failed to delete memory"));
        }
      },
    });
  };

  const handleImport = async (text: string) => {
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch {
      message.error("Invalid JSON format");
      return;
    }
    if (!Array.isArray(parsed)) {
      message.error("Expected a JSON array of {key, value} objects");
      return;
    }
    const items = parsed.map((item: Record<string, unknown>) => ({
      key: String(item.key),
      value: String(item.value),
      category: item.category ? String(item.category) : undefined,
      importance: typeof item.importance === "number" ? item.importance : undefined,
      expires_in_days: typeof item.expires_in_days === "number" ? item.expires_in_days : undefined,
    }));
    if (!items.every((item) => item.key.trim() && item.value.trim())) {
      message.error("All memory entries must have non-empty key and value");
      return;
    }
    try {
      const res = await batchImportMemories(items, userId);
      message.success(`Imported ${res.data.imported} memories (${res.data.failed} failed)`);
      setImportOpen(false);
      await load();
    } catch (err) {
      message.error(formatApiErrorMessage(err, "Import failed"));
    }
  };

  const handleExport = async () => {
    try {
      const res = await exportMemories(userId);
      const blob = new Blob([JSON.stringify(res.data.memories, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      try {
        const a = document.createElement("a");
        a.href = url;
        a.download = `memories_export_${new Date().toISOString().slice(0, 10)}.json`;
        a.click();
        message.success("Memories exported");
      } finally {
        URL.revokeObjectURL(url);
      }
    } catch (err) {
      message.error(formatApiErrorMessage(err, "Export failed"));
    }
  };

  const handleDecay = async () => {
    setDecaying(true);
    try {
      const res = await triggerDecay(userId);
      message.success(`Updated relevance scores for ${res.data.decayed_count} memories`);
      await load();
    } catch (err) {
      message.error(formatApiErrorMessage(err, "Decay failed"));
    } finally {
      setDecaying(false);
    }
  };

  const displayMemories = semanticResults ?? filtered;

  // --- Selection handlers ---
  const toggleSelectionMode = useCallback(() => {
    setIsSelectionMode((prev) => {
      if (prev) setSelectedKeys(new Set());
      return !prev;
    });
  }, []);

  const toggleSelection = useCallback((key: string) => {
    setSelectedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const selectAll = useCallback(() => {
    setSelectedKeys(new Set(displayMemories.map((m) => m.key)));
  }, [displayMemories]);

  const clearSelection = useCallback(() => {
    setSelectedKeys(new Set());
  }, []);

  const exitSelectionMode = useCallback(() => {
    setIsSelectionMode(false);
    setSelectedKeys(new Set());
  }, []);

  const handleBatchDelete = useCallback(
    async (keys: string[]) => {
      if (keys.length === 0) return;
      const results = await Promise.allSettled(keys.map((key) => deleteMemory(key, userId)));
      const succeeded: string[] = [];
      const failed: string[] = [];
      keys.forEach((key, i) => {
        if (results[i].status === "fulfilled") succeeded.push(key);
        else failed.push(key);
      });
      if (succeeded.length > 0) {
        message.success(`${succeeded.length} memor${succeeded.length > 1 ? "ies" : "y"} deleted`);
        await load();
      }
      if (failed.length > 0) {
        message.error(`Failed to delete ${failed.length} memor${failed.length > 1 ? "ies" : "y"}`);
      }
      // Keep failed keys selected so the user can retry; clear only succeeded.
      setSelectedKeys(new Set(failed));
      if (failed.length === 0) {
        setIsSelectionMode(false);
      }
    },
    [userId, load],
  );

  return {
    // State
    memories,
    filtered,
    loading,
    search,
    setSearch,
    semanticResults,
    searching,
    decaying,
    createOpen,
    setCreateOpen,
    editMemory,
    setEditMemory,
    importOpen,
    setImportOpen,
    importText,
    setImportText,
    // Derived
    displayMemories,
    // Selection
    selectedKeys,
    isSelectionMode,
    toggleSelectionMode,
    toggleSelection,
    selectAll,
    clearSelection,
    exitSelectionMode,
    selectedCount: selectedKeys.size,
    // Handlers
    load,
    handleSemanticSearch,
    clearSemantic,
    handleCreate,
    handleEdit,
    handleDelete,
    handleImport,
    handleExport,
    handleDecay,
    handleBatchDelete,
  };
}
