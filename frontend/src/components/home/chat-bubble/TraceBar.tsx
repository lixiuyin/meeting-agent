/**
 * TraceBar — horizontal stacked bar chart for pipeline trace visualization.
 *
 * Shows timing proportions visually with colored segments. Supports
 * expansion to reveal child spans in a nested sub-bar.
 */

import { useState, useCallback } from "react";
import { Tooltip } from "antd";
import type { ChatMessage } from "../../../hooks/useChatStream";
import {
  groupSpansByLevel,
  computeBarSegments,
  type BarSegment,
  type TraceSpan,
} from "../../../utils/trace";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface TraceBarProps {
  trace: NonNullable<ChatMessage["trace"]>;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDuration(ms: number | null): string {
  if (ms == null) return "...";
  if (ms < 1) return `${ms.toFixed(1)}ms`;
  return `${Math.round(ms)}ms`;
}

function buildTooltipText(seg: BarSegment): string {
  const parts = [seg.label, formatDuration(seg.duration_ms)];
  if (seg.status === "error") parts.push("(error)");
  if (seg.status === "degraded") parts.push("(degraded; fallback used)");
  if (seg.status === "timeout") parts.push("(timeout; fallback used)");
  if (seg.skipped) parts.push("(skipped)");
  if (seg.tokens_in != null) parts.push(`in: ${seg.tokens_in}`);
  if (seg.tokens_out != null) parts.push(`out: ${seg.tokens_out}`);
  if (seg.docs_retrieved != null) parts.push(`docs: ${seg.docs_retrieved}`);
  return parts.join(" | ");
}

function findSlowest(segments: BarSegment[]): BarSegment | null {
  if (segments.length === 0) return null;
  return segments.reduce((worst, seg) => {
    if (seg.skipped) return worst;
    if (worst.skipped) return seg;
    return (seg.duration_ms ?? 0) > (worst.duration_ms ?? 0) ? seg : worst;
  });
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

interface SegmentBarProps {
  segments: BarSegment[];
}

function SegmentBar({ segments }: SegmentBarProps) {
  return (
    <div
      style={{
        display: "flex",
        height: 12,
        borderRadius: 6,
        overflow: "hidden",
        background: "var(--color-bg-muted)",
        gap: 1,
      }}
    >
      {segments.map((seg) => {
        const isSkipped = seg.skipped;
        const isError = seg.status === "error";
        const isDegraded = seg.status === "degraded" || seg.status === "timeout";
        const backgroundColor = isError
          ? "#ff4d4f"
          : isDegraded
            ? "#fa8c16"
            : isSkipped
              ? "transparent"
              : seg.color;

        return (
          <Tooltip key={seg.label} title={buildTooltipText(seg)}>
            <div
              style={{
                minWidth: 2,
                width: `${seg.widthPct}%`,
                background: backgroundColor,
                border: isSkipped ? "1px dashed var(--color-text-muted)" : "none",
                borderRadius: 2,
                opacity: isSkipped ? 0.5 : 0.85,
              }}
            />
          </Tooltip>
        );
      })}
    </div>
  );
}

interface ChildSpanListProps {
  children: TraceSpan[];
  totalMs: number;
}

function ChildSpanList({ children, totalMs }: ChildSpanListProps) {
  const childSegments = computeBarSegments(children, totalMs);
  if (childSegments.length === 0) return null;

  return (
    <div
      style={{
        marginTop: 6,
        paddingLeft: 8,
        borderLeft: "2px solid var(--color-border)",
      }}
    >
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: "4px 10px",
          marginBottom: 4,
        }}
      >
        {children.map((child) => {
          const color = child.skipped
            ? "var(--color-text-muted)"
            : child.status === "error"
              ? "#ff4d4f"
              : "var(--color-text-secondary)";
          return (
            <span
              key={child.label}
              style={{
                fontSize: 10,
                fontFamily: "monospace",
                color,
                opacity: child.skipped ? 0.5 : 1,
              }}
            >
              {child.label}: {formatDuration(child.duration_ms)}
              {child.tokens_out != null && ` (out: ${child.tokens_out})`}
            </span>
          );
        })}
      </div>
      <SegmentBar segments={childSegments} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function TraceBar({ trace }: TraceBarProps) {
  const [expanded, setExpanded] = useState(false);
  const toggleExpanded = useCallback(() => setExpanded((prev) => !prev), []);

  const totalMs = trace.total_ms || 1;
  const activeSteps = trace.spans.filter((s) => !s.skipped).length;
  const { roots, children } = groupSpansByLevel(trace.spans);
  const segments = computeBarSegments(roots, totalMs);
  const slowest = findSlowest(segments);

  if (segments.length === 0) return null;

  return (
    <div
      style={{
        fontSize: 12,
        fontFamily: "monospace",
        color: "var(--color-text-muted)",
        paddingLeft: 4,
      }}
    >
      {/* Stats header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginBottom: 4,
          cursor: "pointer",
          userSelect: "none",
        }}
        onClick={toggleExpanded}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            toggleExpanded();
          }
        }}
      >
        <span style={{ fontSize: 11 }}>
          Trace &middot; {Math.round(totalMs)}ms &middot; {activeSteps} steps
        </span>
        {slowest && (
          <span style={{ fontSize: 10, color: slowest.color }}>
            slowest: {slowest.label} ({formatDuration(slowest.duration_ms)})
          </span>
        )}
        <span
          style={{
            fontSize: 9,
            color: "var(--color-text-muted)",
            transition: "transform 150ms ease",
            display: "inline-block",
            transform: expanded ? "rotate(90deg)" : "rotate(0deg)",
          }}
        >
          &#9654;
        </span>
      </div>

      {/* Main bar */}
      <SegmentBar segments={segments} />

      {/* Expanded child spans */}
      {expanded && (
        <div style={{ marginTop: 6 }}>
          {roots.map((root) => {
            const childSpans = children.get(root.label);
            if (!childSpans || childSpans.length === 0) return null;
            return (
              <ChildSpanList
                key={root.label}
                children={childSpans}
                totalMs={root.duration_ms ?? totalMs}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}
