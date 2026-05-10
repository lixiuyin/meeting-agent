import { useRef, useCallback } from "react";

// FE-6: Stream health timeouts. Backend heartbeat fires every 5 s, so a
// 45 s stall window gives 9 heartbeat opportunities before triggering.
// Override via VITE_STREAM_STALL_MS / VITE_STREAM_HEARTBEAT_MS env vars.
const STALL_TIMEOUT = Number(import.meta.env.VITE_STREAM_STALL_MS) || 45_000;
const HEARTBEAT_TIMEOUT = Number(import.meta.env.VITE_STREAM_HEARTBEAT_MS) || 45_000;
// H-FE-2: Absolute upper bound on stream duration (180s). Even if heartbeats
// keep resetting the stall timer, the stream is force-aborted after this limit.
const ABSOLUTE_TIMEOUT = Number(import.meta.env.VITE_STREAM_ABSOLUTE_MS) || 180_000;

export interface StreamTimerActions {
  clearTimers: () => void;
  clearNoticeTimer: () => void;
  scheduleStallCheck: (onStall: () => void) => void;
  scheduleConnectionCheck: (onDead: () => void) => void;
  scheduleAbsoluteTimeout: (onTimeout: () => void) => void;
  scheduleNoticeClear: (onClear: () => void, delay?: number) => void;
  touchActivity: () => void;
  lastActivityRef: React.MutableRefObject<number>;
}

export function useStreamTimers(): StreamTimerActions {
  const noticeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const stallTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const connectionTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const absoluteTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastActivityRef = useRef<number>(0);

  const clearTimers = useCallback(() => {
    if (stallTimerRef.current) {
      clearTimeout(stallTimerRef.current);
      stallTimerRef.current = null;
    }
    if (connectionTimerRef.current) {
      clearTimeout(connectionTimerRef.current);
      connectionTimerRef.current = null;
    }
    if (absoluteTimerRef.current) {
      clearTimeout(absoluteTimerRef.current);
      absoluteTimerRef.current = null;
    }
  }, []);

  const clearNoticeTimer = useCallback(() => {
    if (noticeTimerRef.current) {
      clearTimeout(noticeTimerRef.current);
      noticeTimerRef.current = null;
    }
  }, []);

  const scheduleStallCheck = useCallback((onStall: () => void) => {
    if (stallTimerRef.current) {
      clearTimeout(stallTimerRef.current);
    }
    stallTimerRef.current = setTimeout(() => {
      stallTimerRef.current = null;
      onStall();
    }, STALL_TIMEOUT);
  }, []);

  const scheduleConnectionCheck = useCallback((onDead: () => void) => {
    if (connectionTimerRef.current) {
      clearTimeout(connectionTimerRef.current);
    }
    connectionTimerRef.current = setTimeout(() => {
      connectionTimerRef.current = null;
      onDead();
    }, HEARTBEAT_TIMEOUT);
  }, []);

  const scheduleNoticeClear = useCallback((onClear: () => void, delay = 2000) => {
    if (noticeTimerRef.current) {
      clearTimeout(noticeTimerRef.current);
    }
    noticeTimerRef.current = setTimeout(() => {
      noticeTimerRef.current = null;
      onClear();
    }, delay);
  }, []);

  const scheduleAbsoluteTimeout = useCallback((onTimeout: () => void) => {
    // H-FE-2: Absolute timeout is set once and never reset by heartbeats.
    if (absoluteTimerRef.current) {
      clearTimeout(absoluteTimerRef.current);
    }
    absoluteTimerRef.current = setTimeout(() => {
      absoluteTimerRef.current = null;
      onTimeout();
    }, ABSOLUTE_TIMEOUT);
  }, []);

  const touchActivity = useCallback(() => {
    lastActivityRef.current = Date.now();
  }, []);

  return {
    clearTimers,
    clearNoticeTimer,
    scheduleStallCheck,
    scheduleConnectionCheck,
    scheduleAbsoluteTimeout,
    scheduleNoticeClear,
    touchActivity,
    lastActivityRef,
  };
}
