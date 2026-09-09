import { describe, expect, it, vi } from "vitest";

import { ApiError, sendChatStream } from "./client";

function createSseBody(lines: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const line of lines) {
        controller.enqueue(encoder.encode(line));
      }
      controller.close();
    },
  });
}

describe("sendChatStream", () => {
  it("uses a caller-owned logical turn key and reports durable cursors", async () => {
    const onCursor = vi.fn();
    const onRun = vi.fn();
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(createSseBody(['id: 7\ndata: {"type":"done","session_id":"s"}\n\n']), {
        headers: { "X-Run-ID": "run-7" },
      }),
    );

    for await (const event of sendChatStream("hello", undefined, undefined, undefined, {
      idempotencyKey: "turn-stable",
      onEventCursor: onCursor,
      onRunIdentified: onRun,
    })) {
      void event;
    }

    const request = fetchMock.mock.calls[0][1];
    expect(new Headers(request?.headers).get("Idempotency-Key")).toBe("turn-stable");
    expect(onRun).toHaveBeenCalledWith("run-7");
    expect(onCursor).toHaveBeenCalledWith(7);
  });

  it("resumes after the last durable event cursor", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        new Response(createSseBody(['data: {"type":"done","session_id":"s"}\n\n'])),
      );
    fetchMock.mockClear();

    for await (const event of sendChatStream("hello", undefined, undefined, undefined, {
      resumeRunId: "run/with spaces",
      resumeAfter: 12,
    })) {
      void event;
    }

    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/chat/runs/run%2Fwith%20spaces/events?after=12",
    );
  });

  it("sends bitemporal and saved-session continuation controls", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        new Response(createSseBody(['data: {"type":"done","session_id":"s"}\n\n'])),
      );
    fetchMock.mockClear();

    for await (const event of sendChatStream("historical status", undefined, undefined, "s", {
      validAt: "2025-03-01T00:00",
      knownAt: "2025-04-01T00:00",
      continuationMode: "saved_snapshot",
    })) {
      void event;
    }

    const request = fetchMock.mock.calls[0][1];
    expect(JSON.parse(String(request?.body))).toMatchObject({
      valid_at: new Date("2025-03-01T00:00").toISOString(),
      known_at: new Date("2025-04-01T00:00").toISOString(),
      continuation_mode: "saved_snapshot",
      session_id: "s",
    });
  });

  it.each([
    [undefined, true],
    [new DOMException("Stream detached", "AbortError"), false],
    [new DOMException("Cancellation delegated", "AbortError"), false],
  ])("distinguishes local abort modes: %s", async (reason, expectsRemoteCancel) => {
    const signal = new AbortController();
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(createSseBody(['data: {"type":"heartbeat"}\n\n']), {
        headers: { "X-Run-ID": "test-run" },
      }),
    );
    fetchMock.mockClear();
    const stream = sendChatStream("hello", undefined, undefined, undefined, {
      signal: signal.signal,
    });
    await stream.next();
    signal.abort(reason);
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/test-run/cancel"))).toBe(
      expectsRemoteCancel,
    );
    await stream.return(undefined);
  });
  it("parses SSE events and skips malformed JSON", async () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      body: createSseBody([
        'data: {"type":"token","content":"Hi"}\n',
        "data: not-a-json\n",
        'data: {"type":"done","session_id":"sess-42"}\n',
      ]),
    } as Response);

    const events: Array<{ type: string; content?: string; session_id?: string }> = [];
    for await (const event of sendChatStream("hello")) {
      events.push(event as { type: string; content?: string; session_id?: string });
    }

    expect(fetchMock).toHaveBeenCalled();
    expect(events).toEqual([
      { type: "token", content: "Hi" },
      { type: "done", session_id: "sess-42" },
    ]);
    warnSpy.mockRestore();
  });

  it("throws ApiError when upstream responds non-2xx", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: false,
      status: 503,
      statusText: "Service Unavailable",
      text: async () => "temporary failure",
    } as Response);

    await expect(async () => {
      for await (const event of sendChatStream("hello")) {
        void event;
      }
    }).rejects.toMatchObject({ name: "ApiError", message: "temporary failure" });
  });

  it("prefers backend error envelope message for non-2xx", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: false,
      status: 400,
      statusText: "Bad Request",
      text: async () =>
        JSON.stringify({
          code: "HTTP_400",
          message: "Meeting not found",
          request_id: "req-123",
          details: { meeting_id: 999 },
        }),
    } as Response);

    await expect(async () => {
      for await (const event of sendChatStream("hello")) {
        void event;
      }
    }).rejects.toMatchObject({
      name: "ApiError",
      message: "Meeting not found",
      code: "HTTP_400",
      requestId: "req-123",
      details: { meeting_id: 999 },
    } satisfies Partial<ApiError>);
  });
});
