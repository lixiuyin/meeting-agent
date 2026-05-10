import { useState, useCallback, useEffect, useMemo, useRef } from "react";
import { message, Modal } from "antd";
import { formatRelativeLocalTime } from "../utils/time";
import {
  listSessions,
  getSessionMessages,
  deleteSession,
  summarizeSession,
  getSessionSummary,
  searchSessions,
  ApiError,
  formatApiErrorMessage,
  type SessionInfo,
  type SessionSummaryItem,
  type SessionSearchResult,
  type SourceItem,
} from "../api/client";
import { useDebounce } from "./useDebounce";

export interface SessionMessage {
  role: string;
  content: string;
  sources: SourceItem[];
}

const EXPANDED_SESSION_STORAGE_KEY = "history-expanded-session-id";

export function useSessionMessages() {
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

  const [searchQuery, setSearchQuery] = useState("");
  const debouncedSearchQuery = useDebounce(searchQuery, 300);
  const [searchResults, setSearchResults] = useState<SessionSearchResult[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const searchAbortRef = useRef<AbortController | null>(null);
  const expandGenerationRef = useRef(0);

  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [isSelectionMode, setIsSelectionMode] = useState(false);
  const [summarizingId, setSummarizingId] = useState<string | null>(null);
  const [summaryMap, setSummaryMap] = useState<Record<string, SessionSummaryItem>>({});
  const [loadingSummaryMap, setLoadingSummaryMap] = useState<Record<string, boolean>>({});
  const [messageRoleFilter, setMessageRoleFilter] = useState<"all" | "human" | "agent">("all");

  const fetchSessions = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listSessions();
      setSessions(res.data.sessions);
    } catch (err) {
      message.error(formatApiErrorMessage(err, "Failed to load sessions"));
    } finally {
      setLoading(false);
    }
  }, []);

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
          if ((err as Error)?.name === "AbortError") return;
          message.error(formatApiErrorMessage(err, "Search failed"));
        })
        .finally(() => setSearchLoading(false));
    }, 0);
    return () => {
      window.clearTimeout(timeoutId);
      controller.abort();
    };
  }, [debouncedSearchQuery]);

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
      if (effectiveExpandedId === sessionId) {
        setExpandedId(null);
        return;
      }
      setExpandedId(sessionId);
      setMessageRoleFilter("all");

      const generation = ++expandGenerationRef.current;

      if (!messagesMap[sessionId]) {
        setLoadingMessagesMap((prev) => ({ ...prev, [sessionId]: true }));
        setMessagesErrorMap((prev) => ({ ...prev, [sessionId]: null }));
        try {
          const res = await getSessionMessages(sessionId);
          if (expandGenerationRef.current !== generation) return;
          if (res?.data?.messages) {
            setMessagesMap((prev) => ({ ...prev, [sessionId]: res.data.messages }));
          } else {
            setMessagesMap((prev) => ({ ...prev, [sessionId]: [] }));
          }
        } catch (err) {
          if (expandGenerationRef.current !== generation) return;
          const errMsg = formatApiErrorMessage(err, "Failed to load messages");
          setMessagesErrorMap((prev) => ({ ...prev, [sessionId]: errMsg }));
          message.error(errMsg);
        } finally {
          if (expandGenerationRef.current === generation) {
            setLoadingMessagesMap((prev) => ({ ...prev, [sessionId]: false }));
          }
        }
      }

      if (!summaryMap[sessionId] && !loadingSummaryMap[sessionId]) {
        setLoadingSummaryMap((prev) => ({ ...prev, [sessionId]: true }));
        try {
          const res = await getSessionSummary(sessionId);
          if (expandGenerationRef.current !== generation) return;
          setSummaryMap((prev) => ({ ...prev, [sessionId]: res.data }));
        } catch (err: unknown) {
          if (expandGenerationRef.current !== generation) return;
          if (err instanceof ApiError && err.status === 404) {
            // no summary yet — silently ignore
          } else {
            message.error(formatApiErrorMessage(err, "Failed to load summary"));
          }
        } finally {
          if (expandGenerationRef.current === generation) {
            setLoadingSummaryMap((prev) => ({ ...prev, [sessionId]: false }));
          }
        }
      }
    },
    [effectiveExpandedId, messagesMap, summaryMap, loadingSummaryMap],
  );

  const handleRetryLoad = useCallback(async (sessionId: string) => {
    setLoadingMessagesMap((prev) => ({ ...prev, [sessionId]: true }));
    setMessagesErrorMap((prev) => ({ ...prev, [sessionId]: null }));
    try {
      const res = await getSessionMessages(sessionId);
      setMessagesMap((prev) => ({ ...prev, [sessionId]: res.data.messages ?? [] }));
    } catch (err) {
      const errMsg = formatApiErrorMessage(err, "Failed to load messages");
      setMessagesErrorMap((prev) => ({ ...prev, [sessionId]: errMsg }));
      message.error(errMsg);
    } finally {
      setLoadingMessagesMap((prev) => ({ ...prev, [sessionId]: false }));
    }
  }, []);

  const handleDelete = useCallback(
    async (sessionId: string) => {
      Modal.confirm({
        title: "Delete Session",
        content:
          "This will permanently remove this conversation session and all its messages. This action cannot be undone.",
        okType: "danger",
        okText: "Delete",
        onOk: async () => {
          try {
            await deleteSession(sessionId);
            setSessions((prev) => prev.filter((s) => s.id !== sessionId));
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
            message.success("Session deleted");
          } catch (err) {
            message.error(formatApiErrorMessage(err, "Failed to delete session"));
          }
        },
      });
    },
    [effectiveExpandedId],
  );

  const handleBatchDelete = useCallback(async () => {
    if (selectedIds.size === 0) return;

    const ids = Array.from(selectedIds);
    const results = await Promise.allSettled(ids.map((id) => deleteSession(id)));
    const succeeded = new Set<string>();
    const failed: string[] = [];
    ids.forEach((id, i) => {
      if (results[i].status === "fulfilled") {
        succeeded.add(id);
      } else {
        failed.push(id);
      }
    });

    if (succeeded.size > 0) {
      setSessions((prev) => prev.filter((s) => !succeeded.has(s.id)));
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

    if (failed.length > 0) {
      message.error(
        `Failed to delete ${failed.length} session${failed.length > 1 ? "s" : ""}. Check logs with Request ID if available.`,
      );
    }
    if (succeeded.size > 0) {
      message.success(`Deleted ${succeeded.size} session${succeeded.size > 1 ? "s" : ""}`);
    }

    setSelectedIds(new Set());
    setIsSelectionMode(false);
  }, [selectedIds, effectiveExpandedId]);

  const handleSummarize = useCallback(async (sessionId: string) => {
    setSummarizingId(sessionId);
    try {
      await summarizeSession(sessionId);
      const summaryRes = await getSessionSummary(sessionId);
      setSummaryMap((prev) => ({ ...prev, [sessionId]: summaryRes.data }));
      message.success("Session summary generated");
    } catch (err) {
      message.error(formatApiErrorMessage(err, "Failed to generate summary"));
    } finally {
      setSummarizingId(null);
    }
  }, []);

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

  const selectAll = useCallback(() => {
    setSelectedIds(new Set(displaySessions.map((s) => s.id)));
  }, [displaySessions]);

  const clearSelection = useCallback(() => {
    setSelectedIds(new Set());
  }, []);

  const formatDate = useCallback((iso: string) => formatRelativeLocalTime(iso), []);

  const getMessageCount = useCallback(
    (sessionId: string) => {
      return messagesMap[sessionId]?.length || 0;
    },
    [messagesMap],
  );

  return {
    // State
    sessions,
    loading,
    expandedId: effectiveExpandedId,
    messagesMap,
    loadingMessagesMap,
    messagesErrorMap,
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
