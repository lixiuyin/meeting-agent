import { useState, useEffect, useRef, useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import { message } from "antd";
import { useIntl } from "react-intl";
import type { TextAreaRef } from "antd/es/input/TextArea";
import {
  branchSession,
  cancelChatRun,
  getSessionMessages,
  withdrawChatRun,
  ApiError,
  formatApiErrorMessage,
} from "../api/client";
import { useChatStream } from "./useChatStream";
import type { ChatMessage } from "./useChatStream";
import { useChatOptions } from "./useChatOptions";
import { useSessionSelection } from "./useSessionSelection";
import { isRequestCanceled } from "../api/client-core";
import { toLocalDateTimeInput } from "../utils/time";

export type { ChatOptions } from "./useChatOptions";
export type { ChatMessage } from "./useChatStream";

const SESSION_ID_RE = /^[A-Za-z0-9_-]{8,64}$/;

function restoredMessages(
  sessionId: string,
  messages: Awaited<ReturnType<typeof getSessionMessages>>["data"]["messages"],
): ChatMessage[] {
  return messages.map((item) => ({
    role: (item.role === "human" ? "user" : "agent") as "user" | "agent",
    content: item.content,
    sources: item.sources,
    degraded: item.degraded,
    degradationReason: item.degradation_reason ?? undefined,
    id: `${sessionId}-${item.id}`,
    serverId: item.id,
  }));
}

export function useSessionManager() {
  const { formatMessage } = useIntl();
  const [searchParams, setSearchParams] = useSearchParams();
  const rawSessionId = searchParams.get("sessionId") ?? undefined;
  const resumedSessionId =
    rawSessionId && SESSION_ID_RE.test(rawSessionId) ? rawSessionId : undefined;

  const [input, setInput] = useState("");
  const [restoring, setRestoring] = useState(!!resumedSessionId);
  const [restoreError, setRestoreError] = useState<string | null>(null);
  const [restoreAttempt, setRestoreAttempt] = useState(0);
  const [olderBeforeId, setOlderBeforeId] = useState<number | undefined>();
  const [loadingOlder, setLoadingOlder] = useState(false);
  const olderAbortRef = useRef<AbortController | null>(null);
  const prependingHistoryRef = useRef(false);
  const [pendingRun, setPendingRun] = useState<{
    id: string;
    question: string;
    status: string;
    cursor?: number;
  } | null>(null);
  const [inputFocused, setInputFocused] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [editingMessage, setEditingMessage] = useState<ChatMessage | null>(null);
  const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const {
    paramsExpanded,
    setParamsExpanded,
    useWebSearch,
    setUseWebSearch,
    selectedTypeFilters,
    setSelectedTypeFilters,
    dateFrom,
    setDateFrom,
    dateTo,
    setDateTo,
    validAt,
    setValidAt,
    knownAt,
    setKnownAt,
    continuationMode,
    setContinuationMode,
    ragMode,
    setRagMode,
    retrievalProfile,
    setRetrievalProfile,
    memoryMode,
    setMemoryMode,
    chatOptions,
    activeParamCount,
  } = useChatOptions();

  const {
    selectedMeetingIds,
    setSelectedMeetingIds,
    selectedFileIds,
    setSelectedFileIds,
    meetings,
    refreshMeetings,
    meetingFilesMap,
    loadingMeetings,
    loadingFiles,
    removeSelectedMeeting,
    removeSelectedFile,
    meetingOptions,
    selectedMeetings,
    fileOptions,
    selectedFiles,
    resolveEffectiveFileIds,
  } = useSessionSelection();

  const {
    messages,
    isStreaming,
    sessionId: streamSessionId,
    streamError,
    streamErrorCode,
    streamErrorDetail,
    streamRequestId,
    streamNotice,
    setStreamError,
    setStreamNotice,
    setSessionId,
    startStream,
    abortStream,
    clearMessages,
    setMessages,
  } = useChatStream();
  const streamSessionIdRef = useRef<string | undefined>(streamSessionId);
  const restoreGenerationRef = useRef(0);
  const sessionActionGenerationRef = useRef(0);
  useEffect(() => {
    streamSessionIdRef.current = streamSessionId;
  }, [streamSessionId]);

  // Once the server assigns a new session, persist it in the URL. This makes
  // refresh, copy-link and browser history resume the same conversation.
  useEffect(() => {
    if (!streamSessionId || resumedSessionId === streamSessionId) return;
    setSearchParams(
      (current) => {
        const next = new URLSearchParams(current);
        next.set("sessionId", streamSessionId);
        return next;
      },
      { replace: true },
    );
  }, [resumedSessionId, setSearchParams, streamSessionId]);

  const bottomRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const autoScrollRef = useRef(true);
  const [showScrollToBottom, setShowScrollToBottom] = useState(false);
  const sendingRef = useRef(false);
  const textareaRef = useRef<TextAreaRef>(null);
  const isStreamingRef = useRef(isStreaming);
  useEffect(() => {
    isStreamingRef.current = isStreaming;
  }, [isStreaming]);
  useEffect(() => {
    return () => {
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
    };
  }, []);
  const lastSendIdRef = useRef(0);
  const AUTO_SCROLL_THRESHOLD_PX = 96;
  const retryRestore = useCallback(() => {
    setRestoreError(null);
    setRestoreAttempt((attempt) => attempt + 1);
  }, []);

  const loadOlderMessages = useCallback(async () => {
    if (!resumedSessionId || olderBeforeId === undefined || olderAbortRef.current) return;
    const generation = restoreGenerationRef.current;
    const controller = new AbortController();
    const scrollContainer = scrollContainerRef.current;
    const previousScroll = scrollContainer
      ? { height: scrollContainer.scrollHeight, top: scrollContainer.scrollTop }
      : null;
    prependingHistoryRef.current = true;
    olderAbortRef.current = controller;
    setLoadingOlder(true);
    const timeout = setTimeout(() => controller.abort(), 10_000);
    try {
      const res = await getSessionMessages(resumedSessionId, {
        beforeId: olderBeforeId,
        limit: 200,
        signal: controller.signal,
      });
      if (controller.signal.aborted || generation !== restoreGenerationRef.current) return;
      const older = restoredMessages(resumedSessionId, res.data.messages);
      setMessages((current) => {
        const existing = new Set(current.map((m) => m.id));
        return [...older.filter((m) => !existing.has(m.id)), ...current];
      });
      window.requestAnimationFrame(() => {
        const current = scrollContainerRef.current;
        if (current && previousScroll) {
          current.scrollTop = current.scrollHeight - previousScroll.height + previousScroll.top;
        }
        prependingHistoryRef.current = false;
      });
      setOlderBeforeId(res.data.next_before_id ?? undefined);
    } catch (error) {
      if (!controller.signal.aborted)
        message.error(formatApiErrorMessage(error, "Failed to load older messages"));
    } finally {
      clearTimeout(timeout);
      if (olderAbortRef.current === controller) {
        olderAbortRef.current = null;
        setLoadingOlder(false);
      }
      if (controller.signal.aborted) prependingHistoryRef.current = false;
    }
  }, [resumedSessionId, olderBeforeId, setMessages]);

  // Session restoration from URL
  useEffect(() => {
    if (!resumedSessionId) return;
    // Updating the URL after a completed first turn must not reload the
    // conversation that is already present in memory.
    if (streamSessionIdRef.current === resumedSessionId) return;

    const generation = ++restoreGenerationRef.current;
    ++sessionActionGenerationRef.current;
    const controller = new AbortController();
    let active = true;
    let timedOut = false;
    let requestTimeoutId: ReturnType<typeof setTimeout> | undefined;

    // Defer the state transition to avoid a synchronous effect update while
    // still starting restoration immediately after the URL changes.
    const restoreStartId = window.setTimeout(() => {
      if (!active || restoreGenerationRef.current !== generation) return;
      abortStream({ silent: true });
      setMessages([]);
      setSessionId(undefined);
      streamSessionIdRef.current = undefined;
      setRestoreError(null);
      setRestoring(true);
      setPendingRun(null);
      setOlderBeforeId(undefined);
      setLoadingOlder(false);
      requestTimeoutId = setTimeout(() => {
        timedOut = true;
        controller.abort();
      }, 10_000);
      void (async () => {
        try {
          const pages: Awaited<
            ReturnType<typeof getSessionMessages>
          >["data"]["messages"][number][] = [];
          const res = await getSessionMessages(resumedSessionId, {
            signal: controller.signal,
            limit: 200,
          });
          pages.unshift(...(res.data?.messages ?? []));
          if (controller.signal.aborted || restoreGenerationRef.current !== generation) return;
          setOlderBeforeId(res.data?.next_before_id ?? undefined);
          setPendingRun(res.data?.pending_run ?? null);
          const config = res.data?.session_config;
          if (config?.schema_version === 1) {
            setSelectedMeetingIds(config.meeting_ids ?? []);
            setSelectedFileIds(config.file_ids ?? []);
            setSelectedTypeFilters(config.file_types ?? []);
            setDateFrom(config.date_from ?? "");
            setDateTo(config.date_to ?? "");
            setValidAt(toLocalDateTimeInput(config.valid_at) ?? "");
            setKnownAt(toLocalDateTimeInput(config.known_at) ?? "");
            setUseWebSearch(config.use_web_search ?? false);
            if (config.rag_mode) setRagMode(config.rag_mode);
            if (config.retrieval_profile) setRetrievalProfile(config.retrieval_profile);
            if (config.memory_mode) setMemoryMode(config.memory_mode);
            setContinuationMode(config.continuation_mode ?? "latest");
          }
          const restored = restoredMessages(resumedSessionId, pages);
          setMessages(restored);
          setSessionId(resumedSessionId);
          streamSessionIdRef.current = resumedSessionId;
          setRestoreError(null);
        } catch (err: unknown) {
          if (!active || restoreGenerationRef.current !== generation) return;
          if (timedOut) {
            setRestoreError(formatMessage({ id: "home.restoreTimeout" }));
            return;
          }
          if (controller.signal.aborted) return;
          const notFound = err instanceof ApiError && err.status === 404;
          const msg = notFound
            ? formatMessage({ id: "home.sessionNotFound" })
            : formatApiErrorMessage(err, formatMessage({ id: "home.restoreFailed" }));
          setRestoreError(msg);
          if (notFound) {
            setSearchParams(
              (current) => {
                const next = new URLSearchParams(current);
                next.delete("sessionId");
                return next;
              },
              { replace: true },
            );
          }
        } finally {
          if (active && restoreGenerationRef.current === generation) setRestoring(false);
        }
      })();
    }, 0);
    return () => {
      active = false;
      window.clearTimeout(restoreStartId);
      if (requestTimeoutId) clearTimeout(requestTimeoutId);
      controller.abort();
      olderAbortRef.current?.abort();
      olderAbortRef.current = null;
    };
  }, [
    abortStream,
    formatMessage,
    restoreAttempt,
    resumedSessionId,
    setMessages,
    setDateFrom,
    setDateTo,
    setValidAt,
    setKnownAt,
    setContinuationMode,
    setMemoryMode,
    setRagMode,
    setRetrievalProfile,
    setSelectedFileIds,
    setSelectedMeetingIds,
    setSelectedTypeFilters,
    setSearchParams,
    setSessionId,
    setUseWebSearch,
  ]);

  // Auto-scroll behavior with back-to-bottom button (O-FE-3)
  useEffect(() => {
    const container = scrollContainerRef.current;
    if (prependingHistoryRef.current) return;
    if (!container) return;

    const updateAutoScroll = () => {
      const distanceToBottom =
        container.scrollHeight - container.scrollTop - container.clientHeight;
      const atBottom = distanceToBottom <= AUTO_SCROLL_THRESHOLD_PX;
      autoScrollRef.current = atBottom;
      // O-FE-3: Show back-to-bottom badge when user scrolls up during streaming.
      if (isStreamingRef.current) {
        setShowScrollToBottom(!atBottom);
      }
    };

    updateAutoScroll();
    container.addEventListener("scroll", updateAutoScroll, { passive: true });

    return () => {
      container.removeEventListener("scroll", updateAutoScroll);
    };
  }, []);

  const scrollToBottom = useCallback(() => {
    autoScrollRef.current = true;
    setShowScrollToBottom(false);
    const container = scrollContainerRef.current;
    if (container) {
      container.scrollTo({
        top: container.scrollHeight,
        behavior: "smooth",
      });
    } else {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, []);

  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!container) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
      return;
    }

    if (isStreaming && !autoScrollRef.current) {
      return;
    }
    // O-FE-3: Hide the badge when auto-scroll is active.
    setShowScrollToBottom(false);

    container.scrollTo({
      top: container.scrollHeight,
      behavior: isStreaming ? "auto" : "smooth",
    });
  }, [messages, isStreaming]);

  // Cleanup abort on unmount
  useEffect(() => {
    return () => abortStream({ silent: true });
  }, [abortStream]);

  const createBranch = useCallback(
    async (messageId: number, reason: "edit" | "regenerate") => {
      const generation = ++sessionActionGenerationRef.current;
      const sourceSessionId = streamSessionIdRef.current ?? resumedSessionId;
      if (!sourceSessionId) throw new Error("The message has not been saved yet");
      const response = await branchSession(sourceSessionId, messageId, reason);
      if (generation !== sessionActionGenerationRef.current) {
        throw new DOMException("Session navigation changed", "AbortError");
      }
      const branchId = response.data.session.id;
      setMessages(restoredMessages(branchId, response.data.messages));
      setSessionId(branchId);
      streamSessionIdRef.current = branchId;
      setOlderBeforeId(response.data.next_before_id ?? undefined);
      setPendingRun(null);
      setContinuationMode("latest");
      setSearchParams((current) => {
        const next = new URLSearchParams(current);
        next.set("sessionId", branchId);
        return next;
      });
      message.success(formatMessage({ id: "chat.branchCreated" }));
      return branchId;
    },
    [
      formatMessage,
      resumedSessionId,
      setContinuationMode,
      setMessages,
      setSearchParams,
      setSessionId,
    ],
  );

  const handleSend = useCallback(
    async (questionOverride?: string) => {
      const question = questionOverride?.trim() || input.trim();
      if (!question || sendingRef.current) return;
      if (loadingFiles) {
        message.info(formatMessage({ id: "home.fileListUpdating" }));
        return;
      }
      // Monotonically incrementing send ID prevents overlapping sends from
      // the same microtask — the ref is set synchronously before any await.
      const sendId = ++lastSendIdRef.current;
      // A new send supersedes delayed stop/withdraw/navigation responses in
      // this session. Those operations must never restore an older snapshot
      // over the newly submitted turn.
      ++sessionActionGenerationRef.current;
      sendingRef.current = true;
      autoScrollRef.current = true;

      try {
        setStreamError(null);
        setPendingRun(null);
        let targetSessionId = streamSessionIdRef.current ?? resumedSessionId;
        let requestOptions = chatOptions;
        if (!questionOverride && editingMessage?.serverId) {
          targetSessionId = await createBranch(editingMessage.serverId, "edit");
          requestOptions = { ...chatOptions, continuationMode: "latest" };
          setEditingMessage(null);
        }
        if (!questionOverride) setInput("");
        const clientTurnId = crypto.randomUUID();
        await startStream({
          question,
          meetingIds: selectedMeetingIds.length > 0 ? selectedMeetingIds : undefined,
          fileIds: resolveEffectiveFileIds(),
          sessionId: targetSessionId,
          options: requestOptions,
          clientTurnId,
          onRunIdentified: (id) => setPendingRun({ id, question, status: "running" }),
          onEventCursor: (cursor) =>
            setPendingRun((current) => (current ? { ...current, cursor } : current)),
          onCompleted: () => setPendingRun(null),
          onDone: () => {
            sendingRef.current = false;
          },
        });
      } catch (error) {
        if (isRequestCanceled(error)) return;
        if (editingMessage && !questionOverride) setInput(question);
        message.error(formatApiErrorMessage(error, formatMessage({ id: "chat.branchFailed" })));
      } finally {
        // Only clear the sending flag if this is still the latest send
        if (lastSendIdRef.current === sendId) {
          sendingRef.current = false;
        }
      }
    },
    [
      input,
      loadingFiles,
      selectedMeetingIds,
      resolveEffectiveFileIds,
      chatOptions,
      setInput,
      setStreamError,
      startStream,
      resumedSessionId,
      editingMessage,
      createBranch,
      formatMessage,
    ],
  );

  // Keep ref to latest handleSend for keyboard shortcuts
  const handleSendRef = useRef(handleSend);
  useEffect(() => {
    handleSendRef.current = handleSend;
  }, [handleSend]);

  const handleRegenerate = useCallback(async () => {
    if (loadingFiles) {
      message.info(formatMessage({ id: "home.fileListUpdating" }));
      return;
    }
    const lastUserIndex = [...messages].reverse().findIndex((m) => m.role === "user");
    if (lastUserIndex === -1) return;

    const actualIndex = messages.length - 1 - lastUserIndex;
    const lastUserMessage = messages[actualIndex];
    if (!lastUserMessage.serverId) {
      if (isStreaming) {
        message.info(formatMessage({ id: "chat.waitForSavedMessage" }));
        return;
      }
      // Terminal stream failures can leave a local user turn that was never
      // committed. Remove that failed presentation pair and issue a fresh run.
      setMessages((current) => current.slice(0, actualIndex));
      await handleSend(lastUserMessage.content);
      return;
    }

    const regenId = ++lastSendIdRef.current;
    sendingRef.current = true;
    autoScrollRef.current = true;
    setStreamError(null);
    try {
      const branchId = await createBranch(lastUserMessage.serverId, "regenerate");
      const clientTurnId = crypto.randomUUID();
      await startStream({
        question: lastUserMessage.content,
        meetingIds: selectedMeetingIds.length > 0 ? selectedMeetingIds : undefined,
        fileIds: resolveEffectiveFileIds(),
        sessionId: branchId,
        options: { ...chatOptions, continuationMode: "latest" },
        clientTurnId,
        onRunIdentified: (id) =>
          setPendingRun({ id, question: lastUserMessage.content, status: "running" }),
        onEventCursor: (cursor) =>
          setPendingRun((current) => (current ? { ...current, cursor } : current)),
        onCompleted: () => setPendingRun(null),
        onDone: () => {
          sendingRef.current = false;
        },
      });
    } catch (error) {
      if (isRequestCanceled(error)) return;
      message.error(formatApiErrorMessage(error, formatMessage({ id: "chat.branchFailed" })));
    } finally {
      if (lastSendIdRef.current === regenId) {
        sendingRef.current = false;
      }
    }
  }, [
    loadingFiles,
    isStreaming,
    messages,
    selectedMeetingIds,
    resolveEffectiveFileIds,
    chatOptions,
    setStreamError,
    setMessages,
    startStream,
    setPendingRun,
    createBranch,
    formatMessage,
    handleSend,
  ]);

  const handleEditUserMessage = useCallback(
    (chatMessage: ChatMessage) => {
      if (isStreaming || !chatMessage.serverId) {
        message.info(formatMessage({ id: "chat.waitForSavedMessage" }));
        return;
      }
      setEditingMessage(chatMessage);
      setInput(chatMessage.content);
      window.requestAnimationFrame(() => textareaRef.current?.focus());
    },
    [formatMessage, isStreaming],
  );

  const cancelEditing = useCallback(() => {
    setEditingMessage(null);
    setInput("");
  }, []);

  const handleStop = useCallback(async () => {
    const generation = ++sessionActionGenerationRef.current;
    const runId = pendingRun?.id;
    const currentSessionId = streamSessionIdRef.current ?? resumedSessionId;
    abortStream({
      notice: formatMessage({ id: "chat.responseCanceled" }),
      cancelRemotely: !runId,
    });
    setPendingRun(null);
    if (!runId) {
      setMessages((current) => {
        const agent = current[current.length - 1];
        const user = current[current.length - 2];
        return agent?.role === "agent" && user?.role === "user" && !user.serverId
          ? current.slice(0, -2)
          : current;
      });
      return;
    }
    try {
      await cancelChatRun(runId);
      if (generation !== sessionActionGenerationRef.current) return;
      if (currentSessionId) {
        const response = await getSessionMessages(currentSessionId, { limit: 200 });
        if (generation !== sessionActionGenerationRef.current) return;
        setMessages(restoredMessages(currentSessionId, response.data.messages));
        setOlderBeforeId(response.data.next_before_id ?? undefined);
      }
    } catch (error) {
      if (generation !== sessionActionGenerationRef.current || isRequestCanceled(error)) return;
      message.error(formatApiErrorMessage(error, formatMessage({ id: "chat.stopFailed" })));
    }
  }, [abortStream, formatMessage, pendingRun?.id, resumedSessionId, setMessages]);

  const handleWithdrawCurrent = useCallback(async () => {
    const generation = ++sessionActionGenerationRef.current;
    const runId = pendingRun?.id;
    if (!runId) return;
    abortStream({
      notice: formatMessage({ id: "chat.responseWithdrawn" }),
      cancelRemotely: false,
    });
    setPendingRun(null);
    try {
      const response = await withdrawChatRun(runId);
      if (generation !== sessionActionGenerationRef.current) return;
      const branchId = response.data.session.id;
      setMessages(restoredMessages(branchId, response.data.messages));
      setSessionId(branchId);
      streamSessionIdRef.current = branchId;
      setOlderBeforeId(response.data.next_before_id ?? undefined);
      setContinuationMode("latest");
      setSearchParams((current) => {
        const next = new URLSearchParams(current);
        next.set("sessionId", branchId);
        return next;
      });
      message.success(formatMessage({ id: "chat.responseWithdrawn" }));
    } catch (error) {
      if (generation !== sessionActionGenerationRef.current || isRequestCanceled(error)) return;
      message.error(formatApiErrorMessage(error, formatMessage({ id: "chat.withdrawFailed" })));
    }
  }, [
    abortStream,
    formatMessage,
    pendingRun?.id,
    setContinuationMode,
    setMessages,
    setSearchParams,
    setSessionId,
  ]);

  const handleCopy = useCallback(
    async (content: string, id: string) => {
      try {
        await navigator.clipboard.writeText(content);
        setCopiedId(id);
        message.success({ content: formatMessage({ id: "chat.copied" }), duration: 2 });
        if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
        copyTimerRef.current = setTimeout(() => setCopiedId(null), 2000);
      } catch {
        message.error(formatMessage({ id: "chat.copyFailed" }));
      }
    },
    [formatMessage],
  );

  const handleCopyRequestId = useCallback(
    async (requestId: string) => {
      try {
        await navigator.clipboard.writeText(requestId);
        message.success({ content: formatMessage({ id: "chat.requestIdCopied" }), duration: 2 });
      } catch {
        message.error(formatMessage({ id: "chat.requestIdCopyFailed" }));
      }
    },
    [formatMessage],
  );

  const handleCopySourceSnippet = useCallback(
    async (content: string) => {
      try {
        await navigator.clipboard.writeText(content);
        message.success({ content: formatMessage({ id: "chat.snippetCopied" }), duration: 2 });
      } catch {
        message.error(formatMessage({ id: "chat.snippetCopyFailed" }));
      }
    },
    [formatMessage],
  );

  const handleNewSession = useCallback(() => {
    ++sessionActionGenerationRef.current;
    ++restoreGenerationRef.current;
    abortStream({ notice: formatMessage({ id: "chat.responseCanceled" }) });
    clearMessages();
    setEditingMessage(null);
    setPendingRun(null);
    setInput("");
    setRestoreError(null);
    setSearchParams({});
    setSelectedMeetingIds([]);
    setSelectedFileIds([]);
    setSelectedTypeFilters([]);
    setDateFrom("");
    setDateTo("");
    setValidAt("");
    setKnownAt("");
    setContinuationMode("latest");
    setRagMode("auto");
    setUseWebSearch(false);
    refreshMeetings();
  }, [
    abortStream,
    clearMessages,
    refreshMeetings,
    setSearchParams,
    setSelectedMeetingIds,
    setSelectedFileIds,
    setSelectedTypeFilters,
    setDateFrom,
    setDateTo,
    setValidAt,
    setKnownAt,
    setContinuationMode,
    setRagMode,
    setUseWebSearch,
    formatMessage,
  ]);

  return {
    // State
    input,
    setInput,
    restoring,
    restoreError,
    setRestoreError,
    retryRestore,
    pendingRun,
    resumePendingRun: async () => {
      if (!pendingRun || isStreaming) return;
      const run = pendingRun;
      await startStream({
        question: run.question,
        sessionId: resumedSessionId,
        options: { ...chatOptions, resumeRunId: run.id, resumeAfter: run.cursor },
        onEventCursor: (cursor) =>
          setPendingRun((current) => (current ? { ...current, cursor } : current)),
        onCompleted: () => setPendingRun(null),
      });
    },
    hasOlderMessages: olderBeforeId !== undefined,
    loadingOlder,
    loadOlderMessages,
    inputFocused,
    setInputFocused,
    copiedId,
    editingMessage,
    // Chat options
    paramsExpanded,
    setParamsExpanded,
    useWebSearch,
    setUseWebSearch,
    selectedTypeFilters,
    setSelectedTypeFilters,
    dateFrom,
    setDateFrom,
    dateTo,
    setDateTo,
    validAt,
    setValidAt,
    knownAt,
    setKnownAt,
    continuationMode,
    setContinuationMode,
    ragMode,
    setRagMode,
    retrievalProfile,
    setRetrievalProfile,
    memoryMode,
    setMemoryMode,
    activeParamCount,
    // Session selection
    selectedMeetingIds,
    setSelectedMeetingIds,
    selectedFileIds,
    setSelectedFileIds,
    meetings,
    refreshMeetings,
    meetingFilesMap,
    loadingMeetings,
    loadingFiles,
    removeSelectedMeeting,
    removeSelectedFile,
    meetingOptions,
    selectedMeetings,
    fileOptions,
    selectedFiles,
    // Chat stream
    messages,
    isStreaming,
    streamSessionId,
    streamError,
    streamErrorCode,
    streamErrorDetail,
    streamRequestId,
    streamNotice,
    setStreamError,
    setStreamNotice,
    // Refs
    bottomRef,
    scrollContainerRef,
    sendingRef,
    textareaRef,
    handleSendRef,
    // O-FE-3: Back-to-bottom
    showScrollToBottom,
    scrollToBottom,
    // Derived
    chatOptions,
    // Handlers
    handleSend,
    handleRegenerate,
    handleEditUserMessage,
    cancelEditing,
    handleStop,
    handleWithdrawCurrent,
    handleCopy,
    handleCopyRequestId,
    handleCopySourceSnippet,
    handleNewSession,
  };
}
