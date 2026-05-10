import {
  ApiError,
  TIMEOUT_CHAT,
  api,
  buildAuthHeaders,
  getApiBaseUrl,
  parseApiErrorPayload,
} from "./client-core";
import { reportNonCriticalError } from "../utils/monitoring";

/**
 * Combine multiple AbortSignals so that aborting any one of them aborts the
 * result. Falls back to a manual implementation when the native
 * `AbortSignal.any()` is unavailable (Chrome < 116, Firefox < 124, Safari < 17.4).
 */
function anySignal(signals: AbortSignal[]): AbortSignal & { cleanup?: () => void } {
  if (typeof AbortSignal.any === "function") return AbortSignal.any(signals);
  const controller = new AbortController();
  const cleanups: (() => void)[] = [];
  for (const s of signals) {
    if (s.aborted) {
      controller.abort(s.reason);
      cleanups.forEach((c) => c());
      return controller.signal;
    }
    const fn = () => controller.abort(s.reason);
    s.addEventListener("abort", fn, { once: true });
    cleanups.push(() => s.removeEventListener("abort", fn));
  }
  const sig = controller.signal as AbortSignal & { cleanup?: () => void };
  sig.cleanup = () => cleanups.forEach((c) => c());
  return sig;
}

export interface SourceItem {
  meeting_id: number;
  meeting_title: string;
  content: string;
  score: number;
  file_id: number | null;
  file_name: string | null;
  file_type: string | null;
  chunk_index: number | null;
  page_number: number | null;
  slide_number?: number | null;
  timestamp_start: number | null;
  timestamp_end: number | null;
  speaker: string | null;
  source_kind:
    | "timestamp"
    | "slide"
    | "page"
    | "image"
    | "text"
    | "meeting_summary"
    | "file_summary"
    | null;
  content_type?: "text" | "table" | "image_caption" | "image_ocr" | "image_combined" | null;
  image_caption?: string | null;
  image_ocr?: string | null;
  table_markdown?: string | null;
  image_path?: string | null;
  image_thumbnail_path?: string | null;
  page_image_path?: string | null;
  page_image_thumbnail_path?: string | null;
  heading_path?: string[];
  confidence?: number | null;
}

export interface ChatResponse {
  answer: string;
  sources: SourceItem[];
  session_id: string;
  web_results?: { title: string; url: string; snippet: string }[];
  trace?: {
    trace_id: string;
    total_ms: number;
    spans: {
      label: string;
      phase: string;
      duration_ms: number | null;
      status: string;
      metadata?: Record<string, unknown>;
      parent_label?: string;
      skipped?: boolean;
      tokens_in?: number;
      tokens_out?: number;
      docs_retrieved?: number;
    }[];
  };
}

export interface StreamStepEvent {
  type: "step";
  step: string;
  status: "start" | "done";
  duration_ms?: number;
}

export interface StreamTokenEvent {
  type: "token";
  content: string;
}

export interface StreamSourcesEvent {
  type: "sources";
  items: SourceItem[];
}

export interface StreamTraceEvent {
  type: "trace";
  trace: ChatResponse["trace"];
}

export interface StreamWebResultsEvent {
  type: "web_results";
  items: { title: string; url: string; snippet: string }[];
}

export interface StreamErrorEvent {
  type: "error";
  message: string;
  code?: string;
  detail?: string;
  exception_type?: string;
}

export interface StreamDoneEvent {
  type: "done";
  session_id: string;
}

export interface StreamHeartbeatEvent {
  type: "heartbeat";
}

export type StreamEvent =
  | StreamStepEvent
  | StreamTokenEvent
  | StreamSourcesEvent
  | StreamTraceEvent
  | StreamWebResultsEvent
  | StreamErrorEvent
  | StreamDoneEvent
  | StreamHeartbeatEvent;

export interface ChatOptions {
  signal?: AbortSignal;
  fileTypes?: string[];
  dateFrom?: string;
  dateTo?: string;
  topK?: number;
  useWebSearch?: boolean;
  webSearchResults?: number;
  ragMode?: "native" | "hybrid" | "multimodal" | "hybrid_multimodal" | "auto";
}

/** Map camelCase ChatOptions fields back to snake_case for the API request body. */
function mapChatOptionsToBody(options?: ChatOptions): Record<string, unknown> {
  return {
    file_types: options?.fileTypes?.length ? options.fileTypes : undefined,
    date_from: options?.dateFrom || undefined,
    date_to: options?.dateTo || undefined,
    top_k: options?.topK,
    use_web_search: options?.useWebSearch,
    web_search_results: options?.webSearchResults,
    rag_mode: options?.ragMode,
  };
}

export async function sendChat(
  question: string,
  meetingIds?: number[],
  fileIds?: number[],
  sessionId?: string,
  options?: ChatOptions,
) {
  return api.post<ChatResponse>(
    "/chat",
    {
      question,
      meeting_ids: meetingIds?.length ? meetingIds : undefined,
      file_ids: fileIds?.length ? fileIds : undefined,
      session_id: sessionId || undefined,
      ...mapChatOptionsToBody(options),
    },
    { signal: options?.signal, timeout: TIMEOUT_CHAT },
  );
}

export async function chatSearch(
  question: string,
  meetingIds?: number[],
  fileIds?: number[],
  options?: ChatOptions,
) {
  return api.post<{
    results: { meeting_id: number; meeting_title: string; content: string; score: number }[];
  }>(
    "/chat/search",
    {
      question,
      meeting_ids: meetingIds?.length ? meetingIds : undefined,
      file_ids: fileIds?.length ? fileIds : undefined,
      ...mapChatOptionsToBody(options),
    },
    { signal: options?.signal, timeout: TIMEOUT_CHAT },
  );
}

export async function checkHealth() {
  return api.get<{ status: string; checks: Record<string, string> }>("/health");
}

/**
 * Stream chat responses via SSE with typed events.
 *
 * Yields StreamEvent objects as they arrive from the server.
 * The caller is responsible for collecting tokens, sources, etc.
 */
export async function* sendChatStream(
  question: string,
  meetingIds?: number[],
  fileIds?: number[],
  sessionId?: string,
  options?: ChatOptions,
): AsyncGenerator<StreamEvent> {
  const headers = buildAuthHeaders({
    "Content-Type": "application/json",
    Accept: "text/event-stream",
    "Idempotency-Key": crypto.randomUUID(),
  });

  const body: Record<string, unknown> = {
    question,
    meeting_ids: meetingIds?.length ? meetingIds : undefined,
    file_ids: fileIds?.length ? fileIds : undefined,
    session_id: sessionId || undefined,
    ...mapChatOptionsToBody(options),
  };

  // Combine caller-provided signal with a network-layer timeout (matching
  // TIMEOUT_CHAT) so the fetch itself cannot hang indefinitely.  Once the
  // response body starts streaming, the heartbeat mechanism in the consumer
  // takes over stall detection — this timeout only guards the headers phase.
  const timeoutController = new AbortController();
  const timeoutId = setTimeout(() => timeoutController.abort(), TIMEOUT_CHAT);
  const signals: AbortSignal[] = [timeoutController.signal];
  if (options?.signal) signals.push(options.signal);
  const combinedSignal = anySignal(signals);

  const response = await fetch(`${getApiBaseUrl()}/chat/stream`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    signal: combinedSignal,
  });

  if (!response.ok) {
    const raw = await response.text().catch(() => response.statusText);
    let payload: unknown = raw;
    if (typeof raw === "string") {
      try {
        payload = JSON.parse(raw);
      } catch {
        // Keep raw string payload.
      }
    }
    const parsed = parseApiErrorPayload(payload, response.statusText || "Request failed");
    throw new ApiError(response.status, parsed.message, {
      code: parsed.code,
      requestId: parsed.requestId,
      details: parsed.details,
    });
  }

  const reader = response.body?.getReader();
  if (!reader) throw new Error("No response body");

  const decoder = new TextDecoder();
  let buffer = "";
  let sawDone = false;

  function parseSseEvent(rawEvent: string): StreamEvent | null {
    const dataLines: string[] = [];
    for (const line of rawEvent.split("\n")) {
      const trimmed = line.trim();
      if (trimmed.startsWith("data: ")) {
        dataLines.push(trimmed.slice(6));
      }
    }
    if (dataLines.length === 0) return null;
    const jsonStr = dataLines.join("\n");
    return JSON.parse(jsonStr) as StreamEvent;
  }

  function* parseTrailingEvents(raw: string): Generator<StreamEvent> {
    const trimmed = raw.trim();
    if (!trimmed) return;

    const chunks = trimmed.includes("\n\n")
      ? trimmed.split("\n\n")
      : trimmed
          .split("\n")
          .map((line) => line.trim())
          .filter((line) => line.startsWith("data: "));

    for (const chunk of chunks) {
      if (!chunk.trim()) continue;
      try {
        const event = parseSseEvent(chunk);
        if (event) yield event;
      } catch {
        reportNonCriticalError("sse_parse_failure", new Error("Failed to parse SSE event"), {
          jsonPreview: chunk.slice(0, 200),
        });
      }
    }
  }

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        // Flush the TextDecoder to handle trailing partial UTF-8 bytes
        buffer += decoder.decode();
        break;
      }

      buffer += decoder.decode(value, { stream: true });

      // SSE spec: events are delimited by \n\n. Use indexOf for correct
      // boundary handling — split() can produce spurious empty strings
      // and doesn't preserve the delimiter position accurately.
      let idx: number;
      while ((idx = buffer.indexOf("\n\n")) >= 0) {
        const rawEvent = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);

        if (!rawEvent.trim()) continue;

        // Strictly drop any events after the terminal "done" event.
        if (sawDone) continue;

        try {
          const event = parseSseEvent(rawEvent);
          if (event) {
            if (event.type === "done") sawDone = true;
            yield event;
          }
        } catch {
          // Skip malformed frames but keep telemetry so one bad event does not
          // abort or pollute the user-visible stream.
          reportNonCriticalError("sse_parse_failure", new Error("Failed to parse SSE event"), {
            jsonPreview: rawEvent.slice(0, 200),
          });
        }
      }
    }

    // Flush any trailing frames at EOF. The fallback also tolerates
    // line-delimited `data:` events from mocks and misbehaving proxies.
    for (const event of parseTrailingEvents(buffer)) {
      if (sawDone) break;
      if (event.type === "done") sawDone = true;
      yield event;
    }
  } finally {
    clearTimeout(timeoutId);
    combinedSignal.cleanup?.();
    await reader.cancel().catch(() => {});
  }
}
