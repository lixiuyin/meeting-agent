import { useState, useEffect, useRef, useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import { message } from "antd";
import type { TextAreaRef } from "antd/es/input/TextArea";
import { getSessionMessages, ApiError, formatApiErrorMessage } from "../api/client";
import { useChatStream } from "./useChatStream";
import { useChatOptions } from "./useChatOptions";
import { useSessionSelection } from "./useSessionSelection";

export type { ChatOptions } from "./useChatOptions";
export type { ChatMessage } from "./useChatStream";

const SESSION_ID_RE = /^[A-Za-z0-9_-]{8,64}$/;

export function useSessionManager() {
  const [searchParams, setSearchParams] = useSearchParams();
  const rawSessionId = searchParams.get("sessionId") ?? undefined;
  const resumedSessionId =
    rawSessionId && SESSION_ID_RE.test(rawSessionId) ? rawSessionId : undefined;

  const [input, setInput] = useState("");
  const [restoring, setRestoring] = useState(!!resumedSessionId);
  const [restoreError, setRestoreError] = useState<string | null>(null);
  const [inputFocused, setInputFocused] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const {
    paramsExpanded,
    setParamsExpanded,
    topK,
    setTopK,
    useWebSearch,
    setUseWebSearch,
    selectedTypeFilters,
    setSelectedTypeFilters,
    dateFrom,
    setDateFrom,
    dateTo,
    setDateTo,
    ragMode,
    setRagMode,
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
    startStream,
    abortStream,
    clearMessages,
    setMessages,
  } = useChatStream();
  const streamSessionIdRef = useRef<string | undefined>(streamSessionId);
  useEffect(() => {
    streamSessionIdRef.current = streamSessionId;
  }, [streamSessionId]);

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

  // Session restoration from URL
  useEffect(() => {
    if (!resumedSessionId) return;
    const controller = new AbortController();
    const timeoutMs = 10_000;
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
    getSessionMessages(resumedSessionId, { signal: controller.signal })
      .then((res: { data: { messages: Array<{ role: string; content: string }> } }) => {
        if (controller.signal.aborted) return;
        if (!res.data?.messages) return;
        const restored = res.data.messages.map(
          (m: { role: string; content: string }, idx: number) => ({
            role: (m.role === "human" ? "user" : "agent") as "user" | "agent",
            content: m.content,
            id: `${resumedSessionId}-${idx}`,
          }),
        );
        setMessages(restored);
        setRestoreError(null);
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        const msg =
          err instanceof ApiError && err.status === 404
            ? "Session not found. It may have been deleted."
            : formatApiErrorMessage(err, "Failed to restore session");
        setRestoreError(msg);
        setSearchParams({});
      })
      .finally(() => {
        if (!controller.signal.aborted) setRestoring(false);
      });
    return () => {
      clearTimeout(timeoutId);
      controller.abort();
    };
  }, [resumedSessionId, setMessages, setSearchParams]);

  // Auto-scroll behavior with back-to-bottom button (O-FE-3)
  useEffect(() => {
    const container = scrollContainerRef.current;
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

  const handleSend = useCallback(
    async (questionOverride?: string) => {
      const question = questionOverride?.trim() || input.trim();
      if (!question || sendingRef.current) return;
      if (loadingFiles) {
        message.info("File list is updating. Please retry in a moment.");
        return;
      }
      // Monotonically incrementing send ID prevents overlapping sends from
      // the same microtask — the ref is set synchronously before any await.
      const sendId = ++lastSendIdRef.current;
      sendingRef.current = true;
      autoScrollRef.current = true;

      if (!questionOverride) {
        setInput("");
      }

      try {
        setStreamError(null);
        await startStream({
          question,
          meetingIds: selectedMeetingIds.length > 0 ? selectedMeetingIds : undefined,
          fileIds: resolveEffectiveFileIds(),
          sessionId: streamSessionIdRef.current,
          options: chatOptions,
          onDone: () => {
            sendingRef.current = false;
          },
        });
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
    ],
  );

  // Keep ref to latest handleSend for keyboard shortcuts
  const handleSendRef = useRef(handleSend);
  useEffect(() => {
    handleSendRef.current = handleSend;
  }, [handleSend]);

  const handleRegenerate = useCallback(async () => {
    if (loadingFiles) {
      message.info("File list is updating. Please retry in a moment.");
      return;
    }
    const lastUserIndex = [...messages].reverse().findIndex((m) => m.role === "user");
    if (lastUserIndex === -1) return;

    const actualIndex = messages.length - 1 - lastUserIndex;
    const lastUserMessage = messages[actualIndex];

    if (messages[messages.length - 1]?.role === "agent") {
      setMessages((prev) => prev.slice(0, -1));
    }

    const regenId = ++lastSendIdRef.current;
    sendingRef.current = true;
    autoScrollRef.current = true;
    setStreamError(null);
    try {
      await startStream({
        question: lastUserMessage.content,
        meetingIds: selectedMeetingIds.length > 0 ? selectedMeetingIds : undefined,
        fileIds: resolveEffectiveFileIds(),
        sessionId: streamSessionIdRef.current,
        options: chatOptions,
        onDone: () => {
          sendingRef.current = false;
        },
      });
    } finally {
      if (lastSendIdRef.current === regenId) {
        sendingRef.current = false;
      }
    }
  }, [
    loadingFiles,
    messages,
    selectedMeetingIds,
    resolveEffectiveFileIds,
    chatOptions,
    setMessages,
    setStreamError,
    startStream,
  ]);

  const handleCopy = useCallback(async (content: string, id: string) => {
    try {
      await navigator.clipboard.writeText(content);
      setCopiedId(id);
      message.success({ content: "Copied!", duration: 2 });
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
      copyTimerRef.current = setTimeout(() => setCopiedId(null), 2000);
    } catch {
      message.error("Failed to copy");
    }
  }, []);

  const handleCopyRequestId = useCallback(async (requestId: string) => {
    try {
      await navigator.clipboard.writeText(requestId);
      message.success({ content: "Request ID copied", duration: 2 });
    } catch {
      message.error("Failed to copy Request ID");
    }
  }, []);

  const handleCopySourceSnippet = useCallback(async (content: string) => {
    try {
      await navigator.clipboard.writeText(content);
      message.success({ content: "Source snippet copied", duration: 2 });
    } catch {
      message.error("Failed to copy source snippet");
    }
  }, []);

  const handleNewSession = useCallback(() => {
    abortStream({ notice: "Current response canceled." });
    clearMessages();
    setSelectedMeetingIds([]);
    setSelectedFileIds([]);
    refreshMeetings();
  }, [abortStream, clearMessages, refreshMeetings, setSelectedMeetingIds, setSelectedFileIds]);

  return {
    // State
    input,
    setInput,
    restoring,
    restoreError,
    setRestoreError,
    inputFocused,
    setInputFocused,
    copiedId,
    // Chat options
    paramsExpanded,
    setParamsExpanded,
    topK,
    setTopK,
    useWebSearch,
    setUseWebSearch,
    selectedTypeFilters,
    setSelectedTypeFilters,
    dateFrom,
    setDateFrom,
    dateTo,
    setDateTo,
    ragMode,
    setRagMode,
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
    handleCopy,
    handleCopyRequestId,
    handleCopySourceSnippet,
    handleNewSession,
  };
}
