import { createElement, useState, useCallback, useEffect, useMemo, useRef } from "react";
import { Checkbox, message, Modal } from "antd";
import { useIntl } from "react-intl";
import { formatRelativeLocalTime } from "../utils/time";
import {
  listAllSessions,
  getSessionMessages,
  deleteSession,
  batchDeleteSessions,
  summarizeSession,
  getSessionSummary,
  searchSessions,
  ApiError,
  formatApiErrorMessage,
  isRequestCanceled,
  type SessionInfo,
  type SessionSummaryItem,
  type SessionSearchResult,
  type SourceItem,
} from "../api/client";
import { useDebounce } from "./useDebounce";

export interface SessionMessage {
  degraded?: boolean;
  degradation_reason?: string | null;
  id?: number;
  role: string;
  content: string;
  sources: SourceItem[];
}

const EXPANDED_SESSION_STORAGE_KEY = "history-expanded-session-id";

export function useSessionMessages() {
  const { formatMessage } = useIntl();
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(() => {
    try {
      return localStorage.getItem(EXPANDED_SESSION_STORAGE_KEY);
    } catch {
      return null;
    }
  });
  const [messagesMap, setMessagesMap] = useState<Record<string, SessionMessage[]>>({});
  const [loadingMessagesMap, setLoadingMessagesMap] = useState<Record<string, boolean>>({});
  const [messagesErrorMap, setMessagesErrorMap] = useState<Record<string, string | null>>({});
  const [messageCursorMap, setMessageCursorMap] = useState<Record<string, number | null>>({});
  const [messageTotalMap, setMessageTotalMap] = useState<Record<string, number>>({});

  const [searchQuery, setSearchQuery] = useState("");
  const debouncedSearchQuery = useDebounce(searchQuery, 300);
  const [searchResults, setSearchResults] = useState<SessionSearchResult[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const searchAbortRef = useRef<AbortController | null>(null);
  const expandGenerationRef = useRef(0);
  const expandAbortRef = useRef<AbortController | null>(null);
  const sessionsAbortRef = useRef<AbortController | null>(null);

  const [selectedIdsState, setSelectedIds] = useState<Set<string>>(new Set());
  const [isSelectionMode, setIsSelectionMode] = useState(false);
  const [summarizingId, setSummarizingId] = useState<string | null>(null);
  const [summaryMap, setSummaryMap] = useState<Record<string, SessionSummaryItem>>({});
  const [loadingSummaryMap, setLoadingSummaryMap] = useState<Record<string, boolean>>({});
  const [messageRoleFilter, setMessageRoleFilter] = useState<"all" | "human" | "agent">("all");

  const fetchSessions = useCallback(async () => {
    sessionsAbortRef.current?.abort();
    const controller = new AbortController();
    sessionsAbortRef.current = controller;
    setLoading(true);
    try {
      const incoming = await listAllSessions({ signal: controller.signal });
      if (!controller.signal.aborted) setSessions(incoming);
    } catch (err) {
      if (!controller.signal.aborted) {
        message.error(
          formatApiErrorMessage(err, formatMessage({ id: "history.loadSessionsFailed" })),
        );
      }
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, [formatMessage]);

  useEffect(
    () => () => {
      sessionsAbortRef.current?.abort();
      expandAbortRef.current?.abort();
    },
    [],
  );

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      void fetchSessions();
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [fetchSessions]);

  useEffect(() => {
    try {
      if (expandedId) {
        localStorage.setItem(EXPANDED_SESSION_STORAGE_KEY, expandedId);
      } else {
        localStorage.removeItem(EXPANDED_SESSION_STORAGE_KEY);
      }
    } catch {
      // Ignore storage failures.
    }
  }, [expandedId]);

  const effectiveExpandedId =
    expandedId && sessions.some((s) => s.id === expandedId) ? expandedId : null;

  const loadSessionMessages = useCallback(
    async (sessionId: string, controller: AbortController, generation: number) => {
      setLoadingMessagesMap((prev) => ({ ...prev, [sessionId]: true }));
      setMessagesErrorMap((prev) => ({ ...prev, [sessionId]: null }));
      try {
        const res = await getSessionMessages(sessionId, { signal: controller.signal });
        if (controller.signal.aborted || expandGenerationRef.current !== generation) return;
        setMessagesMap((prev) => ({ ...prev, [sessionId]: res?.data?.messages ?? [] }));
        setMessageCursorMap((prev) => ({
          ...prev,
          [sessionId]: res?.data?.next_before_id ?? null,
        }));
        setMessageTotalMap((prev) => ({ ...prev, [sessionId]: res?.data?.total ?? 0 }));
      } catch (err) {
        if (controller.signal.aborted || expandGenerationRef.current !== generation) return;
        if (isRequestCanceled(err)) return;
        const errMsg = formatApiErrorMessage(
          err,
          formatMessage({ id: "history.loadMessagesFailed" }),
        );
        setMessagesErrorMap((prev) => ({ ...prev, [sessionId]: errMsg }));
        message.error(errMsg);
      } finally {
        if (!controller.signal.aborted && expandGenerationRef.current === generation) {
          setLoadingMessagesMap((prev) => ({ ...prev, [sessionId]: false }));
        }
      }
    },
    [formatMessage],
  );

  // A persisted expanded ID is UI state, not loaded data. Fetch its messages
  // once the session list confirms that the ID still exists.
  useEffect(() => {
    if (!effectiveExpandedId || messagesMap[effectiveExpandedId] !== undefined) return;
    expandAbortRef.current?.abort();
    const controller = new AbortController();
    expandAbortRef.current = controller;
    const generation = ++expandGenerationRef.current;
    const timeoutId = window.setTimeout(() => {
      void loadSessionMessages(effectiveExpandedId, controller, generation);
    }, 0);
    return () => {
      window.clearTimeout(timeoutId);
      controller.abort();
    };
  }, [effectiveExpandedId, loadSessionMessages, messagesMap]);

  // Server-side search
  useEffect(() => {
    const q = debouncedSearchQuery.trim();
    if (!q) return;
    searchAbortRef.current?.abort();
    const controller = new AbortController();
    searchAbortRef.current = controller;
    const timeoutId = window.setTimeout(() => {
      setSearchLoading(true);
      searchSessions(q, 10, { signal: controller.signal })
        .then((res: { data: { results: SessionSearchResult[] } }) => {
          if (controller.signal.aborted) return;
          setSearchResults(res.data.results);
        })
        .catch((err: unknown) => {
          if (isRequestCanceled(err)) return;
          message.error(formatApiErrorMessage(err, formatMessage({ id: "history.searchFailed" })));
        })
        .finally(() => {
          if (!controller.signal.aborted) setSearchLoading(false);
        });
    }, 0);
    return () => {
      window.clearTimeout(timeoutId);
      controller.abort();
    };
  }, [debouncedSearchQuery, formatMessage]);

  const displaySessions = useMemo(() => {
    if (!debouncedSearchQuery.trim()) return sessions;
    const map = new Map<string, SessionInfo>();
    searchResults.forEach((r) => {
      if (!map.has(r.session_id)) {
        map.set(r.session_id, {
          id: r.session_id,
          // Search results don't include user_id; use empty string as placeholder
          user_id: "",
          title: r.session_title,
          created_at: r.created_at || new Date().toISOString(),
          updated_at: r.created_at || new Date().toISOString(),
        });
      }
    });
    return Array.from(map.values());
  }, [sessions, searchResults, debouncedSearchQuery]);

  const selectedIds = useMemo(() => {
    const visibleIds = new Set(displaySessions.map((session) => session.id));
    return new Set(Array.from(selectedIdsState).filter((id) => visibleIds.has(id)));
  }, [displaySessions, selectedIdsState]);

  const matchingSnippets = useMemo(() => {
    if (!debouncedSearchQuery.trim()) return {} as Record<string, SessionSearchResult[]>;
    const map: Record<string, SessionSearchResult[]> = {};
    searchResults.forEach((r) => {
      if (!map[r.session_id]) map[r.session_id] = [];
      map[r.session_id].push(r);
    });
    return map;
  }, [searchResults, debouncedSearchQuery]);

  const handleExpand = useCallback(
    async (sessionId: string) => {
      expandAbortRef.current?.abort();
      if (effectiveExpandedId) {
        setLoadingMessagesMap((prev) => ({ ...prev, [effectiveExpandedId]: false }));
        setLoadingSummaryMap((prev) => ({ ...prev, [effectiveExpandedId]: false }));
      }
      if (effectiveExpandedId === sessionId) {
        setExpandedId(null);
        return;
      }
      const controller = new AbortController();
      expandAbortRef.current = controller;
      setExpandedId(sessionId);
      setMessageRoleFilter("all");

      const generation = ++expandGenerationRef.current;

      if (!messagesMap[sessionId]) {
        await loadSessionMessages(sessionId, controller, generation);
      }

      if (!summaryMap[sessionId] && !loadingSummaryMap[sessionId]) {
        setLoadingSummaryMap((prev) => ({ ...prev, [sessionId]: true }));
        try {
          const res = await getSessionSummary(sessionId, { signal: controller.signal });
          if (expandGenerationRef.current !== generation) return;
          setSummaryMap((prev) => ({ ...prev, [sessionId]: res.data }));
        } catch (err: unknown) {
          if (expandGenerationRef.current !== generation) return;
          if (isRequestCanceled(err)) return;
          if (err instanceof ApiError && err.status === 404) {
            // no summary yet — silently ignore
          } else {
            message.error(
              formatApiErrorMessage(err, formatMessage({ id: "history.loadSummaryFailed" })),
            );
          }
        } finally {
          if (expandGenerationRef.current === generation) {
            setLoadingSummaryMap((prev) => ({ ...prev, [sessionId]: false }));
          }
        }
      }
    },
    [
      effectiveExpandedId,
      formatMessage,
      loadSessionMessages,
      messagesMap,
      summaryMap,
      loadingSummaryMap,
    ],
  );

  const handleRetryLoad = useCallback(
    async (sessionId: string) => {
      setLoadingMessagesMap((prev) => ({ ...prev, [sessionId]: true }));
      setMessagesErrorMap((prev) => ({ ...prev, [sessionId]: null }));
      try {
        const res = await getSessionMessages(sessionId);
        setMessagesMap((prev) => ({ ...prev, [sessionId]: res.data.messages ?? [] }));
        setMessageCursorMap((prev) => ({ ...prev, [sessionId]: res.data.next_before_id }));
        setMessageTotalMap((prev) => ({ ...prev, [sessionId]: res.data.total }));
      } catch (err) {
        const errMsg = formatApiErrorMessage(
          err,
          formatMessage({ id: "history.loadMessagesFailed" }),
        );
        setMessagesErrorMap((prev) => ({ ...prev, [sessionId]: errMsg }));
        message.error(errMsg);
      } finally {
        setLoadingMessagesMap((prev) => ({ ...prev, [sessionId]: false }));
      }
    },
    [formatMessage],
  );

  const handleLoadOlder = useCallback(
    async (sessionId: string) => {
      const beforeId = messageCursorMap[sessionId];
      if (!beforeId || loadingMessagesMap[sessionId]) return;
      setLoadingMessagesMap((prev) => ({ ...prev, [sessionId]: true }));
      try {
        const res = await getSessionMessages(sessionId, { beforeId });
        setMessagesMap((prev) => {
          const existing = prev[sessionId] ?? [];
          const seen = new Set(existing.map((item) => item.id).filter(Boolean));
          const older = res.data.messages.filter((item) => !item.id || !seen.has(item.id));
          return { ...prev, [sessionId]: [...older, ...existing] };
        });
        setMessageCursorMap((prev) => ({ ...prev, [sessionId]: res.data.next_before_id }));
        setMessageTotalMap((prev) => ({ ...prev, [sessionId]: res.data.total }));
      } catch (err) {
        if (!isRequestCanceled(err)) {
          message.error(
            formatApiErrorMessage(err, formatMessage({ id: "history.loadOlderFailed" })),
          );
        }
      } finally {
        setLoadingMessagesMap((prev) => ({ ...prev, [sessionId]: false }));
      }
    },
    [formatMessage, loadingMessagesMap, messageCursorMap],
  );

  const handleDelete = useCallback(
    async (sessionId: string) => {
      let retractDerivedMemories = false;
      Modal.confirm({
        title: formatMessage({ id: "history.deleteTitle" }),
        content: createElement(
          "div",
          null,
          createElement("p", null, formatMessage({ id: "history.deleteConfirm" })),
          createElement(
            Checkbox,
            {
              onChange: (event) => {
                retractDerivedMemories = event.target.checked;
              },
            },
            formatMessage({ id: "history.retractDerivedMemories" }),
          ),
        ),
        okType: "danger",
        okText: formatMessage({ id: "common.delete" }),
        onOk: async () => {
          try {
            await deleteSession(sessionId, retractDerivedMemories);
            setSessions((prev) => prev.filter((s) => s.id !== sessionId));
            setSearchResults((prev) => prev.filter((result) => result.session_id !== sessionId));
            setMessagesMap((prev) => {
              const next = { ...prev };
              delete next[sessionId];
              return next;
            });
            setMessagesErrorMap((prev) => {
              const next = { ...prev };
              delete next[sessionId];
              return next;
            });
            setSummaryMap((prev) => {
              const next = { ...prev };
              delete next[sessionId];
              return next;
            });
            if (effectiveExpandedId === sessionId) setExpandedId(null);
            setSelectedIds((prev) => {
              const next = new Set(prev);
              next.delete(sessionId);
              return next;
            });
            message.success(formatMessage({ id: "history.deleted" }));
          } catch (err) {
            message.error(
              formatApiErrorMessage(err, formatMessage({ id: "history.deleteFailed" })),
            );
          }
        },
      });
    },
    [effectiveExpandedId, formatMessage],
  );

  const handleBatchDelete = useCallback(async () => {
    if (selectedIds.size === 0) return;

    const ids = Array.from(selectedIds);
    let missing: string[];
    try {
      const response = await batchDeleteSessions(ids);
      missing = response.data.missing;
    } catch (err) {
      message.error(
        formatApiErrorMessage(
          err,
          formatMessage({ id: "history.batchDeleteFailed" }, { count: ids.length }),
        ),
      );
      return;
    }
    const missingSet = new Set(missing);
    const succeeded = new Set(ids.filter((id) => !missingSet.has(id)));

    if (succeeded.size > 0) {
      setSessions((prev) => prev.filter((s) => !succeeded.has(s.id)));
      setSearchResults((prev) => prev.filter((result) => !succeeded.has(result.session_id)));
      setMessagesMap((prev) => {
        const next = { ...prev };
        succeeded.forEach((id) => delete next[id]);
        return next;
      });
      setMessagesErrorMap((prev) => {
        const next = { ...prev };
        succeeded.forEach((id) => delete next[id]);
        return next;
      });
      setSummaryMap((prev) => {
        const next = { ...prev };
        succeeded.forEach((id) => delete next[id]);
        return next;
      });
      if (effectiveExpandedId && succeeded.has(effectiveExpandedId)) setExpandedId(null);
    }

    if (missing.length > 0) {
      message.warning(
        formatMessage({ id: "history.batchDeleteFailed" }, { count: missing.length }),
      );
    }
    if (succeeded.size > 0) {
      message.success(formatMessage({ id: "history.batchDeleted" }, { count: succeeded.size }));
    }

    setSelectedIds(missingSet);
    if (missing.length === 0) setIsSelectionMode(false);
  }, [selectedIds, effectiveExpandedId, formatMessage]);

  const handleSummarize = useCallback(
    async (sessionId: string) => {
      setSummarizingId(sessionId);
      try {
        await summarizeSession(sessionId);
        const summaryRes = await getSessionSummary(sessionId);
        setSummaryMap((prev) => ({ ...prev, [sessionId]: summaryRes.data }));
        message.success(formatMessage({ id: "history.summaryGenerated" }));
      } catch (err) {
        message.error(formatApiErrorMessage(err, formatMessage({ id: "history.summaryFailed" })));
      } finally {
        setSummarizingId(null);
      }
    },
    [formatMessage],
  );

  const toggleSelection = useCallback((sessionId: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(sessionId)) {
        next.delete(sessionId);
      } else {
        next.add(sessionId);
      }
      return next;
    });
  }, []);

  const selectAll = useCallback((sessionIds: string[]) => {
    setSelectedIds(new Set(sessionIds));
  }, []);

  const clearSelection = useCallback(() => {
    setSelectedIds(new Set());
  }, []);

  const formatDate = useCallback((iso: string) => formatRelativeLocalTime(iso), []);

  const getMessageCount = useCallback(
    (sessionId: string) => {
      return messageTotalMap[sessionId] ?? messagesMap[sessionId]?.length ?? 0;
    },
    [messageTotalMap, messagesMap],
  );

  return {
    // State
    sessions,
    loading,
    expandedId: effectiveExpandedId,
    messagesMap,
    loadingMessagesMap,
    messagesErrorMap,
    messageCursorMap,
    searchQuery,
    setSearchQuery,
    debouncedSearchQuery,
    searchLoading,
    selectedIds,
    isSelectionMode,
    setIsSelectionMode,
    summarizingId,
    summaryMap,
    messageRoleFilter,
    setMessageRoleFilter,
    // Derived
    displaySessions,
    matchingSnippets,
    // Handlers
    fetchSessions,
    handleExpand,
    handleRetryLoad,
    handleLoadOlder,
    handleDelete,
    handleBatchDelete,
    handleSummarize,
    toggleSelection,
    selectAll,
    clearSelection,
    formatDate,
    getMessageCount,
  };
}
