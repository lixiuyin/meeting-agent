import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { message } from "antd";
import {
  deleteMeetingFile,
  fetchMeetingFile,
  formatApiErrorMessage,
  getMeeting,
  getTranscript,
  reprocessMeeting,
  reprocessMeetingFile,
  updateMeeting,
  type MeetingDetailInfo,
  type MeetingInfo,
} from "../api/client";
import { exportMeetingDetail, showExportError } from "./meeting-detail/exportMeetingDetail";
import { useMeetingSpeakers } from "./meeting-detail/useMeetingSpeakers";
import { useMeetingSummary } from "./meeting-detail/useMeetingSummary";
import { useMeetingTimestamps } from "./meeting-detail/useMeetingTimestamps";
import { isUnnamedSpeakerCode } from "./meeting-detail/types";

export type { TimestampPlayback, TimestampSegment } from "./meeting-detail/types";

export function useMeetingDetail(fetchMeetings: () => void) {
  const [detailMeeting, setDetailMeeting] = useState<MeetingInfo | null>(null);
  const [detailFull, setDetailFull] = useState<MeetingDetailInfo | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [fullTranscript, setFullTranscript] = useState<string | null>(null);
  const [loadingTranscript, setLoadingTranscript] = useState(false);
  const [exporting, setExporting] = useState(false);

  const handleOpenDetail = useCallback(async (meeting: MeetingInfo) => {
    setDetailMeeting(meeting);
    setDetailLoading(true);
    setFullTranscript(null);
    try {
      const res = await getMeeting(meeting.id);
      setDetailFull(res.data);
    } catch (err) {
      message.error(formatApiErrorMessage(err, "Failed to load meeting details"));
      setDetailFull(null);
    } finally {
      setDetailLoading(false);
    }
  }, []);

  const handleCloseDetail = useCallback(() => {
    setDetailMeeting(null);
    setDetailFull(null);
    setFullTranscript(null);
  }, []);

  // Auto-refresh the detail drawer while the meeting is open.  The meetings
  // *list* is refreshed by useMeetings on WS events, but the detail (with
  // per-file summary_status) is only fetched once on open — without this
  // effect the file badge sticks at "Summarizing…" forever.
  //
  // Refs prevent the race condition where an old effect's cleanup aborts a
  // controller that a new effect's interval callback is still using.  Each
  // poll cycle aborts any in-flight request before starting a new one.
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const meetingId = detailMeeting?.id;
    if (!meetingId) return;

    const controller = new AbortController();
    abortRef.current = controller;

    const poll = async () => {
      try {
        const res = await getMeeting(meetingId, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setDetailFull(res.data);
        setDetailMeeting((prev) =>
          prev && prev.id === meetingId ? { ...prev, status: res.data.status } : prev,
        );

        const files = res.data.files ?? [];
        const stillPending = files.some(
          (f) =>
            f.status === "processing" || f.status === "summarizing" || f.status === "uploading",
        );
        if (!stillPending && intervalRef.current) {
          clearInterval(intervalRef.current);
          intervalRef.current = null;
        }
      } catch (e) {
        if ((e as Error)?.name === "AbortError") return;
        // transient failures shouldn't spam the user
      }
    };

    // Initial fetch
    const initialPollId = window.setTimeout(() => {
      void poll();
    }, 0);
    intervalRef.current = setInterval(() => void poll(), 5000);

    return () => {
      window.clearTimeout(initialPollId);
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
      controller.abort();
    };
  }, [detailMeeting?.id]);

  const handleReprocess = useCallback(
    async (id: number) => {
      try {
        await reprocessMeeting(id);
        message.success("Reprocessing started");
        fetchMeetings();
      } catch (err) {
        const msg = formatApiErrorMessage(err, "Failed to start reprocessing");
        const { Modal } = await import("antd");
        Modal.error({ width: "min(92vw, 680px)", title: "Reprocess failed", content: msg });
      }
    },
    [fetchMeetings],
  );

  const handleReprocessFile = useCallback(
    async (meetingId: number, fileId: number) => {
      try {
        await reprocessMeetingFile(meetingId, fileId);
        message.success("File reprocessing started");
        void fetchMeetings();
        if (detailMeeting?.id === meetingId) {
          void handleOpenDetail({ ...detailMeeting } as MeetingInfo);
        }
      } catch (err) {
        const msg = formatApiErrorMessage(err, "Failed to start file reprocessing");
        const { Modal } = await import("antd");
        Modal.error({ width: "min(92vw, 680px)", title: "Reprocess file failed", content: msg });
      }
    },
    [fetchMeetings, detailMeeting, handleOpenDetail],
  );

  const handleViewTranscript = useCallback(async (id: number) => {
    setLoadingTranscript(true);
    try {
      const res = await getTranscript(id);
      setFullTranscript(res.data.transcript);
    } catch (err) {
      message.error(formatApiErrorMessage(err, "Failed to load transcript"));
    } finally {
      setLoadingTranscript(false);
    }
  }, []);

  const handleExport = useCallback(async (id: number, format: "json" | "markdown" | "txt") => {
    setExporting(true);
    try {
      await exportMeetingDetail(id, format);
    } catch (err) {
      showExportError(err);
    } finally {
      setExporting(false);
    }
  }, []);

  const handleUpdateMeeting = useCallback(
    async (
      id: number,
      data: { title: string; description: string; meeting_date: string },
      setEditModal: (v: null) => void,
    ) => {
      try {
        const payload: Record<string, string> = {};
        if (data.title.trim()) payload.title = data.title.trim();
        if (data.description.trim()) payload.description = data.description.trim();
        if (data.meeting_date) payload.meeting_date = data.meeting_date;
        await updateMeeting(id, payload);
        message.success("Meeting updated");
        setEditModal(null);
        fetchMeetings();
        if (detailMeeting?.id === id) {
          void handleOpenDetail({ ...detailMeeting, title: data.title } as MeetingInfo);
        }
      } catch (err) {
        message.error(formatApiErrorMessage(err, "Failed to update meeting"));
      }
    },
    [fetchMeetings, detailMeeting, handleOpenDetail],
  );

  const timestamps = useMeetingTimestamps();
  const summary = useMeetingSummary({
    detailMeetingId: detailMeeting?.id,
    setDetailFull: (detail) => setDetailFull(detail),
  });
  const speakers = useMeetingSpeakers({
    fetchMeetings,
    detailMeeting,
    handleOpenDetail,
    timestampsOpen: timestamps.timestampsOpen,
    timestampsPlayback: timestamps.timestampsPlayback,
    refreshTimestamps: timestamps.refreshTimestamps,
    onSummaryRefresh: summary.summaryData
      ? (id: number) => void summary.handleSilentRefreshSummary(id)
      : undefined,
  });

  const handleDownloadFile = useCallback(
    async (meetingId: number, fileId: number, fileName: string) => {
      try {
        const res = await fetchMeetingFile(meetingId, fileId);
        const blob = new Blob([res.data]);
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = fileName;
        a.click();
        setTimeout(() => URL.revokeObjectURL(url), 10_000);
      } catch (err) {
        message.error(formatApiErrorMessage(err, "Download failed"));
      }
    },
    [],
  );

  const handleDeleteFile = useCallback(
    async (meetingId: number, fileId: number) => {
      try {
        await deleteMeetingFile(meetingId, fileId);
        message.success("File deleted");
        void fetchMeetings();
        if (detailMeeting?.id === meetingId) {
          void handleOpenDetail({ ...detailMeeting } as MeetingInfo);
        }
      } catch (err) {
        message.error(formatApiErrorMessage(err, "Failed to delete file"));
      }
    },
    [fetchMeetings, detailMeeting, handleOpenDetail],
  );

  const isUnnamedSpeaker = useCallback(
    (speaker?: string | null) => isUnnamedSpeakerCode(speaker),
    [],
  );

  const detailFileType = useMemo<string | null>(() => {
    const files = detailFull?.files ?? [];
    if (files.length === 0) {
      return detailMeeting?.file_type ?? null;
    }
    const distinct = new Set<string>();
    for (const f of files) {
      if (f.file_type) distinct.add(f.file_type);
    }
    if (distinct.size === 0) return detailMeeting?.file_type ?? null;
    if (distinct.size === 1) return distinct.values().next().value ?? null;
    return "mixed";
  }, [detailFull, detailMeeting?.file_type]);
  const hasParsedDocumentContent = useMemo(
    () => Boolean(detailMeeting && (detailMeeting.transcript_preview || fullTranscript)),
    [detailMeeting, fullTranscript],
  );

  return {
    detailMeeting,
    setDetailMeeting,
    detailFull,
    detailLoading,
    fullTranscript,
    setFullTranscript,
    loadingTranscript,
    exporting,
    timestampsOpen: timestamps.timestampsOpen,
    setTimestampsOpen: timestamps.setTimestampsOpen,
    timestampsLoading: timestamps.timestampsLoading,
    timestampsData: timestamps.timestampsData,
    timestampsSeekTo: timestamps.timestampsSeekTo,
    setTimestampsSeekTo: timestamps.setTimestampsSeekTo,
    activeSegmentIndex: timestamps.activeSegmentIndex,
    setActiveSegmentIndex: timestamps.setActiveSegmentIndex,
    timestampsPlayback: timestamps.timestampsPlayback,
    timestampsListRef: timestamps.timestampsListRef,
    summaryOpen: summary.summaryOpen,
    setSummaryOpen: summary.setSummaryOpen,
    summaryLoading: summary.summaryLoading,
    summaryData: summary.summaryData,
    summaryStreaming: summary.summaryStreaming,
    setSummaryStreaming: summary.setSummaryStreaming,
    summaryMeetingId: summary.summaryMeetingId,
    summaryAbortRef: summary.summaryAbortRef,
    speakerModalOpen: speakers.speakerModalOpen,
    setSpeakerModalOpen: speakers.setSpeakerModalOpen,
    speakerMeetingId: speakers.speakerMeetingId,
    speakerData: speakers.speakerData,
    speakerLoading: speakers.speakerLoading,
    speakerNames: speakers.speakerNames,
    setSpeakerNames: speakers.setSpeakerNames,
    speakerPlaying: speakers.speakerPlaying,
    setSpeakerPlaying: speakers.setSpeakerPlaying,
    speakerSaving: speakers.speakerSaving,
    stopAudio: speakers.stopAudio,
    detailFileType,
    hasParsedDocumentContent,
    isUnnamedSpeaker,
    handleOpenDetail,
    handleCloseDetail,
    handleReprocess,
    handleReprocessFile,
    handleViewTranscript,
    handleExport,
    handleUpdateMeeting,
    handleViewTimestamps: timestamps.handleViewTimestamps,
    handleGenerateSummary: summary.handleGenerateSummary,
    handleRegenerateSummary: summary.handleRegenerateSummary,
    handleCopySummary: summary.handleCopySummary,
    handleDownloadSummary: summary.handleDownloadSummary,
    handleNavigateToFile: summary.handleNavigateToFile,
    handleOpenSpeakers: speakers.handleOpenSpeakers,
    handlePlaySpeaker: speakers.handlePlaySpeaker,
    handleSaveSpeakers: speakers.handleSaveSpeakers,
    handleDownloadFile,
    handleDeleteFile,
  };
}
