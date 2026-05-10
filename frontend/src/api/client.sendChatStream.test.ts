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
