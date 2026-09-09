import { useState, useCallback, useEffect, useMemo, useRef } from "react";
import { message, Modal } from "antd";
import {
  createMemory,
  deleteMemory,
  retryMemoryIndex,
  listMemories,
  searchMemories,
  updateMemory,
  updateMemoryStatus,
  resolveMemoryConflict,
  batchImportMemories,
  batchDeleteMemories,
  exportAllMemories,
  recordMemoryFeedback,
  triggerDecay,
  formatApiErrorMessage,
  isRequestCanceled,
  type MemoryItem,
} from "../api/client";

export function useMemoryActions(
  userId: string,
  memoryKind: "all" | "personal" | "reference" = "all",
) {
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [total, setTotal] = useState(0);
  const [search, setSearch] = useState("");
  const [factTypeFilter, setFactTypeFilter] = useState<string>();
  const [statusFilter, setStatusFilter] = useState<string>();
  const [projectFilter, setProjectFilter] = useState<string>();
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [debouncedProjectFilter, setDebouncedProjectFilter] = useState<string>();
  const [semanticResults, setSemanticResults] = useState<MemoryItem[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [decaying, setDecaying] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [editMemory, setEditMemory] = useState<MemoryItem | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState("");
  const [activeAction, setActiveAction] = useState<
    "create" | "edit" | "delete" | "import" | "export" | "batch-delete" | null
  >(null);
  const [feedbackKey, setFeedbackKey] = useState<string | null>(null);
  const loadAbortRef = useRef<AbortController | null>(null);
  const loadMoreAbortRef = useRef<AbortController | null>(null);
  const semanticAbortRef = useRef<AbortController | null>(null);
  const semanticGenerationRef = useRef(0);
  const semanticPendingRef = useRef(false);
  const semanticRequestRef = useRef<{ query: string; scope: string } | null>(null);
  const lastLiteralLoadRef = useRef<string | null>(null);
  const literalLoadKey = JSON.stringify([
    userId,
    memoryKind,
    factTypeFilter,
    statusFilter,
    debouncedProjectFilter,
    debouncedSearch,
  ]);
  const literalScope = [
    userId,
    memoryKind,
    factTypeFilter,
    statusFilter,
    projectFilter?.trim(),
  ].join("|");
  const feedbackPendingRef = useRef(false);

  useEffect(() => {
    const timeout = window.setTimeout(() => setDebouncedSearch(search.trim()), 250);
    return () => window.clearTimeout(timeout);
  }, [search]);

  useEffect(() => {
    const timeout = window.setTimeout(
      () => setDebouncedProjectFilter(projectFilter?.trim() || undefined),
      250,
    );
    return () => window.clearTimeout(timeout);
  }, [projectFilter]);

  // Selection state for batch operations
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());
  const [isSelectionMode, setIsSelectionMode] = useState(false);

  const load = useCallback(async () => {
    loadMoreAbortRef.current?.abort();
    loadMoreAbortRef.current = null;
    setLoadingMore(false);
    semanticRequestRef.current = null;
    semanticAbortRef.current?.abort();
    semanticGenerationRef.current += 1;
    setSearching(false);
    setSemanticResults(null);
    loadAbortRef.current?.abort();
    const controller = new AbortController();
    loadAbortRef.current = controller;
    setLoading(true);
    try {
      const page = await listMemories(userId, {
        limit: 100,
        query: debouncedSearch || undefined,
        factType: factTypeFilter,
        assertionStatus: statusFilter,
        projectId: debouncedProjectFilter,
        memoryKind,
        signal: controller.signal,
      });
      if (!controller.signal.aborted) {
        lastLiteralLoadRef.current = literalLoadKey;
        setMemories(page.data.items.filter((memory) => !memory.superseded_by));
        setNextCursor(page.data.next_cursor);
        setTotal(page.data.total);
      }
    } catch (err) {
      if (!isRequestCanceled(err)) {
        message.error(formatApiErrorMessage(err, "Failed to load memories"));
      }
    } finally {
      if (loadAbortRef.current === controller) {
        loadAbortRef.current = null;
        if (!controller.signal.aborted) setLoading(false);
      }
    }
  }, [
    debouncedProjectFilter,
    debouncedSearch,
    factTypeFilter,
    statusFilter,
    userId,
    memoryKind,
    literalLoadKey,
  ]);

  const loadLiteralSearch = useCallback(async () => {
    // A debounced literal search must not cancel an explicit semantic search
    // that the user has just submitted with Enter/the search button.
    if (
      semanticRequestRef.current?.query === search.trim() &&
      semanticRequestRef.current.scope === literalScope
    )
      return;
    // Query and project debounce independently. Never load a mixed old/new
    // filter tuple while one of them is still catching up.
    if (
      debouncedSearch !== search.trim() ||
      debouncedProjectFilter !== (projectFilter?.trim() || undefined)
    )
      return;
    // A live input rerender need not refetch an already loaded filter tuple.
    // Explicit Refresh still calls load directly, including after local edits.
    if (lastLiteralLoadRef.current === literalLoadKey) {
      // Returning from B to an already loaded A must also cancel B's pending
      // response; otherwise it can overwrite A after this cache hit.
      loadAbortRef.current?.abort();
      loadAbortRef.current = null;
      setLoading(false);
      return;
    }
    await load();
  }, [
    load,
    debouncedSearch,
    debouncedProjectFilter,
    search,
    projectFilter,
    literalScope,
    literalLoadKey,
  ]);

  const loadMore = useCallback(async () => {
    if (!nextCursor || loadingMore || loadAbortRef.current) return;
    const controller = new AbortController();
    loadMoreAbortRef.current = controller;
    setLoadingMore(true);
    try {
      const page = await listMemories(userId, {
        limit: 100,
        cursor: nextCursor,
        query: debouncedSearch || undefined,
        factType: factTypeFilter,
        assertionStatus: statusFilter,
        projectId: debouncedProjectFilter,
        memoryKind,
        signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      setMemories((current) => {
        const seen = new Set(current.map((item) => item.key));
        return [
          ...current,
          ...page.data.items.filter((item) => !item.superseded_by && !seen.has(item.key)),
        ];
      });
      setNextCursor(page.data.next_cursor);
      setTotal(page.data.total);
    } catch (error) {
      if (!isRequestCanceled(error)) {
        message.error(formatApiErrorMessage(error, "Failed to load more memories"));
      }
    } finally {
      if (loadMoreAbortRef.current === controller) {
        loadMoreAbortRef.current = null;
        setLoadingMore(false);
      }
    }
  }, [
    debouncedSearch,
    factTypeFilter,
    loadingMore,
    nextCursor,
    debouncedProjectFilter,
    memoryKind,
    statusFilter,
    userId,
  ]);

  useEffect(
    () => () => {
      loadAbortRef.current?.abort();
      loadMoreAbortRef.current?.abort();
      semanticAbortRef.current?.abort();
    },
    [],
  );

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      void loadLiteralSearch();
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [loadLiteralSearch]);

  const filtered = useMemo(() => {
    if (semanticResults !== null) return semanticResults;
    const q = search.toLowerCase();
    return q
      ? memories.filter((m) => m.key.toLowerCase().includes(q) || m.value.toLowerCase().includes(q))
      : memories;
  }, [search, memories, semanticResults]);

  const handleSemanticSearch = async () => {
    if (!search.trim()) return;
    semanticRequestRef.current = { query: search.trim(), scope: literalScope };
    loadAbortRef.current?.abort();
    setLoading(false);
    semanticAbortRef.current?.abort();
    const controller = new AbortController();
    semanticAbortRef.current = controller;
    const generation = ++semanticGenerationRef.current;
    semanticPendingRef.current = true;
    setSearching(true);
    setSemanticResults(null);
    try {
      const res = await searchMemories(search, userId, 10, undefined, {
        signal: controller.signal,
        memoryKind,
        projectId: projectFilter?.trim() || undefined,
        factType: factTypeFilter,
        assertionStatus: statusFilter,
      });
      if (controller.signal.aborted || semanticGenerationRef.current !== generation) return;
      setSemanticResults(res.data.memories);
    } catch (err) {
      if (isRequestCanceled(err)) return;
      message.error(formatApiErrorMessage(err, "Semantic search failed"));
    } finally {
      if (!controller.signal.aborted && semanticGenerationRef.current === generation) {
        setSearching(false);
      }
      if (semanticGenerationRef.current === generation) semanticPendingRef.current = false;
    }
  };

  const clearSemantic = () => {
    semanticRequestRef.current = null;
    semanticAbortRef.current?.abort();
    semanticGenerationRef.current += 1;
    semanticPendingRef.current = false;
    setSearching(false);
    setSemanticResults(null);
    setSearch("");
  };

  const invalidateSemanticResults = () => {
    semanticRequestRef.current = null;
    semanticAbortRef.current?.abort();
    semanticGenerationRef.current += 1;
    setSearching(false);
    setSemanticResults(null);
    semanticPendingRef.current = false;
  };

  const handleCreate = async (values: {
    key: string;
    value: string;
    category?: string;
    importance?: number;
    expiresInDays?: number;
    factType?: "fact" | "preference" | "project_fact" | "decision" | "action_item";
    assertionStatus?: "pending" | "confirmed" | "disputed" | "superseded" | "retracted";
    projectId?: string;
    validFrom?: string;
    validTo?: string;
    actionStatus?: "open" | "in_progress" | "blocked" | "done" | "cancelled";
    assignee?: string;
    dueAt?: string;
  }) => {
    if (activeAction) return;
    setActiveAction("create");
    try {
      const created = await createMemory(
        values.key,
        values.value,
        userId,
        values.category,
        values.importance,
        values.expiresInDays,
        {
          factType: values.factType,
          assertionStatus: values.assertionStatus,
          projectId: values.projectId ?? null,
          validFrom: values.validFrom ?? null,
          validTo: values.validTo ?? null,
          actionStatus: values.actionStatus ?? null,
          assignee: values.assignee ?? null,
          dueAt: values.dueAt ?? null,
        },
      );
      message.success("Memory created");
      setCreateOpen(false);
      // Keep the newly created item visible immediately. A server reload sorts
      // primarily by importance, which can place a default-importance item
      // below the virtualized viewport and make a successful create appear to
      // have vanished.
      invalidateSemanticResults();
      setSearch("");
      setMemories((current) => [
        created.data,
        ...current.filter((item) => item.key !== created.data.key),
      ]);
    } catch (err) {
      message.error(formatApiErrorMessage(err, "Failed to create memory"));
    } finally {
      setActiveAction(null);
    }
  };

  const handleEdit = async (values: {
    key: string;
    value: string;
    category?: string;
    importance?: number;
    factType?: "fact" | "preference" | "project_fact" | "decision" | "action_item";
    assertionStatus?: "pending" | "confirmed" | "disputed" | "superseded" | "retracted";
    projectId?: string;
    validFrom?: string;
    validTo?: string;
    actionStatus?: "open" | "in_progress" | "blocked" | "done" | "cancelled";
    assignee?: string;
    dueAt?: string;
  }) => {
    if (!editMemory || activeAction) return;
    setActiveAction("edit");
    try {
      const updated = await updateMemory(
        editMemory.key,
        values.value,
        editMemory.revision,
        userId,
        values.category ?? null,
        values.importance,
        {
          factType: values.factType,
          assertionStatus: values.assertionStatus,
          projectId: values.projectId ?? null,
          validFrom: values.validFrom ?? null,
          validTo: values.validTo ?? null,
          actionStatus: values.actionStatus ?? null,
          assignee: values.assignee ?? null,
          dueAt: values.dueAt ?? null,
        },
      );
      const replaceUpdated = (item: MemoryItem) =>
        item.key === updated.data.key ? updated.data : item;
      invalidateSemanticResults();
      setMemories((current) => current.map(replaceUpdated));
      setSemanticResults((current) => current?.map(replaceUpdated) ?? null);
      message.success("Memory updated");
      setEditMemory(null);
    } catch (err) {
      message.error(formatApiErrorMessage(err, "Failed to update memory"));
    } finally {
      setActiveAction(null);
    }
  };

  const handleDelete = async (key: string) => {
    Modal.confirm({
      width: "min(92vw, 680px)",
      title: `Delete memory "${key}"?`,
      okType: "danger",
      onOk: async () => {
        if (activeAction) return;
        setActiveAction("delete");
        try {
          await deleteMemory(key, userId);
          invalidateSemanticResults();
          setMemories((current) => current.filter((memory) => memory.key !== key));
          setSemanticResults((current) => current?.filter((memory) => memory.key !== key) ?? null);
          message.success("Memory deleted");
        } catch (err) {
          message.error(formatApiErrorMessage(err, "Failed to delete memory"));
        } finally {
          setActiveAction(null);
        }
      },
    });
  };

  const handleStatusChange = async (
    memory: MemoryItem,
    status: "confirmed" | "retracted" | "disputed",
  ) => {
    if (activeAction) return;
    setActiveAction("edit");
    try {
      if (status === "confirmed" && memory.conflicts_with?.length) {
        await resolveMemoryConflict(memory.key, memory.revision, memory.conflicts_with);
      } else {
        await updateMemoryStatus(memory.key, memory.revision, status);
      }
      // A lifecycle transition can move the row out of the active server-side
      // filter. Reload the authoritative page instead of retaining a locally
      // patched row that no longer satisfies the selected filter.
      invalidateSemanticResults();
      await load();
      message.success(`Memory marked ${status}`);
    } catch (error) {
      message.error(formatApiErrorMessage(error, "Failed to update memory status"));
      await load();
    } finally {
      setActiveAction(null);
    }
  };

  const handleImport = async (text: string) => {
    if (activeAction) return;
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
    if (!parsed.every((item) => item !== null && typeof item === "object")) {
      message.error("Every array item must be a memory object");
      return;
    }
    const items = (parsed as Record<string, unknown>[]).map((item) => ({
      key: typeof item.key === "string" ? item.key : "",
      value: typeof item.value === "string" ? item.value : "",
      category: item.category ? String(item.category) : undefined,
      importance: typeof item.importance === "number" ? item.importance : undefined,
      expires_in_days: typeof item.expires_in_days === "number" ? item.expires_in_days : undefined,
      expires_at: typeof item.expires_at === "string" ? item.expires_at : undefined,
      fact_type:
        typeof item.fact_type === "string"
          ? (item.fact_type as "fact" | "preference" | "project_fact" | "decision" | "action_item")
          : undefined,
      assertion_status:
        typeof item.assertion_status === "string"
          ? (item.assertion_status as
              | "pending"
              | "confirmed"
              | "disputed"
              | "superseded"
              | "retracted")
          : undefined,
      project_id: typeof item.project_id === "string" ? item.project_id : undefined,
      subject: typeof item.subject === "string" ? item.subject : undefined,
      predicate: typeof item.predicate === "string" ? item.predicate : undefined,
      object_value: typeof item.object_value === "string" ? item.object_value : undefined,
      action_status:
        typeof item.action_status === "string"
          ? (item.action_status as "open" | "in_progress" | "blocked" | "done" | "cancelled")
          : undefined,
      assignee: typeof item.assignee === "string" ? item.assignee : undefined,
      due_at: typeof item.due_at === "string" ? item.due_at : undefined,
      evidence_message_ids: Array.isArray(item.evidence_message_ids)
        ? item.evidence_message_ids.filter((value): value is number => typeof value === "number")
        : undefined,
      evidence_excerpt:
        typeof item.evidence_excerpt === "string" ? item.evidence_excerpt : undefined,
      conflicts_with: Array.isArray(item.conflicts_with)
        ? item.conflicts_with.filter((value): value is string => typeof value === "string")
        : undefined,
      meeting_ids: Array.isArray(item.meeting_ids)
        ? item.meeting_ids.filter((value): value is number => typeof value === "number")
        : undefined,
      file_ids: Array.isArray(item.file_ids)
        ? item.file_ids.filter((value): value is number => typeof value === "number")
        : undefined,
    }));
    if (!items.every((item) => item.key.trim() && item.value.trim())) {
      message.error("All memory entries must have non-empty key and value");
      return;
    }
    setActiveAction("import");
    try {
      let imported = 0;
      let failed = 0;
      for (let index = 0; index < items.length; index += 100) {
        const response = await batchImportMemories(items.slice(index, index + 100), userId);
        imported += response.data.imported;
        failed += response.data.failed;
      }
      invalidateSemanticResults();
      await load();
      if (failed > 0) {
        message.warning(`Imported ${imported} memories; ${failed} failed and can be retried`);
        return;
      }
      message.success(`Imported ${imported} memories`);
      setImportOpen(false);
      setImportText("");
    } catch (err) {
      message.error(formatApiErrorMessage(err, "Import stopped; completed batches were kept"));
      invalidateSemanticResults();
      await load();
    } finally {
      setActiveAction(null);
    }
  };

  const handleExport = async () => {
    if (activeAction) return;
    setActiveAction("export");
    try {
      const exported = await exportAllMemories(userId);
      const blob = new Blob([JSON.stringify(exported, null, 2)], {
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
    } finally {
      setActiveAction(null);
    }
  };

  const handleDecay = async () => {
    setDecaying(true);
    try {
      const res = await triggerDecay(userId);
      message.success(`Updated relevance scores for ${res.data.decayed_count} memories`);
      invalidateSemanticResults();
      await load();
    } catch (err) {
      message.error(formatApiErrorMessage(err, "Decay failed"));
    } finally {
      setDecaying(false);
    }
  };

  const handleFeedback = useCallback(async (key: string, useful: boolean) => {
    if (feedbackPendingRef.current) return;
    feedbackPendingRef.current = true;
    setFeedbackKey(key);
    try {
      const response = await recordMemoryFeedback(key, useful);
      const applyFeedback = (memory: MemoryItem): MemoryItem => {
        if (memory.key !== key) return memory;
        return {
          ...memory,
          usefulness_count: response.data.usefulness_count,
          usefulness_score: response.data.usefulness_score,
        };
      };
      setMemories((current) => current.map(applyFeedback));
      setSemanticResults((current) => current?.map(applyFeedback) ?? null);
      message.success(useful ? "Marked this memory as useful" : "Marked this memory as not useful");
    } catch (err) {
      message.error(formatApiErrorMessage(err, "Failed to save memory feedback"));
    } finally {
      feedbackPendingRef.current = false;
      setFeedbackKey(null);
    }
  }, []);

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
      if (keys.length === 0 || activeAction) return;
      setActiveAction("batch-delete");
      try {
        const response = await batchDeleteMemories(keys, userId);
        const missing = new Set(response.data.missing);
        const deleted = new Set(keys.filter((key) => !missing.has(key)));
        if (response.data.deleted > 0) {
          message.success(`${response.data.deleted} memories deleted`);
        }
        setMemories((current) => current.filter((memory) => !deleted.has(memory.key)));
        setSemanticResults(
          (current) => current?.filter((memory) => !deleted.has(memory.key)) ?? null,
        );
        setSelectedKeys(missing);
        if (missing.size === 0) setIsSelectionMode(false);
        else message.warning(`${missing.size} memories no longer existed`);
      } catch (err) {
        message.error(formatApiErrorMessage(err, "Failed to delete selected memories"));
      } finally {
        setActiveAction(null);
      }
    },
    [activeAction, userId],
  );

  return {
    // State
    memories,
    filtered,
    loading,
    loadingMore,
    hasMore: nextCursor !== null,
    total,
    search,
    factTypeFilter,
    setFactTypeFilter: (value: string | undefined) => {
      invalidateSemanticResults();
      setFactTypeFilter(value);
    },
    statusFilter,
    setStatusFilter: (value: string | undefined) => {
      invalidateSemanticResults();
      setStatusFilter(value);
    },
    projectFilter,
    setProjectFilter: (value: string | undefined) => {
      invalidateSemanticResults();
      setProjectFilter(value);
    },
    projectOptions: Array.from(
      new Set(
        memories.map((memory) => memory.project_id).filter((value): value is string => !!value),
      ),
    ).sort(),
    setSearch: (value: string) => {
      invalidateSemanticResults();
      setSearch(value);
    },
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
    activeAction,
    feedbackKey,
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
    loadMore,
    handleSemanticSearch,
    clearSemantic,
    handleCreate,
    handleRetryIndex: async (key: string) => {
      if (activeAction) return;
      setActiveAction("edit");
      invalidateSemanticResults();
      try {
        const response = await retryMemoryIndex(key);
        setMemories((current) => current.map((item) => (item.key === key ? response.data : item)));
        if (response.data.vector_state === "synced") {
          message.success("Memory index synchronized");
        } else {
          message.warning("Index provider is unavailable; automatic retry remains queued");
        }
      } catch (error) {
        message.error(formatApiErrorMessage(error, "Index retry failed"));
      } finally {
        setActiveAction(null);
      }
    },
    handleEdit,
    handleDelete,
    handleImport,
    handleExport,
    handleDecay,
    handleFeedback,
    handleStatusChange,
    handleBatchDelete,
  };
}
