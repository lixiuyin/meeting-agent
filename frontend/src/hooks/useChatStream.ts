import { useState, useRef, useCallback, useEffect, useReducer } from "react";
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
  degraded?: boolean;
  degradationReason?: string | null;
  id?: string;
  serverId?: number;
  collapsedCount?: number;
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
  onCompleted?: () => void;
  onRunIdentified?: (runId: string) => void;
  onEventCursor?: (cursor: number) => void;
  clientTurnId?: string;
}

type StreamPhase = "idle" | "streaming" | "completed" | "error" | "cancelled";

interface StreamState {
  phase: StreamPhase;
  error: string | null;
  errorCode: string | null;
  errorDetail: string | null;
  requestId: string | null;
  notice: string | null;
}

type StreamAction =
  | { type: "start" }
  | { type: "complete" }
  | { type: "finish" }
  | {
      type: "fail";
      error: string;
      errorCode?: string | null;
      errorDetail?: string | null;
      requestId?: string | null;
      notice?: string | null;
    }
  | { type: "cancel"; notice?: string | null }
  | { type: "setError"; error: string | null }
  | { type: "setNotice"; notice: string | null }
  | { type: "clear" };

const initialStreamState: StreamState = {
  phase: "idle",
  error: null,
  errorCode: null,
  errorDetail: null,
  requestId: null,
  notice: null,
};

function streamReducer(state: StreamState, action: StreamAction): StreamState {
  switch (action.type) {
    case "start":
      return { ...initialStreamState, phase: "streaming" };
    case "complete":
      return { ...state, phase: "completed" };
    case "finish":
      return state.phase === "streaming" ? { ...state, phase: "completed" } : state;
    case "fail":
      return {
        phase: "error",
        error: action.error,
        errorCode: action.errorCode ?? null,
        errorDetail: action.errorDetail ?? null,
        requestId: action.requestId ?? null,
        notice: action.notice ?? state.notice,
      };
    case "cancel":
      return { ...state, phase: "cancelled", notice: action.notice ?? null };
    case "setError":
      return { ...state, error: action.error };
    case "setNotice":
      return { ...state, notice: action.notice };
    case "clear":
      return initialStreamState;
  }
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

export function collapseOldMessages(msgs: ChatMessage[]): ChatMessage[] {
  if (msgs.length <= MAX_MESSAGES) return msgs;
  const previousCollapsed = msgs.reduce(
    (total, message) => total + (message.collapsedCount ?? 0),
    0,
  );
  const originals = msgs.filter((message) => message.collapsedCount === undefined);
  const recentCount = MAX_MESSAGES - 3;
  const head = originals.slice(0, 2);
  const tail = originals.slice(-recentCount);
  const newlyCollapsed = Math.max(0, originals.length - head.length - tail.length);
  const collapsedCount = previousCollapsed + newlyCollapsed;
  const placeholder: ChatMessage = {
    role: "agent",
    content: `[Earlier messages (${collapsedCount}) have been collapsed to save memory. Scroll up in session history to review.]`,
    id: makeMessageId("reply"),
    collapsedCount,
  };
  // Keep the first two original messages, one cumulative placeholder, and
  // the recent tail.  Existing placeholders never reset the count.
  return [...head, placeholder, ...tail];
}

export function useChatStream() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | undefined>();
  const [streamState, dispatchStream] = useReducer(streamReducer, initialStreamState);
  const isStreaming = streamState.phase === "streaming";
  const setStreamError = useCallback(
    (error: string | null) => dispatchStream({ type: "setError", error }),
    [],
  );
  const setStreamNotice = useCallback(
    (notice: string | null) => dispatchStream({ type: "setNotice", notice }),
    [],
  );
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

  const flushPendingTokens = useCallback(() => {
    if (!mountedRef.current) return;
    const batch = pendingTokensRef.current;
    pendingTokensRef.current = "";
    if (flushTimerRef.current !== null) {
      clearTimeout(flushTimerRef.current);
      flushTimerRef.current = null;
    }
    if (!batch || !agentMsgIdRef.current) return;
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
    (opts?: { silent?: boolean; notice?: string; cancelRemotely?: boolean }) => {
      const hasActive = abortRef.current !== null;
      abortRef.current?.abort(
        opts?.silent
          ? new DOMException("Stream detached", "AbortError")
          : opts?.cancelRemotely === false
            ? new DOMException("Cancellation delegated", "AbortError")
            : undefined,
      );
      abortRef.current = null;
      runIdRef.current += 1;
      pendingTokensRef.current = "";
      if (flushTimerRef.current !== null) {
        clearTimeout(flushTimerRef.current);
        flushTimerRef.current = null;
      }
      agentMsgIdRef.current = null;
      dispatchStream({
        type: "cancel",
        notice: !opts?.silent && hasActive ? (opts?.notice ?? "Previous response canceled.") : null,
      });
      clearTimers();
      clearNoticeTimer();
      if (!opts?.silent && hasActive) {
        scheduleNoticeClear(() => setStreamNotice(null));
      }
    },
    [clearTimers, clearNoticeTimer, scheduleNoticeClear, setStreamNotice],
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
        onCompleted,
        onRunIdentified,
        onEventCursor,
        clientTurnId,
      } = params;

      // Abort any in-flight stream first so the previous empty agent placeholder
      // is cleaned up before we insert the new user message.
      abortStream({ notice: "Previous response canceled." });

      // Immutable identity for this invocation. A stale request must never
      // clear state owned by a newer stream.
      const runId = runIdRef.current;
      let receivedVisibleContent = "";

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

      dispatchStream({ type: "start" });
      clearNoticeTimer();

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
        dispatchStream({
          type: "fail",
          error: "Response timed out — the server took too long. Please try again.",
          notice: "Stream timed out. Click Retry to resend your last question.",
        });
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
        dispatchStream({
          type: "fail",
          error: "Stream stalled — no response from the server. Please try again.",
          notice: "Stream stalled. Click Retry to resend your last question.",
        });
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
        dispatchStream({
          type: "fail",
          error: "Connection lost — no keep-alive from the server. Please try again.",
          notice: "Connection lost. Click Retry to resend your last question.",
        });
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
          idempotencyKey: clientTurnId,
          onRunIdentified,
          onEventCursor,
          signal: controller.signal,
        })) {
          if (runId !== runIdRef.current) return;
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
            if (event.session_id) setSessionId(event.session_id);
            continue;
          }

          if (event.type === "token") {
            receivedVisibleContent += event.content;
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
          } else if (event.type === "status" && event.status === "degraded") {
            setMessages((prev) =>
              updateLastAgentMsg(prev, agentMsgId, (last) => ({
                ...last,
                degraded: true,
                degradationReason: event.reason ?? "fast_path_timeout",
              })),
            );
          } else if (event.type === "done") {
            flushPendingTokens();
            if (event.message_ids?.length === 2) {
              const [humanId, aiId] = event.message_ids;
              setMessages((previous) =>
                previous.map((message) => {
                  if (message.id === userMsgId) return { ...message, serverId: humanId };
                  if (message.id === agentMsgId) return { ...message, serverId: aiId };
                  return message;
                }),
              );
            }
            dispatchStream({ type: "complete" });
            onDone?.();
            if (!receivedVisibleContent.trim()) {
              const emptyMessage = "The model returned no usable answer. Please retry.";
              dispatchStream({
                type: "fail",
                error: emptyMessage,
                errorCode: "EMPTY_LLM_RESPONSE",
              });
              setMessages((prev) =>
                updateLastAgentMsg(prev, agentMsgId, (last) => ({
                  ...last,
                  content: emptyMessage,
                  sources: undefined,
                  webResults: undefined,
                })),
              );
            } else {
              setSessionId(event.session_id);
              onCompleted?.();
            }
          } else if (event.type === "error") {
            flushPendingTokens();
            onDone?.();
            dispatchStream({
              type: "fail",
              error: event.message,
              errorCode: event.code ?? null,
              errorDetail: event.detail ?? null,
            });
            const errMsg = event.message;
            setMessages((prev) =>
              updateLastAgentMsg(prev, agentMsgId, (last) => ({
                ...last,
                content: last.content.trim() ? `${last.content}\n\n${errMsg}` : errMsg,
                sources: undefined,
                webResults: undefined,
              })),
            );
          }
        }
      } catch (err: unknown) {
        if (runId !== runIdRef.current) return;
        if (isAbortError(err)) {
          setMessages((prev) => prev.filter((m) => m.id !== agentMsgId || m.content !== ""));
        } else {
          const msg = err instanceof ApiError ? err.message : "Connection lost. Please try again.";
          dispatchStream({
            type: "fail",
            error: msg,
            errorCode: err instanceof ApiError ? (err.code ?? null) : null,
            requestId: err instanceof ApiError ? (err.requestId ?? null) : null,
          });
          setMessages((prev) =>
            updateLastAgentMsg(prev, agentMsgId, (last) => ({ ...last, content: msg })),
          );
        }
      } finally {
        if (runId === runIdRef.current) {
          flushPendingTokens();
          dispatchStream({ type: "finish" });
          abortRef.current = null;
          agentMsgIdRef.current = null;
          clearTimers();
        }
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
    dispatchStream({ type: "clear" });
    clearNoticeTimer();
  }, [clearNoticeTimer]);

  return {
    messages,
    isStreaming,
    sessionId,
    streamError: streamState.error,
    streamErrorCode: streamState.errorCode,
    streamErrorDetail: streamState.errorDetail,
    streamRequestId: streamState.requestId,
    streamNotice: streamState.notice,
    setStreamError,
    setStreamNotice,
    setSessionId,
    startStream,
    abortStream,
    setMessages,
    clearMessages,
  };
}
