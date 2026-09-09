import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { message, Modal } from "antd";
import { useIntl } from "react-intl";
import {
  createWebSocketToken,
  deleteMeeting,
  formatApiErrorMessage,
  listAllMeetings,
  listMeetings,
} from "../api/client";
import { reportNonCriticalError } from "../utils/monitoring";
import { useWebSocket } from "./useWebSocket";
import type { MeetingInfo } from "../api/client";

const PROCESSING_POLL_MS = 5000;

export interface UseMeetingsOptions {
  pollInterval?: number;
}

function getWsUrl(token: string) {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const params = new URLSearchParams({ client_id: crypto.randomUUID(), token });
  return `${protocol}://${window.location.host}/api/v1/ws?${params.toString()}`;
}

export function useMeetings(options: UseMeetingsOptions = {}) {
  const { formatMessage } = useIntl();
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

  const [wsUrl, setWsUrl] = useState<string | null>(null);
  const { lastMessage } = useWebSocket(wsUrl);

  // Browsers cannot attach X-API-Key to a WebSocket upgrade. Exchange normal
  // HTTP authentication for a five-minute, user-bound token and rotate it
  // before expiry. Polling remains the fallback if token acquisition fails.
  useEffect(() => {
    let active = true;
    let refreshTimer: ReturnType<typeof setTimeout> | null = null;

    const refresh = async () => {
      try {
        const token = await createWebSocketToken();
        if (!active) return;
        setWsUrl(getWsUrl(token));
        refreshTimer = setTimeout(() => void refresh(), 240_000);
      } catch (err) {
        if (!active) return;
        setWsUrl(null);
        reportNonCriticalError("obtain websocket token", err);
        refreshTimer = setTimeout(() => void refresh(), 30_000);
      }
    };

    void refresh();
    return () => {
      active = false;
      if (refreshTimer) clearTimeout(refreshTimer);
    };
  }, []);

  const loadMeetings = useCallback(
    async (allPages: boolean) => {
      // Cancel previous request
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      setLoading(true);
      try {
        const incoming = allPages
          ? await listAllMeetings({ signal: controller.signal })
          : (await listMeetings({ limit: 100, signal: controller.signal })).data.meetings;
        if (!controller.signal.aborted) {
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
                  : formatMessage({ id: "materials.processingFailed" }, { title: meeting.title }),
              );
              reportedFailureRef.current.add(meeting.id);
            }
          }

          setMeetings(
            allPages
              ? incoming
              : [
                  ...incoming,
                  ...meetingsRef.current.filter(
                    (existing) => !incoming.some((meeting) => meeting.id === existing.id),
                  ),
                ],
          );
        }
      } catch (err) {
        if (!controller.signal.aborted) {
          message.error(formatApiErrorMessage(err, formatMessage({ id: "materials.loadFailed" })));
        }
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      }
    },
    [formatMessage],
  );

  const fetchMeetings = useCallback(() => loadMeetings(true), [loadMeetings]);

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

  // Poll while work is active.  When WebSocket setup fails, continue a
  // lightweight full-list poll so a newly-created processing task can be
  // discovered instead of permanently stopping the fallback.
  useEffect(() => {
    pollRef.current = setInterval(() => {
      const hasProcessing = meetingsRef.current.some(
        (m) => m.status === "processing" || m.status === "uploading" || m.status === "summarizing",
      );
      if (hasProcessing || wsUrl === null) {
        void loadMeetings(false);
      }
    }, pollInterval);

    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [loadMeetings, pollInterval, wsUrl]);

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
          title: formatMessage(
            { id: ids.length === 1 ? "materials.deleteTitle.one" : "materials.deleteTitle.many" },
            { count: ids.length },
          ),
          content: formatMessage({ id: "materials.deleteConfirm" }, { count: ids.length }),
          okType: "danger",
          okText: formatMessage({ id: "common.delete" }),
          onOk: async () => {
            try {
              await Promise.all(ids.map((id) => deleteMeeting(id)));
              message.success(formatMessage({ id: "materials.deleted" }, { count: ids.length }));
              await fetchMeetings();
              resolve(true);
            } catch (err) {
              message.error(
                formatApiErrorMessage(err, formatMessage({ id: "materials.deleteFailed" })),
              );
              resolve(false);
            }
          },
          onCancel: () => resolve(false),
        });
      });
    },
    [fetchMeetings, formatMessage],
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
