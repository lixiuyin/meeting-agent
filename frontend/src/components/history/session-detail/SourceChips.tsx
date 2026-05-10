import { useMemo } from "react";
import {
  BookOutlined,
  FileImageOutlined,
  FilePdfOutlined,
  FileTextOutlined,
  VideoCameraOutlined,
  AudioOutlined,
  CopyOutlined,
  LinkOutlined,
} from "@ant-design/icons";
import { Button, Popover, Tag, Tooltip } from "antd";
import { useViewer } from "../../../contexts/ViewerContext";
import type { SourceItem } from "../../../api/client";
import {
  canOpenSource,
  formatSourceLocation,
  isImageDerivedSource,
  openSourceFromCitation,
  sourceKeyFor,
  sourcePreviewImageUrl,
} from "./sourceHelpers";
import { SourcePreviewContent } from "./SourcePreviewContent";

const SOURCE_TYPE_ICON: Record<string, React.ReactNode> = {
  video: <VideoCameraOutlined />,
  audio: <AudioOutlined />,
  pdf: <FilePdfOutlined />,
  image: <FileImageOutlined />,
};

function SourceChip({
  source,
  index,
  sourceKey,
  open,
  isFlashing,
  onOpenChange,
}: {
  source: SourceItem;
  index: number;
  sourceKey: string;
  open: boolean;
  isFlashing: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { openViewer } = useViewer();
  const icon = SOURCE_TYPE_ICON[source.file_type ?? ""] ?? <FileTextOutlined />;
  const canOpen = canOpenSource(source);
  const imagePreviewUrl = sourcePreviewImageUrl(source);
  const location = formatSourceLocation(source);

  return (
    <Popover
      content={
        <div style={{ maxWidth: 360, fontSize: 12 }}>
          <div style={{ fontWeight: 600, marginBottom: 6 }}>
            [{index}] {source.meeting_title}
          </div>
          {location && (
            <div style={{ color: "var(--color-primary)", marginBottom: 6 }}>{location}</div>
          )}
          {source.speaker && (
            <div style={{ color: "var(--color-text-secondary)", marginBottom: 6 }}>
              Speaker: {source.speaker}
            </div>
          )}
          <div
            style={{
              maxHeight: 120,
              overflowY: "auto",
              padding: "8px 10px",
              background: "var(--color-bg-muted)",
              borderRadius: 8,
              lineHeight: 1.5,
              color: "var(--color-text-secondary)",
              marginBottom: 8,
              whiteSpace: "pre-wrap",
            }}
          >
            {imagePreviewUrl && (
              <img
                src={imagePreviewUrl}
                alt="Source preview"
                style={{
                  maxWidth: "100%",
                  maxHeight: isImageDerivedSource(source) ? 220 : 120,
                  objectFit: "contain",
                  borderRadius: 6,
                  border: "1px solid var(--color-border)",
                  marginBottom: 6,
                }}
              />
            )}
            <SourcePreviewContent source={source} />
          </div>
          <div style={{ display: "flex", gap: 4 }}>
            {canOpen && (
              <Tooltip title="Open source">
                <Button
                  size="small"
                  type="text"
                  icon={<LinkOutlined />}
                  aria-label="Open source"
                  onClick={() => openSourceFromCitation(openViewer, source)}
                />
              </Tooltip>
            )}
            <Tooltip title="Copy snippet">
              <Button
                size="small"
                type="text"
                icon={<CopyOutlined />}
                aria-label="Copy snippet"
                onClick={() => {
                  navigator.clipboard.writeText(source.content).catch(() => {});
                }}
              />
            </Tooltip>
          </div>
        </div>
      }
      trigger="click"
      placement="topLeft"
      open={open}
      onOpenChange={onOpenChange}
    >
      <Tag
        style={{
          borderRadius: 20,
          margin: 0,
          cursor: canOpen ? "pointer" : "default",
          display: "inline-flex",
          alignItems: "center",
          gap: 4,
          fontSize: 11,
          borderColor: isFlashing ? "var(--color-primary)" : undefined,
          boxShadow: isFlashing ? "0 0 0 2px rgba(79, 70, 229, 0.2)" : undefined,
          background: isFlashing ? "rgba(79, 70, 229, 0.12)" : undefined,
          transition: "all 0.2s ease",
        }}
        data-source-key={sourceKey}
      >
        {icon} <sup>{index}</sup> {source.meeting_title}
      </Tag>
    </Popover>
  );
}

interface Props {
  sources: SourceItem[];
  openSourcePopoverKey: string | null;
  onOpenSourcePopoverChange: (key: string | null) => void;
  flashSourceKey: string | null;
}

export function SourceChips({
  sources,
  openSourcePopoverKey,
  onOpenSourcePopoverChange,
  flashSourceKey,
}: Props) {
  const display = useMemo(() => sources.slice(0, 5), [sources]);
  return (
    <div
      style={{
        marginTop: 10,
        paddingTop: 8,
        borderTop: "1px solid var(--color-border)",
        display: "flex",
        flexWrap: "wrap",
        gap: 6,
      }}
    >
      <span style={{ fontSize: 11, color: "var(--color-text-muted)", marginRight: 2 }}>
        <BookOutlined /> Sources ({display.length}
        {display.length < sources.length ? ` / ${sources.length}` : ""}):
      </span>
      {display.map((s, i) => {
        const key = sourceKeyFor(s, i + 1);
        return (
          <SourceChip
            key={key}
            source={s}
            index={i + 1}
            sourceKey={key}
            open={openSourcePopoverKey === key}
            isFlashing={flashSourceKey === key}
            onOpenChange={(open) => onOpenSourcePopoverChange(open ? key : null)}
          />
        );
      })}
    </div>
  );
}
