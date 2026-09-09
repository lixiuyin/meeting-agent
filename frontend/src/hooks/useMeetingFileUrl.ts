import { useState, useEffect } from "react";
import { prefetchMeetingFileUrl, getMeetingFileUrl } from "../api/client";

export function useMeetingFileUrl(meetingId: number, fileId: number): string {
  const [prefetchedState, setPrefetchedState] = useState<{
    meetingId: number;
    fileId: number;
    url: string;
  } | null>(null);

  useEffect(() => {
    if (!meetingId || !fileId) return;
    let cancelled = false;
    prefetchMeetingFileUrl(meetingId, fileId).then(() => {
      if (!cancelled) {
        setPrefetchedState({
          meetingId,
          fileId,
          url: getMeetingFileUrl(meetingId, fileId),
        });
      }
    });
    return () => {
      cancelled = true;
    };
  }, [meetingId, fileId]);

  return prefetchedState?.meetingId === meetingId && prefetchedState.fileId === fileId
    ? prefetchedState.url
    : "";
}
