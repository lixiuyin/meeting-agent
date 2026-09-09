import { useEffect, useState, useCallback, useRef } from "react";
import { Spin } from "antd";
import { ClockCircleOutlined } from "@ant-design/icons";
import { getFileTimeline } from "../../../api/client";
import { api } from "../../../api/client-core";
import { reportNonCriticalError } from "../../../utils/monitoring";

// ---- Shared types ----

export interface TimestampSegment {
  start: number;
  end: number;
  text: string;
  speaker?: string | null;
}

// eslint-disable-next-line react-refresh/only-export-components
export function formatTime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}:${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

// ---- Audio viewer with timestamps sidebar ----

interface AudioViewerProps {
  url: string;
  fileName: string;
  seekTo?: number;
  seekEnd?: number;
  meetingId: number;
  fileId: number;
  audioRef: React.RefObject<HTMLAudioElement | null>;
}

export function AudioViewer({
  url,
  fileName,
  seekTo,
  seekEnd,
  meetingId,
  fileId,
  audioRef,
}: AudioViewerProps) {
  return (
    <AudioViewerInner
      key={`${meetingId}:${fileId}`}
      url={url}
      fileName={fileName}
      seekTo={seekTo}
      seekEnd={seekEnd}
      meetingId={meetingId}
      fileId={fileId}
      audioRef={audioRef}
    />
  );
}

function AudioViewerInner({
  url,
  fileName,
  seekTo,
  seekEnd,
  meetingId,
  fileId,
  audioRef,
}: AudioViewerProps) {
  const [timestamps, setTimestamps] = useState<TimestampSegment[]>([]);
  const [loadingTs, setLoadingTs] = useState(true);
  const [currentTime, setCurrentTime] = useState(seekTo ?? 0);

  useEffect(() => {
    let cancelled = false;
    getFileTimeline(meetingId, fileId)
      .then((res) => {
        if (!cancelled && res.data?.kind === "segments") setTimestamps(res.data.segments);
      })
      .catch((err) => {
        reportNonCriticalError("Failed to load audio timeline", err);
      })
      .finally(() => {
        if (!cancelled) setLoadingTs(false);
      });
    return () => {
      cancelled = true;
    };
  }, [meetingId, fileId]);

  const handleTimeUpdate = useCallback(() => {
    const current = audioRef.current?.currentTime ?? 0;
    setCurrentTime(current);
    if (seekEnd != null && current >= seekEnd) audioRef.current?.pause();
  }, [audioRef, seekEnd]);

  const handleSeek = useCallback(
    (time: number) => {
      if (audioRef.current) {
        audioRef.current.currentTime = time;
        audioRef.current.play().catch(() => {});
      }
    },
    [audioRef],
  );

  const hasTimestamps = timestamps.length > 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* Player bar */}
      <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--color-border)" }}>
        <audio
          ref={audioRef}
          src={url}
          controls
          aria-label="Audio timeline player"
          autoPlay={seekTo != null}
          onTimeUpdate={handleTimeUpdate}
          style={{ width: "100%" }}
        >
          <track kind="captions" />
        </audio>
        <div style={{ marginTop: 8, fontSize: 12, color: "var(--color-text-muted)" }}>
          {fileName}
          {seekTo != null && <span> — starting at {formatTime(seekTo)}</span>}
        </div>
      </div>

      {/* Timestamps / transcript */}
      <div style={{ flex: 1, overflowY: "auto", padding: "8px 12px" }}>
        {loadingTs ? (
          <div style={{ textAlign: "center", padding: 20 }}>
            <Spin size="small" />
          </div>
        ) : hasTimestamps ? (
          timestamps.map((seg) => {
            const active = currentTime >= seg.start && currentTime < seg.end;
            return (
              <div
                key={`${seg.start}-${seg.end}-${seg.speaker ?? ""}`}
                onClick={() => handleSeek(seg.start)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    handleSeek(seg.start);
                  }
                }}
                role="button"
                tabIndex={0}
                style={{
                  display: "flex",
                  gap: 10,
                  padding: "8px 10px",
                  borderRadius: 8,
                  cursor: "pointer",
                  marginBottom: 4,
                  background: active ? "var(--color-primary-alpha)" : "transparent",
                  borderLeft: active ? "3px solid var(--color-primary)" : "3px solid transparent",
                  transition: "all 0.15s ease",
                }}
              >
                <span
                  style={{
                    fontSize: 11,
                    color: active ? "var(--color-primary)" : "var(--color-text-muted)",
                    whiteSpace: "nowrap",
                    paddingTop: 1,
                    minWidth: 52,
                  }}
                >
                  <ClockCircleOutlined style={{ marginRight: 4 }} />
                  {formatTime(seg.start)}
                </span>
                <span
                  style={{
                    fontSize: 12,
                    color: active ? "var(--color-text-primary)" : "var(--color-text-secondary)",
                    lineHeight: 1.5,
                  }}
                >
                  {seg.text}
                </span>
              </div>
            );
          })
        ) : (
          <div
            style={{
              textAlign: "center",
              padding: 20,
              color: "var(--color-text-muted)",
              fontSize: 12,
            }}
          >
            No transcript timestamps available for this file.
          </div>
        )}
      </div>
    </div>
  );
}

// ---- Video viewer with timestamps sidebar ----

interface VideoViewerProps {
  url: string;
  seekTo?: number;
  seekEnd?: number;
  meetingId: number;
  fileId: number;
  videoRef: React.RefObject<HTMLVideoElement | null>;
}

export function VideoViewer({
  url,
  seekTo,
  seekEnd,
  meetingId,
  fileId,
  videoRef,
}: VideoViewerProps) {
  return (
    <VideoViewerInner
      key={`${meetingId}:${fileId}`}
      url={url}
      seekTo={seekTo}
      seekEnd={seekEnd}
      meetingId={meetingId}
      fileId={fileId}
      videoRef={videoRef}
    />
  );
}

function VideoViewerInner({ url, seekTo, seekEnd, meetingId, fileId, videoRef }: VideoViewerProps) {
  const [timestamps, setTimestamps] = useState<TimestampSegment[]>([]);
  const [currentTime, setCurrentTime] = useState(seekTo ?? 0);

  useEffect(() => {
    let cancelled = false;
    getFileTimeline(meetingId, fileId)
      .then((res) => {
        if (!cancelled && res.data?.kind === "segments") setTimestamps(res.data.segments);
      })
      .catch((err) => {
        reportNonCriticalError("Failed to load video timeline", err);
      });
    return () => {
      cancelled = true;
    };
  }, [meetingId, fileId]);

  const handleTimeUpdate = useCallback(() => {
    const current = videoRef.current?.currentTime ?? 0;
    setCurrentTime(current);
    if (seekEnd != null && current >= seekEnd) videoRef.current?.pause();
  }, [seekEnd, videoRef]);

  const handleSeek = useCallback(
    (time: number) => {
      if (videoRef.current) {
        videoRef.current.currentTime = time;
        videoRef.current.play().catch(() => {});
      }
    },
    [videoRef],
  );

  const hasTimestamps = timestamps.length > 0;

  return (
    <div style={{ display: "flex", height: "100%", gap: 0 }}>
      {/* Video */}
      <div
        style={{
          flex: 1,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: 12,
        }}
      >
        <video
          ref={videoRef}
          src={url}
          controls
          aria-label="Video timeline player"
          autoPlay={seekTo != null}
          onTimeUpdate={handleTimeUpdate}
          style={{ width: "100%", maxHeight: "70vh", borderRadius: 8 }}
        >
          <track kind="captions" />
        </video>
      </div>

      {/* Timestamps sidebar */}
      {hasTimestamps && (
        <div
          style={{
            width: 280,
            borderLeft: "1px solid var(--color-border)",
            overflowY: "auto",
            padding: "8px 10px",
          }}
        >
          <div
            style={{
              fontSize: 12,
              fontWeight: 600,
              padding: "4px 10px 8px",
              color: "var(--color-text-secondary)",
            }}
          >
            Transcript
          </div>
          {timestamps.map((seg) => {
            const active = currentTime >= seg.start && currentTime < seg.end;
            return (
              <div
                key={`${seg.start}-${seg.end}-${seg.speaker ?? ""}`}
                onClick={() => handleSeek(seg.start)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    handleSeek(seg.start);
                  }
                }}
                role="button"
                tabIndex={0}
                style={{
                  display: "flex",
                  gap: 8,
                  padding: "6px 10px",
                  borderRadius: 6,
                  cursor: "pointer",
                  marginBottom: 2,
                  background: active ? "var(--color-primary-alpha)" : "transparent",
                  borderLeft: active ? "3px solid var(--color-primary)" : "3px solid transparent",
                  transition: "all 0.15s ease",
                }}
              >
                <span
                  style={{
                    fontSize: 10,
                    color: active ? "var(--color-primary)" : "var(--color-text-muted)",
                    whiteSpace: "nowrap",
                    paddingTop: 1,
                    minWidth: 44,
                  }}
                >
                  {formatTime(seg.start)}
                </span>
                <span
                  style={{
                    fontSize: 11,
                    color: active ? "var(--color-text-primary)" : "var(--color-text-secondary)",
                    lineHeight: 1.4,
                  }}
                >
                  {seg.text}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ---- Text file preview ----

interface TextPreviewProps {
  url: string;
  fileName: string;
  windowStart?: number;
  windowEnd?: number;
}

export function TextPreview(props: TextPreviewProps) {
  return <TextPreviewInner key={props.url} {...props} />;
}

function TextPreviewInner({ url, fileName, windowStart, windowEnd }: TextPreviewProps) {
  const [loading, setLoading] = useState(true);
  const [text, setText] = useState("");
  const [loadError, setLoadError] = useState<string | null>(null);
  const evidenceRef = useRef<HTMLElement>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .get(url, { responseType: "text" })
      .then((resp) => {
        if (!cancelled) setText(resp.data);
      })
      .catch((err) => {
        if (!cancelled) {
          reportNonCriticalError("Failed to load text preview", err);
          setText("");
          setLoadError("Failed to load text preview");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [url]);

  useEffect(() => {
    evidenceRef.current?.scrollIntoView({ block: "center" });
  }, [text, windowEnd, windowStart]);

  if (loading) {
    return (
      <div style={{ textAlign: "center", padding: 32 }}>
        <Spin size="small" />
      </div>
    );
  }

  if (loadError) {
    return (
      <div style={{ textAlign: "center", padding: 24, color: "var(--color-error)" }}>
        {loadError}
      </div>
    );
  }

  if (!text) {
    return (
      <div style={{ textAlign: "center", padding: 24, color: "var(--color-text-muted)" }}>
        No text preview available.
      </div>
    );
  }

  // Backend evidence offsets are Unicode code-point offsets. JavaScript slice
  // uses UTF-16 units, so split by code point before applying coordinates.
  const codePoints = Array.from(text);
  const start = Math.min(Math.max(windowStart ?? 0, 0), codePoints.length);
  const end = Math.min(Math.max(windowEnd ?? start, start), codePoints.length);
  const hasExactWindow = windowStart != null && windowEnd != null && end > start;

  return (
    <div style={{ padding: 16 }}>
      <div style={{ fontSize: 13, color: "var(--color-text-secondary)", marginBottom: 8 }}>
        {fileName}
      </div>
      <pre
        style={{
          margin: 0,
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          maxHeight: "70vh",
          overflow: "auto",
          borderRadius: 8,
          border: "1px solid var(--color-border)",
          background: "var(--color-bg-muted)",
          padding: 12,
          fontSize: 13,
          lineHeight: 1.6,
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
        }}
      >
        {hasExactWindow ? (
          <>
            {codePoints.slice(0, start).join("")}
            <mark ref={evidenceRef} data-testid="exact-evidence-window">
              {codePoints.slice(start, end).join("")}
            </mark>
            {codePoints.slice(end).join("")}
          </>
        ) : (
          text
        )}
      </pre>
    </div>
  );
}
