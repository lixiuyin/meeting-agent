import {
  BookOutlined,
  CopyOutlined,
  DownOutlined,
  LinkOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import { Button, Collapse, Popover, Tooltip } from "antd";
import { useState } from "react";
import { useIntl } from "react-intl";

const VISIBLE_SOURCE_COUNT = 3;

interface SourceChipsRowProps {
  msgKey: string;
  sources: SourceItem[];
  sourceIndexes: number[];
  openSourcePopoverKey: string | null;
  onSetOpenSourcePopoverKey: (key: string | null) => void;
  onOpenSource: (source: SourceItem) => void;
  onCopySourceSnippet: (text: string) => void;
  onSelectCitation: (selection: CitationSelection) => void;
}

function SourceChipsRow({
  msgKey,
  sources,
  sourceIndexes,
  openSourcePopoverKey,
  onSetOpenSourcePopoverKey,
  onOpenSource,
  onCopySourceSnippet,
  onSelectCitation,
}: SourceChipsRowProps) {
  const { formatMessage } = useIntl();
  const [expanded, setExpanded] = useState(false);
  const indexed = sourceIndexes
    .map((globalIdx) => ({ source: sources[globalIdx - 1], globalIdx }))
    .filter((entry): entry is { source: SourceItem; globalIdx: number } => !!entry.source);
  const visible = expanded ? indexed : indexed.slice(0, VISIBLE_SOURCE_COUNT);
  const hiddenCount = indexed.length - VISIBLE_SOURCE_COUNT;

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
        {formatMessage({ id: "chat.citedSources" }, { count: indexed.length })}:
      </span>
      {visible.map(({ source: s, globalIdx }) => {
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
                  <span>
                    ·{" "}
                    {s.memory_key
                      ? formatMessage({ id: "memory.tabs.memories" })
                      : `Retrieval score ${s.score.toFixed(3)}`}
                  </span>
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
                    {formatMessage({ id: "chat.originalContent" })}
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
                        alt={formatMessage({ id: "chat.sourcePreview" })}
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
                    <Tooltip title={formatMessage({ id: "chat.openSource" })}>
                      <Button
                        size="small"
                        type="text"
                        icon={<LinkOutlined />}
                        onClick={() => onOpenSource(s)}
                      >
                        {s.source_kind === "file_summary"
                          ? formatMessage({ id: "chat.viewFileSummary" })
                          : s.file_type === "audio" || s.file_type === "video"
                            ? formatMessage({ id: "chat.playAtSource" })
                            : isImageDerivedSource(s)
                              ? formatMessage({ id: "chat.viewSourceImage" })
                              : formatMessage({ id: "chat.viewSource" })}
                      </Button>
                    </Tooltip>
                  )}
                  <Tooltip title={formatMessage({ id: "chat.copySnippet" })}>
                    <Button
                      size="small"
                      type="text"
                      icon={<CopyOutlined />}
                      aria-label={formatMessage({ id: "chat.copySnippet" })}
                      onClick={() => onCopySourceSnippet((s.content || "").trim())}
                    />
                  </Tooltip>
                  <Tooltip title={formatMessage({ id: "chat.openInModal" })}>
                    <Button
                      size="small"
                      type="text"
                      onClick={() => onSelectCitation({ index: globalIdx, source: s })}
                    >
                      {formatMessage({ id: "chat.preview" })}
                    </Button>
                  </Tooltip>
                </div>
              </div>
            }
          >
            <Button
              type="default"
              size="small"
              aria-label={`Open source ${globalIdx}: ${s.file_name ?? s.meeting_title ?? "untitled"}`}
              style={{
                fontSize: 11,
                borderRadius: 20,
                background: "var(--color-bg-muted)",
                borderColor: "var(--color-border)",
                color: "var(--color-text-secondary)",
                maxWidth: 220,
              }}
            >
              {sourceTypeIcon(s)} [{globalIdx}] {s.file_name ?? s.meeting_title ?? "Untitled"}
            </Button>
          </Popover>
        );
      })}
      {!expanded && hiddenCount > 0 && (
        <Button
          type="text"
          size="small"
          aria-expanded={false}
          style={{
            fontSize: 11,
            borderRadius: 20,
            background: "transparent",
            color: "var(--color-text-tertiary)",
          }}
          onClick={() => setExpanded(true)}
        >
          {formatMessage({ id: "chat.moreSources" }, { count: hiddenCount })}{" "}
          <DownOutlined style={{ fontSize: 10 }} />
        </Button>
      )}
      {expanded && indexed.length > VISIBLE_SOURCE_COUNT && (
        <Button
          type="text"
          size="small"
          aria-expanded={true}
          style={{
            fontSize: 11,
            borderRadius: 20,
            background: "transparent",
            color: "var(--color-text-tertiary)",
          }}
          onClick={() => setExpanded(false)}
        >
          {formatMessage({ id: "chat.showLess" })}
        </Button>
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

function extractCitedSourceIndexes(content: string, sourceCount: number): number[] {
  const indexes = new Set<number>();
  for (const match of content.matchAll(/\[(\d+)(?:[-–](\d+))?\]/g)) {
    const start = Number(match[1]);
    const end = match[2] ? Number(match[2]) : start;
    if (!Number.isInteger(start) || !Number.isInteger(end) || end < start) continue;
    for (let index = start; index <= Math.min(end, sourceCount); index += 1) {
      if (index >= 1) indexes.add(index);
    }
  }
  return Array.from(indexes);
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
  const { formatMessage } = useIntl();
  if (msg.role !== "agent") return null;
  const citedSourceIndexes = extractCitedSourceIndexes(displayContent, msg.sources?.length ?? 0);
  return (
    <>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          paddingLeft: 4,
        }}
      >
        <Tooltip title={formatMessage({ id: copiedId === msg.id ? "chat.copied" : "chat.copy" })}>
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
            {formatMessage({ id: copiedId === msg.id ? "chat.copied" : "chat.copy" })}
          </Button>
        </Tooltip>
        {isLast && !isStreaming && (
          <Tooltip title={formatMessage({ id: "chat.regenerate" })}>
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
              {formatMessage({ id: "chat.retry" })}
            </Button>
          </Tooltip>
        )}
      </div>

      {msg.sources && msg.sources.length > 0 && citedSourceIndexes.length > 0 && (
        <SourceChipsRow
          msgKey={msgKey}
          sources={msg.sources}
          sourceIndexes={citedSourceIndexes}
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

      {import.meta.env.DEV && msg.trace && (
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
