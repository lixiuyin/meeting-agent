import { useCallback, useEffect, useMemo } from "react";
import { Modal, Spin, Button, Tag, Tooltip } from "antd";
import {
  CopyOutlined,
  DownloadOutlined,
  ReloadOutlined,
  FileTextOutlined,
} from "@ant-design/icons";
import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import { remarkPlugins, rehypePlugins, normalizeLatexMathDelimiters } from "../../utils/markdown";

interface SummaryFileRef {
  id: number;
  file_name: string;
}

interface SummaryModalProps {
  open: boolean;
  loading: boolean;
  data: string | null;
  streaming: boolean;
  files: SummaryFileRef[];
  onCopy: () => void;
  onDownload: () => void;
  onClose: () => void;
  onRegenerate: () => void;
  onNavigateToFile?: (fileId: number) => void;
  title?: string;
  focusFileId?: number | null;
}

/**
 * Rewrite ``[file:123]`` and ``file:123`` tokens into Markdown links so
 * ReactMarkdown renders them as clickable elements.  We use a custom protocol
 * ``x-file://`` that our link renderer can detect without conflicting with
 * real URLs.
 */
function rewriteFileCitations(markdown: string): string {
  // First normalize bare "file:N" (without brackets) into [file:N]
  const normalized = markdown.replace(/(?<!\[)\bfile:\s*(\d+)\b(?!\])/g, "[file:$1]");
  // Then rewrite bracketed [file:N] into markdown links
  return normalized.replace(
    /\[file:(\d+)\]/g,
    (_match: string, id: string) => `[file:${id}](x-file://${id})`,
  );
}

export default function SummaryModal({
  open,
  loading,
  data,
  streaming,
  files,
  onCopy,
  onDownload,
  onClose,
  onRegenerate,
  onNavigateToFile,
  title,
  focusFileId,
}: SummaryModalProps) {
  const fileNames = useMemo(() => {
    const map = new Map<number, string>();
    for (const f of files) {
      map.set(f.id, f.file_name);
    }
    return map;
  }, [files]);

  const processedMarkdown = useMemo(() => {
    if (!data) return "";
    return rewriteFileCitations(normalizeLatexMathDelimiters(data));
  }, [data]);

  const handleLinkClick = useCallback(
    (href: string) => {
      if (href.startsWith("x-file://") && onNavigateToFile) {
        const fileId = parseInt(href.slice("x-file://".length), 10);
        if (!isNaN(fileId)) {
          onNavigateToFile(fileId);
          return true;
        }
      }
      return false;
    },
    [onNavigateToFile],
  );

  const markdownComponents: Components = useMemo(
    () => ({
      a({ href, children, ...props }) {
        if (href?.startsWith("x-file://")) {
          const fileId = parseInt(href.slice("x-file://".length), 10);
          const fileName = fileNames.get(fileId) ?? `file:${fileId}`;
          return (
            <Tooltip title={`Open "${fileName}" in Materials`}>
              <Tag
                color={focusFileId === fileId ? "magenta" : "blue"}
                style={{ cursor: "pointer", marginRight: 2 }}
                data-summary-file-id={fileId}
                onClick={(e) => {
                  e.preventDefault();
                  handleLinkClick(href);
                }}
              >
                <FileTextOutlined style={{ marginRight: 3 }} />
                {fileName}
              </Tag>
            </Tooltip>
          );
        }
        return (
          <a href={href} target="_blank" rel="noopener noreferrer" {...props}>
            {children}
          </a>
        );
      },
    }),
    [focusFileId, handleLinkClick, fileNames],
  );

  useEffect(() => {
    if (!open || focusFileId == null) return;
    const handle = window.requestAnimationFrame(() => {
      const el = document.querySelector<HTMLElement>(`[data-summary-file-id="${focusFileId}"]`);
      el?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
    return () => window.cancelAnimationFrame(handle);
  }, [focusFileId, open, processedMarkdown]);

  return (
    <Modal
      title={title ?? "Meeting Summary"}
      open={open}
      onCancel={onClose}
      footer={null}
      centered
      width="min(96vw, 900px)"
      styles={{ body: { maxHeight: "calc(100vh - 180px)", overflowY: "auto" } }}
      zIndex={1210}
    >
      {loading && !data ? (
        <div style={{ textAlign: "center", padding: 40 }}>
          <Spin />
        </div>
      ) : data ? (
        <div style={{ maxHeight: "calc(100vh - 320px)", overflow: "auto" }}>
          {/* Toolbar */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              marginBottom: 12,
              flexWrap: "wrap",
              gap: 8,
            }}
          >
            <div style={{ fontSize: 12, color: "var(--color-text-muted)" }}>
              {streaming ? "Streaming summary..." : "Summary ready"}
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <Button
                size="small"
                icon={<ReloadOutlined />}
                onClick={onRegenerate}
                loading={loading}
                disabled={loading || streaming}
              >
                Regenerate
              </Button>
              <Button size="small" icon={<CopyOutlined />} onClick={() => void onCopy()}>
                Copy
              </Button>
              <Button size="small" icon={<DownloadOutlined />} onClick={onDownload}>
                Download
              </Button>
            </div>
          </div>

          {/* Summary body */}
          <div
            className="markdown-body"
            style={{
              fontSize: 14,
              lineHeight: 1.75,
              color: "var(--color-text-primary)",
              background: "var(--color-bg-muted)",
              border: "1px solid var(--color-border)",
              borderRadius: 10,
              padding: 18,
            }}
          >
            <ReactMarkdown
              remarkPlugins={remarkPlugins}
              rehypePlugins={rehypePlugins}
              components={markdownComponents}
            >
              {processedMarkdown}
            </ReactMarkdown>
          </div>
        </div>
      ) : (
        <div style={{ textAlign: "center", padding: 24, color: "var(--color-text-muted)" }}>
          <p>No cached summary available.</p>
          <Button size="small" icon={<ReloadOutlined />} onClick={onRegenerate} loading={loading}>
            Generate
          </Button>
        </div>
      )}
    </Modal>
  );
}
