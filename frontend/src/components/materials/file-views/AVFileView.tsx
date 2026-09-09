/**
 * AVFileView — renders timestamped segments for audio/video files.
 * Renders an inline player plus a synchronized per-file transcript list.
 */
import { Spin, Tag } from "antd";
import AudioPlayer from "../../AudioPlayer";
import { useMeetingFileUrl } from "../../../hooks/useMeetingFileUrl";

interface Segment {
  start: number;
  end: number;
  text: string;
  speaker?: string | null;
}

interface AVFileViewProps {
  fileId: number;
  meetingId: number;
  fileType: string;
  segments: Segment[];
  loading?: boolean;
  activeSegmentIndex: number | null;
  seekTo: number | undefined;
  listRef: React.RefObject<HTMLDivElement | null>;
  isUnnamedSpeaker: (speaker?: string | null) => boolean;
  onSeek: (time: number) => void;
  onActiveSegmentChange: (index: number | null) => void;
}

const formatTime = (seconds: number) => {
  if (!Number.isFinite(seconds)) return "--:--";
  const m = Math.floor(seconds / 60)
    .toString()
    .padStart(2, "0");
  const s = Math.floor(seconds % 60)
    .toString()
    .padStart(2, "0");
  return `${m}:${s}`;
};

export default function AVFileView({
  fileId,
  meetingId,
  fileType,
  segments,
  loading,
  activeSegmentIndex,
  seekTo,
  listRef,
  isUnnamedSpeaker,
  onSeek,
  onActiveSegmentChange,
}: AVFileViewProps) {
  const mediaUrl = useMeetingFileUrl(meetingId, fileId);
  if (loading || !mediaUrl) {
    return (
      <div style={{ textAlign: "center", padding: 40 }}>
        <Spin />
      </div>
    );
  }

  if (segments.length === 0) {
    return (
      <div style={{ textAlign: "center", padding: 24, color: "var(--color-text-muted)" }}>
        No timestamps available.
      </div>
    );
  }

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 320px), 1fr))",
        gap: 12,
        alignItems: "start",
      }}
    >
      <div
        style={{
          alignSelf: "stretch",
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          minHeight: 360,
        }}
      >
        <AudioPlayer
          src={mediaUrl}
          meetingId={meetingId}
          fileId={fileId}
          mediaType={fileType === "video" ? "video" : "audio"}
          seekTo={seekTo}
          segments={segments}
          onActiveSegmentChange={onActiveSegmentChange}
        />
      </div>
      <div
        ref={listRef}
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 8,
          maxHeight: 520,
          overflow: "auto",
        }}
      >
        {segments.map((seg, idx) => (
          <div
            key={`${seg.start}-${seg.end}-${idx}`}
            data-segment-index={idx}
            role="button"
            tabIndex={0}
            onClick={() => {
              onSeek(seg.start);
              onActiveSegmentChange(idx);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onSeek(seg.start);
                onActiveSegmentChange(idx);
              }
            }}
            style={{
              padding: "10px 12px",
              borderRadius: 8,
              background:
                activeSegmentIndex === idx ? "rgba(79, 70, 229, 0.14)" : "var(--color-bg-muted)",
              border:
                activeSegmentIndex === idx
                  ? "1px solid var(--color-primary)"
                  : "1px solid transparent",
              cursor: "pointer",
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                marginBottom: 4,
                flexWrap: "wrap",
              }}
            >
              {seg.speaker && (
                <Tag
                  style={{
                    fontSize: 10,
                    borderRadius: 10,
                    padding: "0 8px",
                    lineHeight: "18px",
                    borderColor: "rgba(244, 63, 94, 0.35)",
                    background: "rgba(244, 63, 94, 0.12)",
                    color: "#be123c",
                  }}
                  color="red"
                >
                  {isUnnamedSpeaker(seg.speaker) ? `${seg.speaker} (unmapped)` : seg.speaker}
                </Tag>
              )}
              <span
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 5,
                  fontSize: 11,
                  fontWeight: 600,
                  color: "#0f766e",
                  background: "rgba(20, 184, 166, 0.14)",
                  border: "1px solid rgba(20, 184, 166, 0.35)",
                  borderRadius: 999,
                  padding: "2px 8px",
                }}
              >
                <span
                  style={{
                    width: 6,
                    height: 6,
                    borderRadius: "50%",
                    background: "#14b8a6",
                  }}
                />
                {formatTime(seg.start)}-{formatTime(seg.end)}
              </span>
            </div>
            <div style={{ fontSize: 13, color: "var(--color-text-primary)" }}>{seg.text}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
