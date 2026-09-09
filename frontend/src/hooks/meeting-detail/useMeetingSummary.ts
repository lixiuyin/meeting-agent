import { useCallback, useEffect, useRef, useState } from "react";
import { message } from "antd";
import {
  generateSummary,
  getMeeting,
  getSummary,
  sendMeetingSummaryStream,
  formatApiErrorMessage,
  isRequestCanceled,
  type MeetingDetailInfo,
} from "../../api/client";
import { reportNonCriticalError } from "../../utils/monitoring";

interface Options {
  detailMeetingId?: number;
  setDetailFull: (detail: MeetingDetailInfo) => void;
}

export function useMeetingSummary({ detailMeetingId, setDetailFull }: Options) {
  const [summaryOpen, setSummaryOpen] = useState(false);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryData, setSummaryData] = useState<string | null>(null);
  const [summaryStreaming, setSummaryStreaming] = useState(false);
  const [summaryMeetingId, setSummaryMeetingId] = useState<number | null>(null);
  const summaryAbortRef = useRef<AbortController | null>(null);

  const handleGenerateSummary = useCallback(
    async (id: number) => {
      setSummaryOpen(true);
      setSummaryMeetingId(id);
      summaryAbortRef.current?.abort();

      // Try to fetch a pre-generated summary first
      try {
        const existing = await getSummary(id);
        if (existing.data?.summary) {
          setSummaryData(existing.data.summary);
          setSummaryLoading(false);
          setSummaryStreaming(false);
          return;
        }
      } catch {
        // No pre-generated summary — fall through to generation
      }

      setSummaryLoading(true);
      setSummaryData(null);
      setSummaryStreaming(false);
      const controller = new AbortController();
      summaryAbortRef.current = controller;
      let summaryCompleted = false;
      try {
        let accumulated = "";
        setSummaryStreaming(true);
        for await (const event of sendMeetingSummaryStream(id, { signal: controller.signal })) {
          if (event.type === "token") {
            accumulated += event.content;
            setSummaryData(accumulated);
            setSummaryLoading(false);
          } else if (event.type === "done") {
            setSummaryLoading(false);
            setSummaryStreaming(false);
            summaryAbortRef.current = null;
            summaryCompleted = true;
          } else if (event.type === "error") {
            throw new Error(event.message);
          }
        }
        if (!accumulated) {
          const res = await generateSummary(id);
          setSummaryData(res.data.summary ?? "");
          summaryCompleted = true;
        }
      } catch (err) {
        if (!isRequestCanceled(err)) {
          message.error(formatApiErrorMessage(err, "Failed to generate summary"));
        }
        setSummaryStreaming(false);
      } finally {
        if (summaryCompleted && detailMeetingId === id) {
          try {
            const refreshed = await getMeeting(id);
            setDetailFull(refreshed.data);
          } catch (err) {
            reportNonCriticalError("refresh meeting after summary", err);
          }
        }
        setSummaryLoading(false);
      }
    },
    [detailMeetingId, setDetailFull],
  );

  const handleCopySummary = useCallback(async () => {
    if (!summaryData) return;
    try {
      await navigator.clipboard.writeText(summaryData);
      message.success("Summary copied");
    } catch (err) {
      reportNonCriticalError("copy summary to clipboard", err);
      message.error("Failed to copy summary");
    }
  }, [summaryData]);

  const handleDownloadSummary = useCallback(() => {
    if (!summaryData) return;
    const blob = new Blob([summaryData], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    const ts = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
    a.download = `meeting-${summaryMeetingId ?? "unknown"}-summary-${ts}.md`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }, [summaryData, summaryMeetingId]);

  const handleRegenerateSummary = useCallback(
    async (id: number) => {
      setSummaryLoading(true);
      setSummaryData(null);
      setSummaryStreaming(false);
      summaryAbortRef.current?.abort();
      const controller = new AbortController();
      summaryAbortRef.current = controller;
      let summaryCompleted = false;
      try {
        let accumulated = "";
        setSummaryStreaming(true);
        for await (const event of sendMeetingSummaryStream(id, { signal: controller.signal })) {
          if (event.type === "token") {
            accumulated += event.content;
            setSummaryData(accumulated);
            setSummaryLoading(false);
          } else if (event.type === "done") {
            setSummaryLoading(false);
            setSummaryStreaming(false);
            summaryAbortRef.current = null;
            summaryCompleted = true;
          } else if (event.type === "error") {
            throw new Error(event.message);
          }
        }
        if (!accumulated) {
          const res = await generateSummary(id);
          setSummaryData(res.data.summary ?? "");
          summaryCompleted = true;
        }
      } catch (err) {
        if (!isRequestCanceled(err)) {
          message.error(formatApiErrorMessage(err, "Failed to regenerate summary"));
        }
        setSummaryStreaming(false);
      } finally {
        if (summaryCompleted && detailMeetingId === id) {
          try {
            const refreshed = await getMeeting(id);
            setDetailFull(refreshed.data);
          } catch (err) {
            reportNonCriticalError("refresh meeting after summary regen", err);
          }
        }
        setSummaryLoading(false);
      }
    },
    [detailMeetingId, setDetailFull],
  );

  const handleNavigateToFile = useCallback(() => {
    // When a [file:ID] citation is clicked, the parent (MaterialsPage)
    // wires this callback to scroll to / highlight the file in the detail
    // drawer.  The actual navigation is handled by the page component.
  }, []);

  useEffect(() => {
    return () => {
      summaryAbortRef.current?.abort();
    };
  }, []);

  const handleSilentRefreshSummary = useCallback(async (id: number) => {
    try {
      const existing = await getSummary(id);
      if (existing.data?.summary) {
        setSummaryData(existing.data.summary);
      }
    } catch {
      // Summary not available yet — nothing to refresh
    }
  }, []);

  return {
    summaryOpen,
    setSummaryOpen,
    summaryLoading,
    summaryData,
    summaryStreaming,
    setSummaryStreaming,
    summaryMeetingId,
    summaryAbortRef,
    handleGenerateSummary,
    handleRegenerateSummary,
    handleSilentRefreshSummary,
    handleCopySummary,
    handleDownloadSummary,
    handleNavigateToFile,
  };
}
