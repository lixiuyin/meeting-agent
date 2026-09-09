import { Button, Modal } from "antd";
import { CopyOutlined, LinkOutlined } from "@ant-design/icons";
import { CitationSourceView } from "./CitationSourceView";
import { formatContentType, formatSourceLocation, isImageDerivedSource } from "./sourceHelpers";
import { canOpenSource } from "./sourceLinks";
import type { CitationSelection } from "./AgentMetaPanels";

interface Props {
  citation: CitationSelection | null;
  onClose: () => void;
  onOpenSource: (source: CitationSelection["source"]) => void;
  onCopySourceSnippet: (text: string) => void;
}

export function CitationModal({ citation, onClose, onOpenSource, onCopySourceSnippet }: Props) {
  return (
    <Modal
      open={citation !== null}
      title={
        citation
          ? `Source [${citation.index}] ${citation.source.file_name ?? citation.source.meeting_title}`
          : ""
      }
      onCancel={onClose}
      footer={null}
      width={560}
      destroyOnHidden
    >
      {citation && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={{ fontSize: 12, color: "var(--color-text-muted)" }}>
            {formatSourceLocation(citation.source) ?? "No location metadata"}
          </div>
          <div style={{ fontSize: 12, color: "var(--color-text-muted)" }}>
            Type: {formatContentType(citation.source)}
          </div>
          <div style={{ fontSize: 12, color: "var(--color-text-muted)" }}>
            Retrieval score: {citation.source.score.toFixed(3)} (not a probability)
          </div>
          {!!citation.source.alternate_sources?.length && (
            <div aria-label="Additional evidence sources" style={{ fontSize: 12 }}>
              Also found in:{" "}
              {citation.source.alternate_sources
                .map((item) => String(item.file_name ?? `File ${item.file_id ?? "unknown"}`))
                .join(" · ")}
            </div>
          )}
          <div
            style={{
              padding: "10px 12px",
              borderRadius: 8,
              background: "var(--color-bg-muted)",
              border: "1px solid var(--color-border)",
              maxHeight: 400,
              overflow: "auto",
            }}
          >
            <CitationSourceView source={citation.source} />
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            {canOpenSource(citation.source) && (
              <Button
                type="primary"
                icon={<LinkOutlined />}
                onClick={() => onOpenSource(citation.source)}
              >
                {citation.source.source_kind === "file_summary"
                  ? "View file summary"
                  : citation.source.file_type === "audio" || citation.source.file_type === "video"
                    ? "Play recording"
                    : isImageDerivedSource(citation.source)
                      ? "View source image"
                      : "View source file"}
              </Button>
            )}
            <Button
              icon={<CopyOutlined />}
              onClick={() => onCopySourceSnippet((citation.source.content || "").trim())}
            >
              Copy original snippet
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}
