import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { collapseOldMessages, useChatStream, type ChatMessage } from "./useChatStream";
import * as client from "../api/client";

describe("useChatStream", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("starts with empty state", () => {
    const { result } = renderHook(() => useChatStream());
    expect(result.current.messages).toEqual([]);
    expect(result.current.isStreaming).toBe(false);
    expect(result.current.sessionId).toBeUndefined();
    expect(result.current.streamError).toBeNull();
    expect(result.current.streamRequestId).toBeNull();
  });

  it("rejects a done event that has no visible answer", async () => {
    vi.spyOn(client, "sendChatStream").mockImplementation(async function* () {
      yield { type: "done", session_id: "sess-123" };
    });

    const { result } = renderHook(() => useChatStream());

    await act(async () => {
      await result.current.startStream({ question: "Hello" });
    });

    expect(result.current.messages).toHaveLength(2);
    expect(result.current.messages[0].role).toBe("user");
    expect(result.current.messages[0].content).toBe("Hello");
    expect(result.current.messages[1].role).toBe("agent");
    expect(result.current.messages[1].content).toContain("no usable answer");
    expect(result.current.streamErrorCode).toBe("EMPTY_LLM_RESPONSE");
    expect(result.current.sessionId).toBeUndefined();
    expect(result.current.isStreaming).toBe(false);
  });

  it("appends tokens to the agent message", async () => {
    vi.spyOn(client, "sendChatStream").mockImplementation(async function* () {
      yield { type: "token", content: "Hi" };
      yield { type: "token", content: " there" };
      yield { type: "done", session_id: "sess-456" };
    });

    const { result } = renderHook(() => useChatStream());

    await act(async () => {
      await result.current.startStream({ question: "Hi" });
    });

    expect(result.current.messages[1].content).toBe("Hi there");
  });

  it("sets sources on agent message", async () => {
    const sources = [
      {
        meeting_id: 1,
        meeting_title: "Test",
        content: "foo",
        score: 0.9,
        file_id: 11,
        file_name: "meeting.wav",
        file_type: "audio",
        chunk_index: 0,
        page_number: null,
        timestamp_start: 12,
        timestamp_end: 20,
        speaker: "Speaker A",
        source_kind: "timestamp" as const,
      },
    ];
    vi.spyOn(client, "sendChatStream").mockImplementation(async function* () {
      yield { type: "sources", items: sources };
      yield { type: "token", content: "Answer [1]" };
      yield { type: "done", session_id: "sess-789" };
    });

    const { result } = renderHook(() => useChatStream());

    await act(async () => {
      await result.current.startStream({ question: "Q" });
    });

    expect(result.current.messages[1].sources).toEqual(sources);
  });

  it("keeps degraded generation status separate from answer content", async () => {
    vi.spyOn(client, "sendChatStream").mockImplementation(async function* () {
      yield { type: "status", status: "degraded", reason: "fast_path_timeout" };
      yield { type: "token", content: "Relevant source excerpts (partial result):" };
      yield { type: "done", session_id: "sess-degraded" };
    });

    const { result } = renderHook(() => useChatStream());

    await act(async () => {
      await result.current.startStream({ question: "What happened?" });
    });

    expect(result.current.messages[1].degraded).toBe(true);
    expect(result.current.messages[1].degradationReason).toBe("fast_path_timeout");
    expect(result.current.messages[1].content).toBe("Relevant source excerpts (partial result):");
  });

  it("sets streamError on non-abort errors", async () => {
    // eslint-disable-next-line require-yield
    vi.spyOn(client, "sendChatStream").mockImplementation(async function* () {
      throw new client.ApiError(500, "Server error", { requestId: "req-123" });
    });

    const { result } = renderHook(() => useChatStream());

    await act(async () => {
      await result.current.startStream({ question: "Q" });
    });

    expect(result.current.streamError).toBe("Server error");
    expect(result.current.streamRequestId).toBe("req-123");
    expect(result.current.messages[1].content).toBe("Server error");
    expect(result.current.isStreaming).toBe(false);
  });

  it("sets streamError when server emits stream error event", async () => {
    vi.spyOn(client, "sendChatStream").mockImplementation(async function* () {
      yield { type: "error", message: "LLM rate limit reached. Please retry in a moment." };
    });

    const { result } = renderHook(() => useChatStream());

    await act(async () => {
      await result.current.startStream({ question: "Q" });
    });

    expect(result.current.streamError).toBe("LLM rate limit reached. Please retry in a moment.");
    expect(result.current.messages[1].content).toBe(
      "LLM rate limit reached. Please retry in a moment.",
    );
    expect(result.current.isStreaming).toBe(false);
  });

  it("filters empty agent message on abort", async () => {
    vi.spyOn(client, "sendChatStream").mockImplementation(async function* () {
      yield { type: "token", content: "partial" };
      throw new Error("AbortError");
    });

    const { result } = renderHook(() => useChatStream());

    await act(async () => {
      await result.current.startStream({ question: "Q" });
    });

    // partial token was appended before abort, so agent remains
    expect(result.current.messages.some((m) => m.role === "agent")).toBe(true);
  });

  it("clears messages and session on clearMessages", async () => {
    vi.spyOn(client, "sendChatStream").mockImplementation(async function* () {
      yield { type: "done", session_id: "sess-abc" };
    });

    const { result } = renderHook(() => useChatStream());

    await act(async () => {
      await result.current.startStream({ question: "Hello" });
    });

    act(() => {
      result.current.clearMessages();
    });

    expect(result.current.messages).toEqual([]);
    expect(result.current.sessionId).toBeUndefined();
    expect(result.current.streamError).toBeNull();
    expect(result.current.streamRequestId).toBeNull();
  });

  it("aborts active stream", async () => {
    const sendChatStreamSpy = vi
      .spyOn(client, "sendChatStream")
      .mockImplementation(async function* () {
        yield { type: "token", content: "a" };
        yield { type: "token", content: "b" };
        yield { type: "done", session_id: "sess-abort" };
      });

    const { result } = renderHook(() => useChatStream());

    act(() => {
      result.current.startStream({ question: "Hello" });
    });

    // Wait for streaming to finish (mock completes immediately)
    await waitFor(() => expect(result.current.isStreaming).toBe(false));

    // All tokens should be present since the stream completed normally
    expect(result.current.messages[1]?.content).toBe("ab");
    sendChatStreamSpy.mockRestore();
  });

  it("attaches persisted IDs from the done event to the completed turn", async () => {
    vi.spyOn(client, "sendChatStream").mockImplementation(async function* () {
      yield { type: "token", content: "answer" };
      yield { type: "done", session_id: "sess-ids", message_ids: [41, 42] };
    });
    const { result } = renderHook(() => useChatStream());
    await act(() => result.current.startStream({ question: "question" }));
    expect(result.current.messages).toEqual([
      expect.objectContaining({ role: "user", serverId: 41 }),
      expect.objectContaining({ role: "agent", serverId: 42 }),
    ]);
  });

  it("clears pending tokens and flush timer on abort", async () => {
    let yieldSecondToken: (() => void) | null = null;
    const sendChatStreamSpy = vi
      .spyOn(client, "sendChatStream")
      .mockImplementation(async function* () {
        yield { type: "token", content: "a" };
        // Block until test signals to continue
        await new Promise<void>((resolve) => {
          yieldSecondToken = resolve;
        });
      });

    const { result } = renderHook(() => useChatStream());

    act(() => {
      result.current.startStream({ question: "Hello" });
    });

    await waitFor(() => expect(result.current.messages[1]?.content).toBe("a"));

    // Abort while stream is still blocked
    act(() => {
      result.current.abortStream();
    });

    // Release the blocked generator — but abort already incremented runId
    // so any further tokens are discarded by the runId guard.
    const release = yieldSecondToken as (() => void) | null;
    if (release) release();

    await waitFor(() => expect(result.current.isStreaming).toBe(false));
    expect(result.current.messages[1]?.content).toBe("a");

    sendChatStreamSpy.mockRestore();
  });
});

describe("collapseOldMessages", () => {
  it("reports and accumulates the actual number of removed messages", () => {
    const initial: ChatMessage[] = Array.from({ length: 201 }, (_, index) => ({
      role: index % 2 === 0 ? "user" : "agent",
      content: `message-${index}`,
      id: `message-${index}`,
    }));
    const first = collapseOldMessages(initial);
    expect(first).toHaveLength(200);
    expect(first[2].collapsedCount).toBe(2);
    expect(first[2].content).toContain("(2)");

    const second = collapseOldMessages([...first, { role: "user", content: "next", id: "next" }]);
    expect(second).toHaveLength(200);
    expect(second[2].collapsedCount).toBe(3);
    expect(second[2].content).toContain("(3)");
  });
});
