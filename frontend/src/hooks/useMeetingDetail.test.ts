import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import * as client from "../api/client";
import { useMeetingDetail } from "./useMeetingDetail";
import type { MeetingDetailInfo, MeetingInfo } from "../api/client";

// Mock the sub-hooks to avoid pulling in their dependencies
vi.mock("./meeting-detail/useMeetingTimestamps", () => ({
  useMeetingTimestamps: () => ({
    timestampsOpen: false,
    setTimestampsOpen: vi.fn(),
    timestampsLoading: false,
    timestampsData: [],
    timestampsSeekTo: undefined,
    setTimestampsSeekTo: vi.fn(),
    activeSegmentIndex: null,
    setActiveSegmentIndex: vi.fn(),
    timestampsPlayback: null,
    timestampsListRef: { current: null },
    handleViewTimestamps: vi.fn(),
    refreshTimestamps: vi.fn(),
  }),
}));

vi.mock("./meeting-detail/useMeetingSummary", () => ({
  useMeetingSummary: () => ({
    summaryOpen: false,
    setSummaryOpen: vi.fn(),
    summaryLoading: false,
    summaryData: null,
    summaryStreaming: false,
    setSummaryStreaming: vi.fn(),
    summaryMeetingId: null,
    summaryAbortRef: { current: null },
    handleGenerateSummary: vi.fn(),
    handleRegenerateSummary: vi.fn(),
    handleCopySummary: vi.fn(),
    handleDownloadSummary: vi.fn(),
    handleNavigateToFile: vi.fn(),
    handleSilentRefreshSummary: vi.fn(),
  }),
}));

vi.mock("./meeting-detail/useMeetingSpeakers", () => ({
  useMeetingSpeakers: () => ({
    speakerModalOpen: false,
    setSpeakerModalOpen: vi.fn(),
    speakerMeetingId: null,
    speakerData: null,
    speakerLoading: false,
    speakerNames: {},
    setSpeakerNames: vi.fn(),
    speakerPlaying: null,
    setSpeakerPlaying: vi.fn(),
    speakerSaving: false,
    stopAudio: vi.fn(),
    handleOpenSpeakers: vi.fn(),
    handlePlaySpeaker: vi.fn(),
    handleSaveSpeakers: vi.fn(),
  }),
}));

vi.mock("./meeting-detail/exportMeetingDetail", () => ({
  exportMeetingDetail: vi.fn(),
  showExportError: vi.fn(),
}));

describe("useMeetingDetail", () => {
  const mockFetchMeetings = vi.fn();
  const asGetMeetingResponse = (data: MeetingDetailInfo) =>
    ({ data }) as Awaited<ReturnType<typeof client.getMeeting>>;
  const createMeeting = (id: number, title: string, status: MeetingInfo["status"]): MeetingInfo =>
    ({
      id,
      title,
      status,
      created_at: "",
      updated_at: "",
      file_name: "",
      file_url: "",
      file_type: null,
      transcript_preview: null,
      summary_preview: null,
      file_count: 0,
      duration_seconds: null,
      description: null,
      meeting_date: null,
      error_message: null,
    }) as MeetingInfo;
  const createDetail = (
    id: number,
    title: string,
    status: MeetingDetailInfo["status"],
    files: MeetingDetailInfo["files"] = [],
  ): MeetingDetailInfo =>
    ({
      id,
      title,
      status,
      files,
      created_at: "",
      updated_at: "",
      description: null,
      meeting_date: null,
      summary: null,
      user_id: "test-user",
    }) as MeetingDetailInfo;

  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("opens detail and fetches meeting data", async () => {
    const mockMeeting = createMeeting(1, "Test Meeting", "ready");
    const mockDetail = createDetail(1, "Test Meeting", "ready");

    const getMeetingSpy = vi
      .spyOn(client, "getMeeting")
      .mockResolvedValue(asGetMeetingResponse(mockDetail));

    const { result } = renderHook(() => useMeetingDetail(mockFetchMeetings));

    await act(async () => {
      await result.current.handleOpenDetail(mockMeeting);
    });

    expect(result.current.detailMeeting).toEqual(mockMeeting);
    expect(result.current.detailFull).toEqual(mockDetail);
    expect(result.current.detailLoading).toBe(false);

    // handleOpenDetail calls getMeeting(id) without signal
    expect(getMeetingSpy).toHaveBeenCalledWith(1);

    getMeetingSpy.mockRestore();
  });

  it("closing detail clears state", async () => {
    const mockMeeting = createMeeting(1, "Test", "ready");
    const mockDetail = createDetail(1, "Test", "ready");

    vi.spyOn(client, "getMeeting").mockResolvedValue(asGetMeetingResponse(mockDetail));

    const { result } = renderHook(() => useMeetingDetail(mockFetchMeetings));

    await act(async () => {
      await result.current.handleOpenDetail(mockMeeting);
    });

    expect(result.current.detailMeeting).not.toBeNull();

    act(() => {
      result.current.handleCloseDetail();
    });

    expect(result.current.detailMeeting).toBeNull();
    expect(result.current.detailFull).toBeNull();
  });

  it("switching meetings sets correct detailFull", async () => {
    const meetingA = createMeeting(1, "Meeting A", "ready");
    const meetingB = createMeeting(2, "Meeting B", "ready");

    const detailA = createDetail(1, "Meeting A", "ready");
    const detailB = createDetail(2, "Meeting B", "ready");

    const getMeetingSpy = vi.spyOn(client, "getMeeting").mockImplementation(async (id: number) => {
      if (id === 1) return asGetMeetingResponse(detailA);
      return asGetMeetingResponse(detailB);
    });

    const { result } = renderHook(() => useMeetingDetail(mockFetchMeetings));

    // Open meeting A
    await act(async () => {
      await result.current.handleOpenDetail(meetingA);
    });
    expect(result.current.detailFull?.id).toBe(1);

    // Switch to meeting B
    await act(async () => {
      await result.current.handleOpenDetail(meetingB);
    });
    expect(result.current.detailFull?.id).toBe(2);
    expect(result.current.detailFull?.title).toBe("Meeting B");

    getMeetingSpy.mockRestore();
  });

  describe("polling with AbortController", () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it("polling passes AbortSignal to getMeeting", async () => {
      const mockMeeting = createMeeting(1, "Polling Test", "processing");
      const mockDetail = createDetail(1, "Polling Test", "processing", [
        { id: 10, status: "processing" } as MeetingDetailInfo["files"][number],
      ]);

      const getMeetingSpy = vi
        .spyOn(client, "getMeeting")
        .mockResolvedValue(asGetMeetingResponse(mockDetail));

      const { result } = renderHook(() => useMeetingDetail(mockFetchMeetings));

      // Open detail to start the polling effect
      await act(async () => {
        await result.current.handleOpenDetail(mockMeeting);
      });

      // Clear initial call from handleOpenDetail (no signal)
      getMeetingSpy.mockClear();

      // Advance past the 5000ms polling interval
      await act(async () => {
        vi.advanceTimersByTime(5500);
      });

      // The polling call should pass an AbortSignal
      expect(getMeetingSpy).toHaveBeenCalledWith(1, { signal: expect.any(AbortSignal) });

      getMeetingSpy.mockRestore();
    });

    it("abort signal is triggered when meeting changes during polling", async () => {
      const meetingA = createMeeting(1, "A", "processing");
      const meetingB = createMeeting(2, "B", "ready");
      const detailA = createDetail(1, "A", "processing", [
        { id: 10, status: "processing" } as MeetingDetailInfo["files"][number],
      ]);
      const detailB = createDetail(2, "B", "ready");

      const signals: AbortSignal[] = [];

      const getMeetingSpy = vi
        .spyOn(client, "getMeeting")
        .mockImplementation(async (id: number, opts?: { signal?: AbortSignal }) => {
          if (opts?.signal) {
            signals.push(opts.signal);
          }
          if (id === 1) return asGetMeetingResponse(detailA);
          return asGetMeetingResponse(detailB);
        });

      const { result } = renderHook(() => useMeetingDetail(mockFetchMeetings));

      // Open meeting A — triggers immediate poll via void poll()
      await act(async () => {
        await result.current.handleOpenDetail(meetingA);
      });

      // Advance to trigger interval poll
      await act(async () => {
        vi.advanceTimersByTime(5500);
      });

      // Switch to meeting B — effect cleanup should abort the polling controller
      await act(async () => {
        await result.current.handleOpenDetail(meetingB);
      });

      // At least one polling signal should have been captured and aborted
      const abortedSignals = signals.filter((s) => s.aborted);
      expect(abortedSignals.length).toBeGreaterThanOrEqual(1);

      getMeetingSpy.mockRestore();
    });
  });
});
