import { FileTextOutlined } from "@ant-design/icons";
import { Tag } from "antd";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { SourceItem, SessionSummaryItem } from "../../../api/client";
import { CitationMarkdown } from "./CitationMarkdown";
import { SourceChips } from "./SourceChips";
import { sourceKeyFor } from "./sourceHelpers";
import { SourceDetailModal } from "./SourceDetailModal";

interface Props {
  summary: SessionSummaryItem;
  sources: SourceItem[];
}

export function SummarySection({ summary, sources }: Props) {
  const [openSourcePopoverKey, setOpenSourcePopoverKey] = useState<string | null>(null);
  const [flashSourceKey, setFlashSourceKey] = useState<string | null>(null);
  const flashTimerRef = useRef<number | null>(null);
  const [selectedCitation, setSelectedCitation] = useState<{
    index: number;
    source: SourceItem;
  } | null>(null);
  const citationSources = useMemo(() => sources, [sources]);

  useEffect(
    () => () => {
      if (flashTimerRef.current != null) window.clearTimeout(flashTimerRef.current);
    },
    [],
  );

  const handleCiteClick = useCallback(
    (citeIdx: number) => {
      const src = citationSources[citeIdx - 1];
      if (!src) return;
      const targetKey = sourceKeyFor(src, citeIdx);
      setSelectedCitation({ index: citeIdx, source: src });
      setOpenSourcePopoverKey(targetKey);
      setFlashSourceKey(targetKey);
      if (flashTimerRef.current != null) window.clearTimeout(flashTimerRef.current);
      flashTimerRef.current = window.setTimeout(() => {
        setFlashSourceKey((prev) => (prev === targetKey ? null : prev));
      }, 1000);
    },
    [citationSources],
  );

  return (
    <div
      style={{
        background: "var(--color-bg-surface)",
        borderRadius: 12,
        padding: 16,
        marginBottom: 16,
        border: "1px solid var(--color-border)",
      }}
    >
      <div
        style={{
          fontSize: 13,
          fontWeight: 600,
          color: "var(--color-text-primary)",
          marginBottom: 8,
          display: "flex",
          alignItems: "center",
          gap: 8,
        }}
      >
        <FileTextOutlined style={{ color: "var(--color-primary)" }} />
        Summary
      </div>
      <div
        className="markdown-body"
        style={{
          fontSize: 13,
          color: "var(--color-text-secondary)",
          lineHeight: 1.6,
          marginBottom: 12,
        }}
      >
        <CitationMarkdown
          content={summary.summary}
          sourceCount={sources.length}
          onCiteClick={handleCiteClick}
        />
      </div>
      {sources.length > 0 && (
        <SourceChips
          sources={sources}
          openSourcePopoverKey={openSourcePopoverKey}
          onOpenSourcePopoverChange={setOpenSourcePopoverKey}
          flashSourceKey={flashSourceKey}
        />
      )}
      {summary.topics.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {summary.topics.map((t) => (
            <Tag key={t} style={{ borderRadius: 20, margin: 0 }}>
              {t}
            </Tag>
          ))}
        </div>
      )}
      <SourceDetailModal
        selectedCitation={selectedCitation}
        onClose={() => setSelectedCitation(null)}
      />
    </div>
  );
}
