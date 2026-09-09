import { Modal, Spin, Alert } from "antd";
import type { RefObject } from "react";
import { useEffect, useState } from "react";
import AudioPlayer from "../AudioPlayer";
import type { TimestampSegment, TimestampPlayback } from "../../hooks/useMeetingDetail";
import { useMainContentScrollLock } from "../../hooks/useMainContentScrollLock";
import { useMeetingFileUrl } from "../../hooks/useMeetingFileUrl";
import { AUDIO_VIEWER_MODAL, VIDEO_VIEWER_MODAL } from "./viewerModalPresets";
import TranscriptSegmentItem from "./TranscriptSegmentItem";

interface TranscriptViewerProps {
  open: boolean;
  loading: boolean;
  segments: TimestampSegment[];
  playback: TimestampPlayback | null;
  seekTo: number | undefined;
  seekEnd?: number;
  activeSegmentIndex: number | null;
  listRef: RefObject<HTMLDivElement | null>;
  isUnnamedSpeaker: (speaker?: string | null) => boolean;
  onSeek: (time: number) => void;
  onActiveSegmentChange: (index: number | null) => void;
  onClose: () => void;
}

export default function TranscriptViewer({
  open,
  loading,
  segments,
  playback,
  seekTo,
  seekEnd,
  activeSegmentIndex,
  listRef,
  isUnnamedSpeaker,
  onSeek,
  onActiveSegmentChange,
  onClose,
}: TranscriptViewerProps) {
  const [isCompactVideoLayout, setIsCompactVideoLayout] = useState(false);
  const mediaType =
    playback?.fileType === "video" ? "video" : playback?.fileType === "audio" ? "audio" : null;
  const isVideo = mediaType === "video";
  const isAudio = mediaType === "audio";
  const viewerPreset = isVideo ? VIDEO_VIEWER_MODAL : AUDIO_VIEWER_MODAL;
  const resolvedUrl = useMeetingFileUrl(playback?.meetingId ?? 0, playback?.fileId ?? 0);

  useMainContentScrollLock(open);

  useEffect(() => {
    if (activeSegmentIndex == null || !open) return;
    const container = listRef.current;
    if (!container) return;
    const el = container.querySelector(
      `[data-segment-index="${activeSegmentIndex}"]`,
    ) as HTMLElement | null;
    el?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [activeSegmentIndex, open, listRef]);

  useEffect(() => {
    if (!open) return;
    const checkLayout = () => setIsCompactVideoLayout(window.innerWidth < 1180);
    checkLayout();
    window.addEventListener("resize", checkLayout);
    return () => window.removeEventListener("resize", checkLayout);
  }, [open]);

  return (
    <Modal
      title="Transcript Timestamps"
      open={open}
      centered
      onCancel={() => {
        onClose();
      }}
      footer={null}
      width={viewerPreset.width}
      style={{ top: viewerPreset.top }}
      wrapClassName={viewerPreset.wrapClassName}
      styles={{
        body: {
          maxHeight: viewerPreset.bodyMaxHeight,
          overflowY: "auto",
          padding: viewerPreset.bodyPadding,
        },
      }}
      zIndex={1200}
      destroyOnHidden
    >
      {loading ? (
        <div style={{ textAlign: "center", padding: 40 }}>
          <Spin />
        </div>
      ) : segments.length === 0 ? (
        <div style={{ textAlign: "center", padding: 24, color: "var(--color-text-muted)" }}>
          No timestamps available.
        </div>
      ) : (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 12,
          }}
        >
          {playback && isAudio ? (
            <div
              style={{
                padding: 12,
                borderRadius: 8,
                background: "var(--color-bg-muted)",
                border: "1px solid var(--color-border)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <AudioPlayer
                src={resolvedUrl}
                meetingId={playback.meetingId}
                fileId={playback.fileId}
                mediaType="audio"
                seekTo={seekTo}
                seekEnd={seekEnd}
                segments={segments}
                onActiveSegmentChange={onActiveSegmentChange}
              />
            </div>
          ) : null}

          {playback && isVideo ? (
            <div
              style={{
                display: "grid",
                gridTemplateColumns: isCompactVideoLayout
                  ? "1fr"
                  : "minmax(0, 1.6fr) minmax(360px, 1fr)",
                gap: 12,
                alignItems: "start",
              }}
            >
              <div
                style={{
                  padding: 10,
                  borderRadius: 8,
                  background: "var(--color-bg-muted)",
                  border: "1px solid var(--color-border)",
                }}
              >
                <AudioPlayer
                  src={resolvedUrl}
                  meetingId={playback.meetingId}
                  fileId={playback.fileId}
                  mediaType="video"
                  videoMaxHeight={isCompactVideoLayout ? 460 : 620}
                  seekTo={seekTo}
                  seekEnd={seekEnd}
                  segments={segments}
                  onActiveSegmentChange={onActiveSegmentChange}
                />
              </div>
              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 8,
                  border: "1px solid var(--color-border)",
                  borderRadius: 8,
                  background: "var(--color-bg-elevated)",
                  overflow: "hidden",
                }}
              >
                <div
                  style={{
                    padding: "8px 12px",
                    borderBottom: "1px solid var(--color-border)",
                    background: "var(--color-bg-muted)",
                    fontSize: 12,
                    color: "var(--color-text-secondary)",
                    fontWeight: 600,
                  }}
                >
                  Timeline ({segments.length} segments)
                </div>
                <div
                  ref={listRef}
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    gap: 8,
                    maxHeight: isCompactVideoLayout ? "calc(100vh - 500px)" : "calc(100vh - 270px)",
                    overflow: "auto",
                    padding: 10,
                    paddingRight: 6,
                  }}
                >
                  {segments.map((seg, idx) => (
                    <TranscriptSegmentItem
                      key={`${seg.start}-${idx}`}
                      segment={seg}
                      index={idx}
                      isActive={activeSegmentIndex === idx}
                      isUnnamedSpeaker={isUnnamedSpeaker}
                      onSeek={onSeek}
                      onActiveSegmentChange={onActiveSegmentChange}
                    />
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <>
              {!playback ? (
                <Alert
                  type="warning"
                  showIcon
                  message="No playable audio/video file found for this meeting."
                />
              ) : null}
              <div
                ref={listRef}
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 8,
                  maxHeight: "calc(100vh - 300px)",
                  overflow: "auto",
                }}
              >
                {segments.map((seg, idx) => (
                  <TranscriptSegmentItem
                    key={idx}
                    segment={seg}
                    index={idx}
                    isActive={activeSegmentIndex === idx}
                    isUnnamedSpeaker={isUnnamedSpeaker}
                    onSeek={onSeek}
                    onActiveSegmentChange={onActiveSegmentChange}
                  />
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </Modal>
  );
}
