import type { PropsWithChildren } from "react";
import { act, renderHook, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { I18nProvider } from "../i18n/I18nProvider";
import * as client from "../api/client";
import { useSessionManager } from "./useSessionManager";

const mocks = vi.hoisted(() => ({
  streamSessionId: undefined as string | undefined,
  messages: [] as { role: "user" | "agent"; content: string; id: string; serverId?: number }[],
  isStreaming: false,
  setMessages: vi.fn(),
  setSessionId: vi.fn(),
  abortStream: vi.fn(),
  startStream: vi.fn(),
  setUseWebSearch: vi.fn(),
  setSelectedTypeFilters: vi.fn(),
  setDateFrom: vi.fn(),
  setDateTo: vi.fn(),
  setValidAt: vi.fn(),
  setKnownAt: vi.fn(),
  setContinuationMode: vi.fn(),
  setRagMode: vi.fn(),
  chatOptions: {} as Record<string, unknown>,
}));

vi.mock("./useChatStream", () => ({
  useChatStream: () => ({
    messages: mocks.messages,
    isStreaming: mocks.isStreaming,
    sessionId: mocks.streamSessionId,
    streamError: null,
    streamErrorCode: null,
    streamErrorDetail: null,
    streamRequestId: null,
    streamNotice: null,
    setStreamError: vi.fn(),
    setStreamNotice: vi.fn(),
    setSessionId: mocks.setSessionId,
    startStream: mocks.startStream,
    abortStream: mocks.abortStream,
    clearMessages: vi.fn(),
    setMessages: mocks.setMessages,
  }),
}));

vi.mock("./useChatOptions", () => ({
  useChatOptions: () => ({
    paramsExpanded: false,
    setParamsExpanded: vi.fn(),
    useWebSearch: false,
    setUseWebSearch: mocks.setUseWebSearch,
    selectedTypeFilters: [],
    setSelectedTypeFilters: mocks.setSelectedTypeFilters,
    dateFrom: "",
    setDateFrom: mocks.setDateFrom,
    dateTo: "",
    setDateTo: mocks.setDateTo,
    validAt: "",
    setValidAt: mocks.setValidAt,
    knownAt: "",
    setKnownAt: mocks.setKnownAt,
    continuationMode: "latest",
    setContinuationMode: mocks.setContinuationMode,
    ragMode: "auto",
    setRagMode: mocks.setRagMode,
    retrievalProfile: "balanced",
    setRetrievalProfile: vi.fn(),
    memoryMode: "balanced",
    setMemoryMode: vi.fn(),
    chatOptions: mocks.chatOptions,
    activeParamCount: 0,
  }),
}));

vi.mock("./useSessionSelection", () => ({
  useSessionSelection: () => ({
    selectedMeetingIds: [],
    setSelectedMeetingIds: vi.fn(),
    selectedFileIds: [],
    setSelectedFileIds: vi.fn(),
    meetings: [],
    refreshMeetings: vi.fn(),
    meetingFilesMap: {},
    loadingMeetings: false,
    loadingFiles: false,
    removeSelectedMeeting: vi.fn(),
    removeSelectedFile: vi.fn(),
    meetingOptions: [],
    selectedMeetings: [],
    fileOptions: [],
    selectedFiles: [],
    resolveEffectiveFileIds: vi.fn(() => undefined),
  }),
}));

function wrapper(initialEntry: string) {
  return function TestWrapper({ children }: PropsWithChildren) {
    return (
      <I18nProvider>
        <MemoryRouter initialEntries={[initialEntry]}>{children}</MemoryRouter>
      </I18nProvider>
    );
  };
}

describe("useSessionManager session continuity", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mocks.streamSessionId = undefined;
    mocks.messages = [];
    mocks.isStreaming = false;
    mocks.setMessages.mockReset();
    mocks.setSessionId.mockReset();
    mocks.abortStream.mockReset();
    mocks.startStream.mockReset();
    mocks.setUseWebSearch.mockReset();
    mocks.setSelectedTypeFilters.mockReset();
    mocks.setDateFrom.mockReset();
    mocks.setDateTo.mockReset();
    mocks.setValidAt.mockReset();
    mocks.setKnownAt.mockReset();
    mocks.setContinuationMode.mockReset();
    mocks.setRagMode.mockReset();
    mocks.chatOptions = {};
  });

  it("writes a newly assigned server session to the URL", async () => {
    const getSessionMessages = vi.spyOn(client, "getSessionMessages");
    const { result, rerender } = renderHook(
      () => ({ manager: useSessionManager(), location: useLocation() }),
      { wrapper: wrapper("/") },
    );

    await waitFor(() => expect(result.current).not.toBeNull());
    expect(result.current.location.search).toBe("");
    mocks.streamSessionId = "session_new_123";
    rerender();

    await waitFor(() => expect(result.current.location.search).toBe("?sessionId=session_new_123"));
    expect(getSessionMessages).not.toHaveBeenCalled();
  });

  it("aborts stale work and restores the session selected in the URL", async () => {
    vi.spyOn(client, "getSessionMessages").mockResolvedValue({
      data: {
        session: {} as never,
        messages: [{ id: 7, role: "human", content: "Previous question", sources: [] }],
        total: 1,
        next_before_id: null,
      },
    } as never);

    renderHook(() => useSessionManager(), {
      wrapper: wrapper("/?sessionId=session_old_123"),
    });

    await waitFor(() => expect(mocks.setSessionId).toHaveBeenCalledWith("session_old_123"));
    expect(mocks.abortStream).toHaveBeenCalledWith({ silent: true });
    expect(mocks.setMessages).toHaveBeenCalledWith([
      expect.objectContaining({
        id: "session_old_123-7",
        serverId: 7,
        role: "user",
        content: "Previous question",
      }),
    ]);
  });

  it("edits a saved user message by creating a new branch", async () => {
    mocks.streamSessionId = "session_source_123";
    mocks.messages = [
      { role: "user", content: "original", id: "u", serverId: 7 },
      { role: "agent", content: "answer", id: "a", serverId: 8 },
    ];
    vi.spyOn(client, "branchSession").mockResolvedValue({
      data: {
        session: { id: "session_branch_123" },
        messages: [],
      },
    } as never);
    const { result } = renderHook(() => useSessionManager(), { wrapper: wrapper("/") });
    await waitFor(() => expect(result.current).not.toBeNull());

    act(() => result.current.handleEditUserMessage(mocks.messages[0]));
    expect(result.current.input).toBe("original");
    await act(() => result.current.handleSend());

    expect(client.branchSession).toHaveBeenCalledWith("session_source_123", 7, "edit");
    expect(mocks.startStream).toHaveBeenCalledWith(
      expect.objectContaining({ question: "original", sessionId: "session_branch_123" }),
    );
  });

  it("regenerates in a branch instead of duplicating the source session", async () => {
    mocks.streamSessionId = "session_source_456";
    mocks.messages = [
      { role: "user", content: "retry me", id: "u", serverId: 17 },
      { role: "agent", content: "old answer", id: "a", serverId: 18 },
    ];
    vi.spyOn(client, "branchSession").mockResolvedValue({
      data: { session: { id: "session_branch_456" }, messages: [] },
    } as never);
    const { result } = renderHook(() => useSessionManager(), { wrapper: wrapper("/") });
    await waitFor(() => expect(result.current).not.toBeNull());

    await act(() => result.current.handleRegenerate());

    expect(client.branchSession).toHaveBeenCalledWith("session_source_456", 17, "regenerate");
    expect(mocks.startStream).toHaveBeenCalledWith(
      expect.objectContaining({
        question: "retry me",
        sessionId: "session_branch_456",
        clientTurnId: expect.any(String),
        onRunIdentified: expect.any(Function),
        onCompleted: expect.any(Function),
      }),
    );
  });

  it("uses latest continuation for the first request in a new branch", async () => {
    mocks.streamSessionId = "session_snapshot_source";
    mocks.chatOptions = { continuationMode: "saved_snapshot", memoryMode: "balanced" };
    mocks.messages = [
      { role: "user", content: "retry from latest", id: "u", serverId: 27 },
      { role: "agent", content: "old answer", id: "a", serverId: 28 },
    ];
    vi.spyOn(client, "branchSession").mockResolvedValue({
      data: { session: { id: "session_latest_branch" }, messages: [] },
    } as never);
    const { result } = renderHook(() => useSessionManager(), { wrapper: wrapper("/") });
    await waitFor(() => expect(result.current).not.toBeNull());

    await act(() => result.current.handleRegenerate());

    expect(mocks.startStream).toHaveBeenCalledWith(
      expect.objectContaining({
        sessionId: "session_latest_branch",
        options: expect.objectContaining({
          continuationMode: "latest",
          memoryMode: "balanced",
        }),
      }),
    );
  });

  it("retries a failed unsaved user turn instead of waiting forever for a server id", async () => {
    mocks.messages = [{ role: "user", content: "retry failed turn", id: "local-user" }];
    const { result } = renderHook(() => useSessionManager(), { wrapper: wrapper("/") });
    await waitFor(() => expect(result.current).not.toBeNull());

    await act(() => result.current.handleRegenerate());

    expect(mocks.setMessages).toHaveBeenCalledWith(expect.any(Function));
    expect(mocks.startStream).toHaveBeenCalledWith(
      expect.objectContaining({ question: "retry failed turn", clientTurnId: expect.any(String) }),
    );
  });

  it("restores temporal coordinates and continuation semantics", async () => {
    vi.spyOn(client, "getSessionMessages").mockResolvedValue({
      data: {
        session: {} as never,
        messages: [],
        total: 0,
        next_before_id: null,
        session_config: {
          schema_version: 1,
          valid_at: "2025-03-01T00:00:00Z",
          known_at: "2025-04-01T00:00:00Z",
          continuation_mode: "saved_scope",
        },
      },
    } as never);

    renderHook(() => useSessionManager(), {
      wrapper: wrapper("/?sessionId=session_temporal_123"),
    });

    await waitFor(() => expect(mocks.setSessionId).toHaveBeenCalledWith("session_temporal_123"));
    expect(mocks.setValidAt).toHaveBeenCalledWith(expect.stringMatching(/^2025-03-01T/));
    expect(mocks.setKnownAt).toHaveBeenCalledWith(expect.stringMatching(/^2025-04-01T/));
    expect(mocks.setContinuationMode).toHaveBeenCalledWith("saved_scope");
  });

  it("does not let a delayed stop response restore a session after New chat", async () => {
    vi.spyOn(client, "getSessionMessages").mockResolvedValue({
      data: {
        session: {} as never,
        messages: [],
        total: 0,
        next_before_id: null,
        pending_run: { id: "run-delayed", question: "q", status: "running" },
      },
    } as never);
    let releaseCancel!: () => void;
    vi.spyOn(client, "cancelChatRun").mockImplementation(
      () => new Promise((resolve) => (releaseCancel = () => resolve({ data: {} } as never))),
    );
    const { result } = renderHook(() => useSessionManager(), {
      wrapper: wrapper("/?sessionId=session_delayed_123"),
    });
    await waitFor(() => expect(result.current.pendingRun?.id).toBe("run-delayed"));

    let stopping!: Promise<void>;
    act(() => {
      stopping = result.current.handleStop();
    });
    act(() => result.current.handleNewSession());
    await act(async () => {
      releaseCancel();
      await stopping;
    });

    expect(client.getSessionMessages).toHaveBeenCalledTimes(1);
  });

  it("does not let a delayed stop response overwrite a new send in the same session", async () => {
    vi.spyOn(client, "getSessionMessages").mockResolvedValue({
      data: {
        session: {} as never,
        messages: [],
        total: 0,
        next_before_id: null,
        pending_run: { id: "run-old", question: "old", status: "running" },
      },
    } as never);
    let releaseCancel!: () => void;
    vi.spyOn(client, "cancelChatRun").mockImplementation(
      () => new Promise((resolve) => (releaseCancel = () => resolve({ data: {} } as never))),
    );
    const { result } = renderHook(() => useSessionManager(), {
      wrapper: wrapper("/?sessionId=session_same_123"),
    });
    await waitFor(() => expect(result.current.pendingRun?.id).toBe("run-old"));

    let stopping!: Promise<void>;
    act(() => {
      stopping = result.current.handleStop();
    });
    await act(() => result.current.handleSend("new question"));
    mocks.setMessages.mockClear();
    await act(async () => {
      releaseCancel();
      await stopping;
    });

    expect(mocks.setMessages).not.toHaveBeenCalled();
  });

  it("invalidates a delayed withdraw response after New chat", async () => {
    vi.spyOn(client, "getSessionMessages").mockResolvedValue({
      data: {
        session: {} as never,
        messages: [],
        total: 0,
        next_before_id: null,
        pending_run: { id: "run-withdraw", question: "q", status: "running" },
      },
    } as never);
    let releaseWithdraw!: () => void;
    vi.spyOn(client, "withdrawChatRun").mockImplementation(
      () =>
        new Promise(
          (resolve) =>
            (releaseWithdraw = () =>
              resolve({
                data: {
                  session: { id: "session_must_not_activate" },
                  messages: [],
                  total: 0,
                  next_before_id: null,
                },
              } as never)),
        ),
    );
    const { result } = renderHook(() => useSessionManager(), {
      wrapper: wrapper("/?sessionId=session_withdraw_123"),
    });
    await waitFor(() => expect(result.current.pendingRun?.id).toBe("run-withdraw"));
    mocks.setSessionId.mockClear();

    let withdrawing!: Promise<void>;
    act(() => {
      withdrawing = result.current.handleWithdrawCurrent();
    });
    act(() => result.current.handleNewSession());
    await act(async () => {
      releaseWithdraw();
      await withdrawing;
    });

    expect(mocks.setSessionId).not.toHaveBeenCalledWith("session_must_not_activate");
  });

  it("clears request-scoped filters when starting a new chat", async () => {
    const { result } = renderHook(() => useSessionManager(), { wrapper: wrapper("/") });
    await waitFor(() => expect(result.current).not.toBeNull());
    act(() => result.current.handleNewSession());
    expect(mocks.setSelectedTypeFilters).toHaveBeenCalledWith([]);
    expect(mocks.setDateFrom).toHaveBeenCalledWith("");
    expect(mocks.setDateTo).toHaveBeenCalledWith("");
    expect(mocks.setValidAt).toHaveBeenCalledWith("");
    expect(mocks.setKnownAt).toHaveBeenCalledWith("");
    expect(mocks.setContinuationMode).toHaveBeenCalledWith("latest");
    expect(mocks.setRagMode).toHaveBeenCalledWith("auto");
    expect(mocks.setUseWebSearch).toHaveBeenCalledWith(false);
  });

  it("shows the latest page first and loads older history on demand", async () => {
    const getSessionMessages = vi.spyOn(client, "getSessionMessages");
    getSessionMessages
      .mockResolvedValueOnce({
        data: {
          session: {} as never,
          messages: [{ id: 201, role: "ai", content: "Newest", sources: [] }],
          total: 201,
          next_before_id: 201,
        },
      } as never)
      .mockResolvedValueOnce({
        data: {
          session: {} as never,
          messages: [{ id: 1, role: "human", content: "Oldest", sources: [] }],
          total: 201,
          next_before_id: null,
        },
      } as never);

    const { result } = renderHook(() => useSessionManager(), {
      wrapper: wrapper("/?sessionId=session_long_123"),
    });

    await waitFor(() => expect(mocks.setSessionId).toHaveBeenCalledWith("session_long_123"));
    expect(getSessionMessages).toHaveBeenCalledTimes(1);
    expect(result.current.hasOlderMessages).toBe(true);
    await act(() => result.current.loadOlderMessages());
    expect(getSessionMessages).toHaveBeenNthCalledWith(
      2,
      "session_long_123",
      expect.objectContaining({ beforeId: 201, limit: 200 }),
    );
    const updater = mocks.setMessages.mock.calls[mocks.setMessages.mock.calls.length - 1][0];
    expect(updater([{ id: "session_long_123-201", content: "Newest" }])).toEqual([
      expect.objectContaining({ id: "session_long_123-1", content: "Oldest" }),
      expect.objectContaining({ id: "session_long_123-201", content: "Newest" }),
    ]);
    expect(result.current.hasOlderMessages).toBe(false);
  });

  it("keeps interrupted recovery available until a completed terminal event", async () => {
    vi.spyOn(client, "getSessionMessages").mockResolvedValue({
      data: {
        session: {} as never,
        messages: [],
        total: 0,
        next_before_id: null,
        pending_run: { id: "run-1", question: "Recover me", status: "interrupted" },
      },
    } as never);
    mocks.startStream.mockImplementation(async (params) => {
      expect(params.options.resumeRunId).toBe("run-1");
    });

    const { result } = renderHook(() => useSessionManager(), {
      wrapper: wrapper("/?sessionId=session_pending_123"),
    });
    await waitFor(() => expect(result.current.pendingRun?.id).toBe("run-1"));
    await act(() => result.current.resumePendingRun());
    expect(result.current.pendingRun?.id).toBe("run-1");

    const params = mocks.startStream.mock.calls[0][0];
    act(() => params.onCompleted());
    expect(result.current.pendingRun).toBeNull();
  });
});
