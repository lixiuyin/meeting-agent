import { Button, Modal } from "antd";
import { CopyOutlined, LinkOutlined } from "@ant-design/icons";
import { useViewer } from "../../../contexts/ViewerContext";
import type { SourceItem } from "../../../api/client";
import {
  canOpenSource,
  formatSourceLocation,
  isImageDerivedSource,
  openSourceFromCitation,
  sourcePreviewImageUrl,
} from "./sourceHelpers";
import { SourcePreviewContent } from "./SourcePreviewContent";

interface Props {
  selectedCitation: { index: number; source: SourceItem } | null;
  onClose: () => void;
}

export function SourceDetailModal({ selectedCitation, onClose }: Props) {
  const { openViewer } = useViewer();
  return (
    <Modal
      open={selectedCitation !== null}
      title={
        selectedCitation
          ? `Source [${selectedCitation.index}] ${selectedCitation.source.file_name ?? selectedCitation.source.meeting_title}`
          : ""
      }
      onCancel={onClose}
      footer={null}
      width={560}
      destroyOnHidden
    >
      {selectedCitation && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={{ fontSize: 12, color: "var(--color-text-muted)" }}>
            {formatSourceLocation(selectedCitation.source) ?? "No location metadata"}
          </div>
          <div style={{ fontSize: 12, color: "var(--color-text-muted)" }}>
            Score: {(selectedCitation.source.score * 100).toFixed(0)}%
          </div>
          <div
            style={{
              fontSize: 13,
              lineHeight: 1.6,
              color: "var(--color-text-primary)",
              padding: "10px 12px",
              borderRadius: 8,
              background: "var(--color-bg-muted)",
              border: "1px solid var(--color-border)",
              maxHeight: 280,
              overflowY: "auto",
              whiteSpace: "pre-wrap",
            }}
          >
            {sourcePreviewImageUrl(selectedCitation.source) && (
              <img
                src={sourcePreviewImageUrl(selectedCitation.source)!}
                alt="Source preview"
                style={{
                  maxWidth: "100%",
                  maxHeight: isImageDerivedSource(selectedCitation.source) ? 320 : 160,
                  objectFit: "contain",
                  borderRadius: 8,
                  border: "1px solid var(--color-border)",
                  marginBottom: 8,
                }}
              />
            )}
            <SourcePreviewContent source={selectedCitation.source} />
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            {canOpenSource(selectedCitation.source) && (
              <Button
                type="primary"
                icon={<LinkOutlined />}
                onClick={() => openSourceFromCitation(openViewer, selectedCitation.source)}
              >
                {selectedCitation.source.file_type === "audio" ||
                selectedCitation.source.file_type === "video"
                  ? "Play recording"
                  : isImageDerivedSource(selectedCitation.source)
                    ? "View source image"
                    : "View source file"}
              </Button>
            )}
            <Button
              icon={<CopyOutlined />}
              onClick={() => {
                navigator.clipboard
                  .writeText((selectedCitation.source.content || "").trim())
                  .catch(() => {});
              }}
            >
              Copy original snippet
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}
