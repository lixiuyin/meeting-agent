import {
  BookOutlined,
  CopyOutlined,
  DownOutlined,
  LinkOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import { Button, Collapse, Popover, Tag, Tooltip } from "antd";
import { useState } from "react";

const VISIBLE_SOURCE_COUNT = 6;

interface SourceChipsRowProps {
  msgKey: string;
  sources: SourceItem[];
  openSourcePopoverKey: string | null;
  onSetOpenSourcePopoverKey: (key: string | null) => void;
  onOpenSource: (source: SourceItem) => void;
  onCopySourceSnippet: (text: string) => void;
  onSelectCitation: (selection: CitationSelection) => void;
}

function SourceChipsRow({
  msgKey,
  sources,
  openSourcePopoverKey,
  onSetOpenSourcePopoverKey,
  onOpenSource,
  onCopySourceSnippet,
  onSelectCitation,
}: SourceChipsRowProps) {
  const [expanded, setExpanded] = useState(false);
  const visible = expanded ? sources : sources.slice(0, VISIBLE_SOURCE_COUNT);
  const hiddenCount = sources.length - VISIBLE_SOURCE_COUNT;

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 6,
        flexWrap: "wrap",
        paddingLeft: 4,
      }}
    >
      <BookOutlined
        style={{
          fontSize: 12,
          color: "var(--color-text-muted)",
        }}
      />
      <span style={{ fontSize: 12, color: "var(--color-text-muted)" }}>
        Sources ({sources.length}):
      </span>
      {visible.map((s) => {
        const globalIdx = sources.indexOf(s) + 1;
        const sourceKey = sourceKeyFor(msgKey, s);
        return (
          <Popover
            key={sourceKey}
            trigger="click"
            placement="bottom"
            zIndex={1030}
            getPopupContainer={(trigger) => trigger.parentElement ?? document.body}
            open={openSourcePopoverKey === sourceKey}
            onOpenChange={(open) => onSetOpenSourcePopoverKey(open ? sourceKey : null)}
            content={
              <div style={{ maxWidth: 280 }}>
                <div style={{ fontWeight: 600, marginBottom: 2, fontSize: 12 }}>
                  [{globalIdx}] {s.file_name ?? s.meeting_title}
                </div>
                <div
                  style={{
                    display: "flex",
                    gap: 6,
                    marginBottom: 4,
                    flexWrap: "wrap",
                    fontSize: 11,
                    color: "var(--color-text-muted)",
                  }}
                >
                  {formatSourceLocation(s) && <span>{formatSourceLocation(s)}</span>}
                  {s.speaker && s.source_kind !== "timestamp" && <span>· {s.speaker}</span>}
                  <span>· {formatContentType(s)}</span>
                  <span>· {(s.score * 100).toFixed(0)}%</span>
                </div>
                <div
                  style={{
                    fontSize: 12,
                    lineHeight: 1.5,
                    color: "var(--color-text-secondary)",
                    padding: "6px 8px",
                    borderRadius: 6,
                    background: "var(--color-bg-muted)",
                    border: "1px solid var(--color-border)",
                    maxHeight: 220,
                    overflowY: "auto",
                    marginBottom: 6,
                    whiteSpace: "pre-wrap",
                  }}
                >
                  <div
                    style={{
                      fontSize: 11,
                      color: "var(--color-text-muted)",
                      marginBottom: 4,
                    }}
                  >
                    Original content
                  </div>
                  {sourcePreviewImageUrl(s) && (
                    <div
                      style={{
                        position: "relative",
                        marginBottom: 6,
                        borderRadius: 6,
                        overflow: "hidden",
                        border: "1px solid var(--color-border)",
                        background: "var(--color-bg-muted)",
                      }}
                    >
                      <img
                        src={sourcePreviewImageUrl(s)!}
                        alt="Source preview"
                        style={{
                          width: "100%",
                          maxHeight: isImageDerivedSource(s) ? 280 : 120,
                          objectFit: "contain",
                          display: "block",
                        }}
                      />
                    </div>
                  )}
                  <SourcePreviewContent source={s} />
                </div>
                <div style={{ display: "flex", gap: 4 }}>
                  {canOpenSource(s) && (
                    <Tooltip title="Open source">
                      <Button
                        size="small"
                        type="text"
                        icon={<LinkOutlined />}
                        onClick={() => onOpenSource(s)}
                      >
                        {s.source_kind === "file_summary"
                          ? "View file summary"
                          : s.file_type === "audio" || s.file_type === "video"
                            ? "Play at source"
                            : isImageDerivedSource(s)
                              ? "View source image"
                              : "View source"}
                      </Button>
                    </Tooltip>
                  )}
                  <Tooltip title="Copy snippet">
                    <Button
                      size="small"
                      type="text"
                      icon={<CopyOutlined />}
                      aria-label="Copy snippet"
                      onClick={() => onCopySourceSnippet((s.content || "").trim())}
                    />
                  </Tooltip>
                  <Tooltip title="Open in modal">
                    <Button
                      size="small"
                      type="text"
                      onClick={() => onSelectCitation({ index: globalIdx, source: s })}
                    >
                      Preview
                    </Button>
                  </Tooltip>
                </div>
              </div>
            }
          >
            <Tag
              aria-label={`Open source ${globalIdx}: ${s.file_name ?? s.meeting_title ?? "untitled"}`}
              style={{
                fontSize: 11,
                borderRadius: 20,
                margin: 0,
                background: "var(--color-bg-muted)",
                borderColor: "var(--color-border)",
                cursor: "pointer",
              }}
            >
              {sourceTypeIcon(s)} <sup>[{globalIdx}]</sup>
            </Tag>
          </Popover>
        );
      })}
      {!expanded && hiddenCount > 0 && (
        <Tag
          style={{
            fontSize: 11,
            borderRadius: 20,
            margin: 0,
            background: "transparent",
            borderColor: "var(--color-border)",
            cursor: "pointer",
            color: "var(--color-text-tertiary)",
          }}
          onClick={() => setExpanded(true)}
        >
          +{hiddenCount} more <DownOutlined style={{ fontSize: 10 }} />
        </Tag>
      )}
      {expanded && sources.length > VISIBLE_SOURCE_COUNT && (
        <Tag
          style={{
            fontSize: 11,
            borderRadius: 20,
            margin: 0,
            background: "transparent",
            borderColor: "var(--color-border)",
            cursor: "pointer",
            color: "var(--color-text-tertiary)",
          }}
          onClick={() => setExpanded(false)}
        >
          Show less
        </Tag>
      )}
    </div>
  );
}
import type { ChatMessage } from "../../../hooks/useChatStream";
import { isSafeExternalUrl } from "../../../utils/url";
import type { SourceItem } from "../../../api/client";
import { PipelineTracePanel } from "./PipelineTracePanel";
import {
  formatContentType,
  formatSourceLocation,
  isImageDerivedSource,
  sourceKeyFor,
  sourceTypeIcon,
} from "./sourceHelpers";
import { SourcePreviewContent } from "./SourcePreviewContent";
import { canOpenSource, sourcePreviewImageUrl } from "./sourceLinks";
export interface CitationSelection {
  index: number;
  source: SourceItem;
}
interface Props {
  msg: ChatMessage;
  msgKey: string;
  displayContent: string;
  copiedId: string | null;
  isLast: boolean;
  isStreaming: boolean;
  openSourcePopoverKey: string | null;
  onSetOpenSourcePopoverKey: (key: string | null) => void;
  onCopy: (text: string, messageId: string) => void;
  onRegenerate: () => void;
  onOpenSource: (source: SourceItem) => void;
  onCopySourceSnippet: (text: string) => void;
  onSelectCitation: (selection: CitationSelection) => void;
}
export function AgentMetaPanels({
  msg,
  msgKey,
  displayContent,
  copiedId,
  isLast,
  isStreaming,
  openSourcePopoverKey,
  onSetOpenSourcePopoverKey,
  onCopy,
  onRegenerate,
  onOpenSource,
  onCopySourceSnippet,
  onSelectCitation,
}: Props) {
  if (msg.role !== "agent") return null;
  return (
    <>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          opacity: 0.7,
          paddingLeft: 4,
        }}
      >
        <Tooltip title={copiedId === msg.id ? "Copied!" : "Copy"}>
          <Button
            type="text"
            size="small"
            icon={<CopyOutlined />}
            onClick={() => onCopy(displayContent, msgKey)}
            style={{
              fontSize: 12,
              color: copiedId === msg.id ? "var(--color-success)" : "var(--color-text-muted)",
            }}
          >
            {copiedId === msg.id ? "Copied" : "Copy"}
          </Button>
        </Tooltip>
        {isLast && !isStreaming && (
          <Tooltip title="Regenerate response">
            <Button
              type="text"
              size="small"
              icon={<ReloadOutlined />}
              onClick={onRegenerate}
              style={{
                fontSize: 12,
                color: "var(--color-text-muted)",
              }}
            >
              Retry
            </Button>
          </Tooltip>
        )}
      </div>

      {msg.sources && msg.sources.length > 0 && (
        <SourceChipsRow
          msgKey={msgKey}
          sources={msg.sources}
          openSourcePopoverKey={openSourcePopoverKey}
          onSetOpenSourcePopoverKey={onSetOpenSourcePopoverKey}
          onOpenSource={onOpenSource}
          onCopySourceSnippet={onCopySourceSnippet}
          onSelectCitation={onSelectCitation}
        />
      )}

      {msg.webResults && msg.webResults.length > 0 && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            flexWrap: "wrap",
            paddingLeft: 4,
          }}
        >
          <LinkOutlined style={{ fontSize: 12, color: "var(--color-text-muted)" }} />
          {msg.webResults.map((w, widx) => {
            const safe = isSafeExternalUrl(w.url);
            return (
              <Tooltip key={widx} title={w.snippet}>
                {safe ? (
                  <a
                    href={w.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{
                      fontSize: 11,
                      borderRadius: 20,
                      padding: "2px 8px",
                      background: "var(--color-bg-muted)",
                      border: "1px solid var(--color-border)",
                      color: "var(--color-primary)",
                      textDecoration: "none",
                    }}
                  >
                    {w.title}
                  </a>
                ) : (
                  <span
                    style={{
                      fontSize: 11,
                      borderRadius: 20,
                      padding: "2px 8px",
                      background: "var(--color-bg-muted)",
                      border: "1px solid var(--color-border)",
                      color: "var(--color-text-muted)",
                    }}
                  >
                    {w.title}
                  </span>
                )}
              </Tooltip>
            );
          })}
        </div>
      )}

      {msg.trace && (
        <Collapse
          ghost
          size="small"
          style={{ paddingLeft: 4, background: "transparent" }}
          items={[
            {
              key: "1",
              label: (
                <span style={{ fontSize: 12, color: "var(--color-text-muted)" }}>
                  Trace ({msg.trace.spans.filter((s) => !s.skipped).length} steps,{" "}
                  {Math.round(msg.trace.total_ms)}ms)
                </span>
              ),
              children: <PipelineTracePanel trace={msg.trace} />,
            },
          ]}
        />
      )}
    </>
  );
}
