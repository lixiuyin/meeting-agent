import { useRef, useEffect, useCallback, useState } from "react";
import { getMeetingFileUrl, prefetchMeetingFileUrl } from "../api/client";

interface Segment {
  start: number;
  end: number;
  text: string;
  speaker?: string | null;
}

interface Props {
  src: string;
  meetingId?: number;
  fileId?: number;
  mediaType?: "audio" | "video";
  videoMaxHeight?: number;
  seekTo?: number;
  seekEnd?: number;
  segments?: Segment[];
  onActiveSegmentChange?: (index: number | null) => void;
}

/** Time before signed URL expiry (5 min TTL) to trigger a refresh, in milliseconds. */
const URL_REFRESH_THRESHOLD_MS = 240_000;

/**
 * Module-level deduplication map for in-flight signed-URL refresh requests.
 * When multiple AudioPlayer instances need to refresh tokens for the same
 * meeting+file combination simultaneously, this ensures only one network
 * request is made and all callers share the resulting promise.
 */
const _inflightRefresh = new Map<string, Promise<void>>();

function dedupedRefresh(meetingId: number, fileId: number): Promise<void> {
  const key = `${meetingId}:${fileId}`;
  const existing = _inflightRefresh.get(key);
  if (existing) return existing;
  const promise = prefetchMeetingFileUrl(meetingId, fileId).finally(() => {
    _inflightRefresh.delete(key);
  });
  _inflightRefresh.set(key, promise);
  return promise;
}

export default function AudioPlayer({
  src,
  meetingId,
  fileId,
  mediaType = "audio",
  videoMaxHeight = 360,
  seekTo,
  seekEnd,
  segments,
  onActiveSegmentChange,
}: Props) {
  const mediaRef = useRef<HTMLMediaElement>(null);
  const activeIndexRef = useRef<number | null>(null);
  const rafRef = useRef<number>(0);
  const lastCheckRef = useRef<number>(0);
  const activeSegmentCallbackRef = useRef<Props["onActiveSegmentChange"]>(onActiveSegmentChange);
  const [refreshedSrc, setRefreshedSrc] = useState<{ baseSrc: string; value: string | null }>(
    () => ({
      baseSrc: src,
      value: null,
    }),
  );
  const currentSrc = refreshedSrc.baseSrc === src ? (refreshedSrc.value ?? src) : src;
  const urlLoadedAtRef = useRef<number>(0);
  const pendingRestoreRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    urlLoadedAtRef.current = Date.now();
  }, [currentSrc]);

  const refreshUrl = useCallback(async () => {
    if (meetingId == null || fileId == null) return;
    try {
      await dedupedRefresh(meetingId, fileId);
      const freshUrl = getMeetingFileUrl(meetingId, fileId);
      const media = mediaRef.current;
      if (!media || freshUrl === currentSrc) return;
      const wasPlaying = !media.paused;
      const resumeTime = media.currentTime;
      setRefreshedSrc({ baseSrc: src, value: freshUrl });
      // Remove any prior pending restore callback before adding a new one
      // to prevent multiple listeners from firing on rapid successive refreshes.
      if (pendingRestoreRef.current) {
        media.removeEventListener("loadedmetadata", pendingRestoreRef.current);
      }
      // Wait for the element to load the new source, then restore position
      const restorePlayback = () => {
        media.currentTime = resumeTime;
        if (wasPlaying) media.play().catch(() => {});
      };
      pendingRestoreRef.current = restorePlayback;
      media.addEventListener("loadedmetadata", restorePlayback, { once: true });
    } catch {
      // Non-fatal: playback continues with the existing URL until it expires
    }
  }, [meetingId, fileId, currentSrc, src]);

  // Proactive refresh: swap URL at the threshold mark before expiry
  useEffect(() => {
    if (meetingId == null || fileId == null) return;
    const interval = setInterval(() => {
      if (Date.now() - urlLoadedAtRef.current >= URL_REFRESH_THRESHOLD_MS) {
        void refreshUrl();
      }
    }, 30_000);
    return () => clearInterval(interval);
  }, [meetingId, fileId, refreshUrl]);

  // Reactive refresh: on stalled/suspend events, attempt a URL refresh
  useEffect(() => {
    const media = mediaRef.current;
    if (!media || meetingId == null || fileId == null) return;
    const handleStalled = () => void refreshUrl();
    const handleSuspend = () => void refreshUrl();
    media.addEventListener("stalled", handleStalled);
    media.addEventListener("suspend", handleSuspend);
    return () => {
      media.removeEventListener("stalled", handleStalled);
      media.removeEventListener("suspend", handleSuspend);
    };
  }, [meetingId, fileId, refreshUrl]);

  // Seek on mount / seekTo change
  useEffect(() => {
    const media = mediaRef.current;
    if (!media || seekTo == null) return;

    const handleLoaded = () => {
      media.currentTime = seekTo;
      if (media.paused) {
        media.play().catch(() => {});
      }
    };

    if (media.readyState >= 1) {
      handleLoaded();
    } else {
      media.addEventListener("loadedmetadata", handleLoaded, { once: true });
    }
  }, [seekTo, currentSrc]);

  const stopAtEvidenceEnd = useCallback(() => {
    const media = mediaRef.current;
    if (media && seekEnd != null && media.currentTime >= seekEnd) {
      media.pause();
    }
  }, [seekEnd]);

  useEffect(() => {
    activeSegmentCallbackRef.current = onActiveSegmentChange;
  }, [onActiveSegmentChange]);

  // Poll current time for segment highlighting
  const tickRef = useRef<() => void>(() => {});
  const tick = useCallback(() => {
    const now = performance.now();
    const media = mediaRef.current;
    if (media && now - lastCheckRef.current >= 120) {
      lastCheckRef.current = now;
      const time = media.currentTime;
      let found: number | null = null;
      if (segments) {
        for (let i = 0; i < segments.length; i++) {
          const isWithinSegment =
            time >= segments[i].start &&
            (time < segments[i].end || (i === segments.length - 1 && time <= segments[i].end));
          if (isWithinSegment) {
            found = i;
            break;
          }
        }
      }
      if (activeIndexRef.current !== found) {
        activeIndexRef.current = found;
        activeSegmentCallbackRef.current?.(found);
      }
    }
    rafRef.current = requestAnimationFrame(() => tickRef.current?.());
  }, [segments]);

  useEffect(() => {
    tickRef.current = tick;
  }, [tick]);

  useEffect(() => {
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(rafRef.current);
    };
  }, [tick]);

  if (mediaType === "video") {
    return (
      <div style={{ position: "relative", width: "100%" }}>
        <video
          ref={(el) => {
            mediaRef.current = el;
          }}
          src={currentSrc}
          controls
          aria-label="Video player"
          playsInline
          onTimeUpdate={stopAtEvidenceEnd}
          style={{ width: "100%", maxHeight: videoMaxHeight, borderRadius: 8, background: "#000" }}
        >
          <track kind="captions" />
        </video>
      </div>
    );
  }

  return (
    <audio
      ref={(el) => {
        mediaRef.current = el;
      }}
      src={currentSrc}
      controls
      aria-label="Audio player"
      onTimeUpdate={stopAtEvidenceEnd}
      style={{ width: "100%" }}
    >
      <track kind="captions" />
    </audio>
  );
}

export type { Segment };
