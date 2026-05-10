import { Tag } from "antd";
import ReactMarkdown from "react-markdown";
import { remarkPlugins, rehypePlugins, normalizeLatexMathDelimiters } from "../../utils/markdown";
import type { TimestampSegment } from "../../hooks/useMeetingDetail";

function formatTime(seconds: number) {
  return `${Math.floor(seconds / 60)
    .toString()
    .padStart(2, "0")}:${Math.floor(seconds % 60)
    .toString()
    .padStart(2, "0")}`;
}

interface TranscriptSegmentItemProps {
  segment: TimestampSegment;
  index: number;
  isActive: boolean;
  isUnnamedSpeaker: (speaker?: string | null) => boolean;
  onSeek: (time: number) => void;
  onActiveSegmentChange: (index: number | null) => void;
}

export default function TranscriptSegmentItem({
  segment: seg,
  index: idx,
  isActive,
  isUnnamedSpeaker,
  onSeek,
  onActiveSegmentChange,
}: TranscriptSegmentItemProps) {
  return (
    <div
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
        background: isActive ? "rgba(79, 70, 229, 0.14)" : "var(--color-bg-muted)",
        border: isActive ? "1px solid var(--color-primary)" : "1px solid transparent",
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
        {"speaker" in seg && seg.speaker && (
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
      <div className="markdown-body" style={{ fontSize: 13, color: "var(--color-text-primary)" }}>
        <ReactMarkdown remarkPlugins={remarkPlugins} rehypePlugins={rehypePlugins}>
          {normalizeLatexMathDelimiters(seg.text)}
        </ReactMarkdown>
      </div>
    </div>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export { formatTime };
