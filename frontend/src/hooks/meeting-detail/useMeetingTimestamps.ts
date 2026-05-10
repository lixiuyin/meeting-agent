import { useCallback, useEffect, useRef, useState } from "react";
import { message } from "antd";
import { getFileTimeline, getMeeting, formatApiErrorMessage } from "../../api/client";
import { getKindCapabilities } from "../../types/fileKinds";
import type { TimestampPlayback, TimestampSegment } from "./types";

async function loadTimelineContext(meetingId: number, fileId: number) {
  const [timelineRes, detailRes] = await Promise.all([
    getFileTimeline(meetingId, fileId),
    getMeeting(meetingId),
  ]);
  const selectedFile = detailRes.data.files.find((file) => file.id === fileId);
  const playback: TimestampPlayback | null =
    selectedFile &&
    selectedFile.status === "ready" &&
    getKindCapabilities(selectedFile.file_type).hasTimeline
      ? {
          meetingId,
          fileId: selectedFile.id,
          fileName: selectedFile.file_name,
          fileType: selectedFile.file_type,
        }
      : null;
  const segments = timelineRes.data.kind === "segments" ? timelineRes.data.segments : [];
  return { playback, segments };
}

export function useMeetingTimestamps() {
  const [timestampsOpen, setTimestampsOpen] = useState(false);
  const [timestampsLoading, setTimestampsLoading] = useState(false);
  const [timestampsData, setTimestampsData] = useState<TimestampSegment[]>([]);
  const [timestampsSeekTo, setTimestampsSeekTo] = useState<number | undefined>(undefined);
  const [activeSegmentIndex, setActiveSegmentIndex] = useState<number | null>(null);
  const [timestampsPlayback, setTimestampsPlayback] = useState<TimestampPlayback | null>(null);
  const timestampsListRef = useRef<HTMLDivElement>(null);

  const handleViewTimestamps = useCallback(async (meetingId: number, fileId: number) => {
    setTimestampsOpen(true);
    setTimestampsLoading(true);
    setTimestampsSeekTo(undefined);
    setActiveSegmentIndex(null);
    try {
      const { playback, segments } = await loadTimelineContext(meetingId, fileId);
      setTimestampsPlayback(playback);
      setTimestampsData(segments);
    } catch (err) {
      message.error(formatApiErrorMessage(err, "Failed to load timestamps"));
      setTimestampsData([]);
      setTimestampsPlayback(null);
    } finally {
      setTimestampsLoading(false);
    }
  }, []);

  const refreshTimestamps = useCallback(async (meetingId: number, fileId: number) => {
    try {
      const { playback, segments } = await loadTimelineContext(meetingId, fileId);
      setTimestampsPlayback(playback);
      setTimestampsData(segments);
      setActiveSegmentIndex(null);
    } catch (err) {
      message.error(formatApiErrorMessage(err, "Failed to refresh timestamps"));
    }
  }, []);

  useEffect(() => {
    if (activeSegmentIndex == null || !timestampsListRef.current) return;
    const target = timestampsListRef.current.querySelector(
      `[data-segment-index="${activeSegmentIndex}"]`,
    ) as HTMLDivElement | null;
    target?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [activeSegmentIndex]);

  return {
    timestampsOpen,
    setTimestampsOpen,
    timestampsLoading,
    timestampsData,
    timestampsSeekTo,
    setTimestampsSeekTo,
    activeSegmentIndex,
    setActiveSegmentIndex,
    timestampsPlayback,
    timestampsListRef,
    handleViewTimestamps,
    refreshTimestamps,
  };
}
