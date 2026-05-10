import { useState, useRef, useCallback, useEffect } from "react";
import { flushSync } from "react-dom";
import {
  sendChatStream,
  ApiError,
  type ChatOptions,
  type ChatResponse,
  type SourceItem,
  type StreamWebResultsEvent,
} from "../api/client";
import { reportNonCriticalError } from "../utils/monitoring";
import { useStreamTimers } from "./useStreamTimers";

export interface ChatMessage {
  role: "user" | "agent";
  content: string;
  sources?: SourceItem[];
  webResults?: StreamWebResultsEvent["items"];
  trace?: ChatResponse["trace"];
  id?: string;
}

const TOKEN_FLUSH_INTERVAL_MS = 50;

function isAbortError(err: unknown): boolean {
  if (err instanceof Error) {
    return err.name === "CanceledError" || err.name === "AbortError";
  }
  if (typeof err === "object" && err !== null) {
    const name = (err as Record<string, unknown>).name;
    return name === "CanceledError" || name === "AbortError";
  }
  return false;
}

export interface StartStreamParams {
  question: string;
  meetingIds?: number[];
  fileIds?: number[];
  sessionId?: string;
  options?: ChatOptions;
  onDone?: () => void;
}

// M-21: Cap in-memory message history to prevent unbounded growth during
// long sessions. Old messages are collapsed into a placeholder entry.
const MAX_MESSAGES = 200;
// H-FE-1: Fold agent message content when it exceeds 1MB to prevent
// unbounded memory growth from very long streaming responses.
const MAX_CONTENT_LENGTH = 1_000_000;

let _fallbackIdCounter = 0;

function makeMessageId(prefix: "msg" | "reply"): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `${prefix}-${crypto.randomUUID()}`;
  }
  return `${prefix}-${Date.now()}-${++_fallbackIdCounter}-${Math.random().toString(36).slice(2, 8)}`;
}

// Immutably update the last agent message that matches msgId.
// Returns the original array (same reference) when no match is found —
// callers can rely on reference equality to detect a no-op.
function updateLastAgentMsg(
  prev: ChatMessage[],
  msgId: string,
  updater: (msg: ChatMessage) => ChatMessage,
): ChatMessage[] {
  const last = prev[prev.length - 1];
  if (!last || last.role !== "agent" || last.id !== msgId) return prev;
  const next = [...prev];
  next[next.length - 1] = updater(last);
  return next;
}

function collapseOldMessages(msgs: ChatMessage[]): ChatMessage[] {
  if (msgs.length <= MAX_MESSAGES) return msgs;
  const excess = msgs.length - MAX_MESSAGES;
  const placeholder: ChatMessage = {
    role: "agent",
    content: `[Earlier messages (${excess + 2}) have been collapsed to save memory. Scroll up in session history to review.]`,
    id: makeMessageId("reply"),
  };
  // Keep the first 2 messages (opening context) + placeholder + most recent tail
  return [msgs[0], msgs[1], placeholder, ...msgs.slice(-(MAX_MESSAGES - 3))];
}

export function useChatStream() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [sessionId, setSessionId] = useState<string | undefined>();
  const [streamError, setStreamError] = useState<string | null>(null);
  const [streamErrorCode, setStreamErrorCode] = useState<string | null>(null);
  const [streamErrorDetail, setStreamErrorDetail] = useState<string | null>(null);
  const [streamRequestId, setStreamRequestId] = useState<string | null>(null);
  const [streamNotice, setStreamNotice] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);
  const runIdRef = useRef(0);

  // Cleanup on unmount: prevent setMessages on unmounted component
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // Token batching: accumulate tokens and flush on a timer instead of calling
  // setMessages on every single token.  This prevents React re-render pressure
  // from slowing SSE consumption, which would cause StreamBus queue overflow
  // and dropped heartbeats → "Stream stalled" errors.
  const pendingTokensRef = useRef("");
  const flushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const agentMsgIdRef = useRef<string | null>(null);
  const flushRunIdRef = useRef(0);

  const flushPendingTokens = useCallback(() => {
    if (!mountedRef.current) return;
    const batch = pendingTokensRef.current;
    pendingTokensRef.current = "";
    if (flushTimerRef.current !== null) {
      clearTimeout(flushTimerRef.current);
      flushTimerRef.current = null;
    }
    if (!batch || !agentMsgIdRef.current) return;
    if (flushRunIdRef.current !== runIdRef.current) return;
    const msgId = agentMsgIdRef.current;
    setMessages((prev) =>
      updateLastAgentMsg(prev, msgId, (last) => {
        const newContent = last.content + batch;
        return {
          ...last,
          content:
            newContent.length > MAX_CONTENT_LENGTH
              ? newContent.slice(0, MAX_CONTENT_LENGTH) +
                "\n\n[Response truncated — exceeded maximum display length]"
              : newContent,
        };
      }),
    );
  }, []);

  const {
    clearTimers,
    clearNoticeTimer,
    scheduleStallCheck,
    scheduleConnectionCheck,
    scheduleAbsoluteTimeout,
    scheduleNoticeClear,
    touchActivity,
  } = useStreamTimers();

  // M-21: collapse logic is inlined into setMessages calls that append new messages.

  const abortStream = useCallback(
    (opts?: { silent?: boolean; notice?: string }) => {
      const hasActive = abortRef.current !== null;
      abortRef.current?.abort();
      abortRef.current = null;
      runIdRef.current += 1;
      pendingTokensRef.current = "";
      if (flushTimerRef.current !== null) {
        clearTimeout(flushTimerRef.current);
        flushTimerRef.current = null;
      }
      agentMsgIdRef.current = null;
      clearTimers();
      clearNoticeTimer();
      if (!opts?.silent && hasActive) {
        setStreamNotice(opts?.notice ?? "Previous response canceled.");
        scheduleNoticeClear(() => setStreamNotice(null));
      }
    },
    [clearTimers, clearNoticeTimer, scheduleNoticeClear],
  );

  const startStream = useCallback(
    async (params: StartStreamParams) => {
      const {
        question,
        meetingIds,
        fileIds,
        sessionId: initialSessionId,
        options,
        onDone,
      } = params;

      // Abort any in-flight stream first so the previous empty agent placeholder
      // is cleaned up before we insert the new user message.
      abortStream({ notice: "Previous response canceled." });

      // Capture the new run ID immediately after abort so the previous stream's
      // finally block can flush its pending tokens before we overwrite this ref.
      flushRunIdRef.current = runIdRef.current;

      const userMsgId = makeMessageId("msg");
      const agentMsgId = makeMessageId("reply");
      agentMsgIdRef.current = agentMsgId;

      // A2: flushSync commits the user message synchronously so it paints in the
      // very next frame — before any async work (fetch, state resets) begins.
      // A3: collapse is inlined here instead of a useEffect that fired on every add.
      flushSync(() => {
        setMessages((prev) => {
          const next = [...prev, { role: "user" as const, content: question, id: userMsgId }];
          return next.length > MAX_MESSAGES ? collapseOldMessages(next) : next;
        });
      });

      setIsStreaming(true);
      setStreamError(null);
      setStreamErrorCode(null);
      setStreamErrorDetail(null);
      setStreamRequestId(null);
      clearNoticeTimer();
      setStreamNotice(null);

      const controller = new AbortController();
      abortRef.current = controller;

      setMessages((prev) => {
        const next = [...prev, { role: "agent" as const, content: "", id: agentMsgId }];
        return next.length > MAX_MESSAGES ? collapseOldMessages(next) : next;
      });

      touchActivity();

      const handleAbsoluteTimeout = () => {
        reportNonCriticalError(
          "chat-stream-absolute-timeout",
          new Error("Stream exceeded absolute time limit"),
          {
            sessionId: initialSessionId,
          },
        );
        controller.abort();
        setStreamError("Response timed out — the server took too long. Please try again.");
        setStreamNotice("Stream timed out. Click Retry to resend your last question.");
        setMessages((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          if (last?.role === "agent" && !last.content) {
            next.pop();
          }
          return next;
        });
      };

      const handleStall = () => {
        reportNonCriticalError("chat-stream-stall", new Error("Stream stalled"), {
          sessionId: initialSessionId,
          questionLength: question.length,
        });
        controller.abort();
        setStreamError("Stream stalled — no response from the server. Please try again.");
        setStreamNotice("Stream stalled. Click Retry to resend your last question.");
        setMessages((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          if (last?.role === "agent" && !last.content) {
            next.pop();
          }
          return next;
        });
      };

      const handleDeadConnection = () => {
        reportNonCriticalError("chat-stream-dead-connection", new Error("Dead connection"), {
          sessionId: initialSessionId,
        });
        controller.abort();
        setStreamError("Connection lost — no keep-alive from the server. Please try again.");
        setStreamNotice("Connection lost. Click Retry to resend your last question.");
        setMessages((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          if (last?.role === "agent" && !last.content) {
            next.pop();
          }
          return next;
        });
      };

      scheduleStallCheck(handleStall);
      scheduleConnectionCheck(handleDeadConnection);
      scheduleAbsoluteTimeout(handleAbsoluteTimeout);

      try {
        for await (const event of sendChatStream(question, meetingIds, fileIds, initialSessionId, {
          ...options,
          signal: controller.signal,
        })) {
          touchActivity();

          // Heartbeats prove the server is alive AND actively working on the
          // request (e.g. during query rewrite, retrieval, rerank, or LLM TTFT
          // before the first token arrives). Reset both timers so a long
          // pre-token phase isn't misreported as a stall.
          scheduleStallCheck(handleStall);
          scheduleConnectionCheck(handleDeadConnection);

          // N-H2: Guard all subsequent setMessages calls — once the component
          // unmounts, writing state would be a no-op but also triggers the
          // React "setState on unmounted component" warning.
          if (!mountedRef.current) return;

          if (event.type === "heartbeat") {
            continue;
          }

          if (event.type === "token") {
            pendingTokensRef.current += event.content;
            if (flushTimerRef.current === null) {
              flushTimerRef.current = setTimeout(flushPendingTokens, TOKEN_FLUSH_INTERVAL_MS);
            }
          } else if (event.type === "sources") {
            const items = event.items;
            setMessages((prev) =>
              updateLastAgentMsg(prev, agentMsgId, (last) => ({ ...last, sources: items })),
            );
          } else if (event.type === "web_results") {
            const items = event.items;
            setMessages((prev) =>
              updateLastAgentMsg(prev, agentMsgId, (last) => ({ ...last, webResults: items })),
            );
          } else if (event.type === "trace") {
            const trace = event.trace;
            setMessages((prev) =>
              updateLastAgentMsg(prev, agentMsgId, (last) => ({ ...last, trace })),
            );
          } else if (event.type === "done") {
            flushPendingTokens();
            setIsStreaming(false);
            onDone?.();
            setSessionId(event.session_id);
          } else if (event.type === "error") {
            flushPendingTokens();
            setIsStreaming(false);
            onDone?.();
            setStreamError(event.message);
            setStreamErrorCode(event.code ?? null);
            setStreamErrorDetail(event.detail ?? null);
            const errMsg = event.message;
            setMessages((prev) =>
              updateLastAgentMsg(prev, agentMsgId, (last) => ({ ...last, content: errMsg })),
            );
          }
        }
      } catch (err: unknown) {
        if (isAbortError(err)) {
          setMessages((prev) => prev.filter((m) => m.id !== agentMsgId || m.content !== ""));
        } else {
          const msg = err instanceof ApiError ? err.message : "Connection lost. Please try again.";
          setStreamError(msg);
          setStreamErrorCode(err instanceof ApiError ? (err.code ?? null) : null);
          setStreamErrorDetail(null);
          setStreamRequestId(err instanceof ApiError ? (err.requestId ?? null) : null);
          setMessages((prev) =>
            updateLastAgentMsg(prev, agentMsgId, (last) => ({ ...last, content: msg })),
          );
        }
      } finally {
        // Only flush pending tokens if this stream run is still active.
        // abortStream() increments runIdRef and clears pending tokens/buffers,
        // so a stale finally from an aborted run must not re-flush.
        if (flushRunIdRef.current === runIdRef.current) {
          flushPendingTokens();
        }
        setIsStreaming(false);
        abortRef.current = null;
        agentMsgIdRef.current = null;
        clearTimers();
      }
    },
    [
      abortStream,
      clearTimers,
      clearNoticeTimer,
      flushPendingTokens,
      scheduleStallCheck,
      scheduleConnectionCheck,
      scheduleAbsoluteTimeout,
      touchActivity,
    ],
  );

  const clearMessages = useCallback(() => {
    setMessages([]);
    setSessionId(undefined);
    setStreamError(null);
    setStreamErrorCode(null);
    setStreamErrorDetail(null);
    setStreamRequestId(null);
    clearNoticeTimer();
    setStreamNotice(null);
  }, [clearNoticeTimer]);

  return {
    messages,
    isStreaming,
    sessionId,
    streamError,
    streamErrorCode,
    streamErrorDetail,
    streamRequestId,
    streamNotice,
    setStreamError,
    setStreamNotice,
    startStream,
    abortStream,
    setMessages,
    clearMessages,
  };
}
