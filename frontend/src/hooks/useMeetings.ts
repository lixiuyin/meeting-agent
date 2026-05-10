import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { message, Modal } from "antd";
import { deleteMeeting, formatApiErrorMessage, listMeetings } from "../api/client";
import { useWebSocket } from "./useWebSocket";
import type { MeetingInfo } from "../api/client";

const PROCESSING_POLL_MS = 5000;

export interface UseMeetingsOptions {
  pollInterval?: number;
}

function getWsUrl() {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  return `${protocol}://${window.location.host}/api/v1/ws?client_id=${crypto.randomUUID()}`;
}

export function useMeetings(options: UseMeetingsOptions = {}) {
  const { pollInterval = PROCESSING_POLL_MS } = options;
  const [meetings, setMeetings] = useState<MeetingInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const meetingsRef = useRef(meetings);
  useEffect(() => {
    meetingsRef.current = meetings;
  }, [meetings]);
  const reportedFailureRef = useRef<Set<number>>(new Set());

  const wsUrl = useMemo(() => getWsUrl(), []);
  const { lastMessage } = useWebSocket(wsUrl);

  const fetchMeetings = useCallback(async () => {
    // Cancel previous request
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setLoading(true);
    try {
      const res = await listMeetings({ limit: 100 });
      if (!controller.signal.aborted) {
        const incoming = res.data.meetings;
        const prevById = new Map(meetingsRef.current.map((m) => [m.id, m]));

        for (const meeting of incoming) {
          const isFailed = meeting.status === "failed" || meeting.status === "error";
          if (!isFailed) {
            reportedFailureRef.current.delete(meeting.id);
            continue;
          }

          const prev = prevById.get(meeting.id);
          const wasFailed = prev ? prev.status === "failed" || prev.status === "error" : false;
          const alreadyReported = reportedFailureRef.current.has(meeting.id);
          if (!wasFailed || !alreadyReported) {
            const detail = meeting.error_message?.trim();
            message.error(
              detail
                ? `${meeting.title}: ${detail}`
                : `${meeting.title}: processing failed. Please re-upload or reprocess.`,
            );
            reportedFailureRef.current.add(meeting.id);
          }
        }

        setMeetings(incoming);
      }
    } catch (err) {
      if (!controller.signal.aborted) {
        message.error(formatApiErrorMessage(err, "Failed to load materials"));
      }
    } finally {
      if (!controller.signal.aborted) {
        setLoading(false);
      }
    }
  }, []);

  const scheduleFetchMeetings = useCallback(() => {
    const timeoutId = window.setTimeout(() => {
      void fetchMeetings();
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [fetchMeetings]);

  // Initial fetch
  useEffect(() => {
    return scheduleFetchMeetings();
  }, [scheduleFetchMeetings]);

  // Refresh on WebSocket progress/complete messages
  useEffect(() => {
    if (lastMessage && (lastMessage.type === "complete" || lastMessage.type === "progress")) {
      return scheduleFetchMeetings();
    }
  }, [lastMessage, scheduleFetchMeetings]);

  // Polling for processing meetings (fallback)
  useEffect(() => {
    pollRef.current = setInterval(() => {
      const hasProcessing = meetingsRef.current.some(
        (m) => m.status === "processing" || m.status === "uploading" || m.status === "summarizing",
      );
      if (hasProcessing) {
        void fetchMeetings();
      } else if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    }, pollInterval);

    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [fetchMeetings, pollInterval]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      if (pollRef.current) {
        clearInterval(pollRef.current);
      }
    };
  }, []);

  const deleteMeetings = useCallback(
    async (ids: number[]) => {
      return new Promise<boolean>((resolve) => {
        Modal.confirm({
          title: ids.length === 1 ? "Delete Meeting" : `Delete ${ids.length} Meetings`,
          content: `This will permanently remove ${ids.length === 1 ? "this meeting" : "these meetings"} and all associated data (files, transcripts, vectors). This action cannot be undone.`,
          okType: "danger",
          okText: "Delete",
          onOk: async () => {
            try {
              await Promise.all(ids.map((id) => deleteMeeting(id)));
              message.success(`Deleted ${ids.length} materials`);
              await fetchMeetings();
              resolve(true);
            } catch (err) {
              message.error(formatApiErrorMessage(err, "Failed to delete materials"));
              resolve(false);
            }
          },
          onCancel: () => resolve(false),
        });
      });
    },
    [fetchMeetings],
  );

  return useMemo(
    () => ({
      meetings,
      loading,
      fetchMeetings,
      deleteMeetings,
    }),
    [meetings, loading, fetchMeetings, deleteMeetings],
  );
}
