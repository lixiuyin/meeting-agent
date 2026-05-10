/**
 * PipelineTracePanel — combines the TraceBar visualization with a
 * collapsible detailed tree view of the original span hierarchy.
 */

import { Collapse } from "antd";
import type { ReactNode } from "react";
import type { ChatMessage } from "../../../hooks/useChatStream";
import { TraceBar } from "./TraceBar";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type TraceSpan = NonNullable<ChatMessage["trace"]>["spans"][number];

interface Props {
  trace: NonNullable<ChatMessage["trace"]>;
}

// ---------------------------------------------------------------------------
// Tree renderer (legacy detail view)
// ---------------------------------------------------------------------------

function renderSpan(
  s: TraceSpan,
  depth: number,
  totalMs: number,
  childMap: Map<string, TraceSpan[]>,
): ReactNode {
  const pct = s.duration_ms != null ? Math.max((s.duration_ms / totalMs) * 100, 0.5) : 0;
  const isError = s.status === "error";
  const color = s.skipped ? "var(--color-text-muted)" : isError ? "#ff4d4f" : "var(--color-text)";
  const barColor = isError ? "#ff4d4f" : s.skipped ? "var(--color-text-muted)" : "#1677ff";

  const metaChips: string[] = [];
  if (s.tokens_in != null) metaChips.push(`in: ${s.tokens_in}`);
  if (s.tokens_out != null) metaChips.push(`out: ${s.tokens_out}`);
  if (s.docs_retrieved != null) metaChips.push(`docs: ${s.docs_retrieved}`);

  const childSpans = childMap.get(s.label) ?? [];

  return (
    <div key={s.label + depth}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 2 }}>
        <span
          style={{
            paddingLeft: depth * 16,
            fontSize: 11,
            color,
            fontFamily: "monospace",
            whiteSpace: "nowrap",
            opacity: s.skipped ? 0.5 : 1,
          }}
        >
          {s.label}
        </span>
        <div
          style={{
            flex: 1,
            height: 4,
            borderRadius: 2,
            background: "var(--color-bg-muted, #f0f0f0)",
            minWidth: 20,
            maxWidth: 160,
            opacity: s.skipped ? 0.3 : 0.6,
          }}
        >
          <div
            style={{
              width: `${pct}%`,
              height: "100%",
              borderRadius: 2,
              background: barColor,
            }}
          />
        </div>
        <span
          style={{
            fontSize: 11,
            color,
            fontFamily: "monospace",
            whiteSpace: "nowrap",
          }}
        >
          {s.duration_ms != null ? `${Math.round(s.duration_ms)}ms` : "..."}
        </span>
        {metaChips.length > 0 && (
          <span
            style={{
              fontSize: 10,
              color: "var(--color-text-muted)",
              fontFamily: "monospace",
            }}
          >
            [{metaChips.join(", ")}]
          </span>
        )}
      </div>
      {childSpans.map((c) => renderSpan(c, depth + 1, totalMs, childMap))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function PipelineTracePanel({ trace }: Props) {
  const totalMs = trace.total_ms || 1;
  const spans = trace.spans;

  const childMap = new Map<string, TraceSpan[]>();
  const roots: TraceSpan[] = [];
  for (const s of spans) {
    if (s.parent_label) {
      const list = childMap.get(s.parent_label) ?? [];
      list.push(s);
      childMap.set(s.parent_label, list);
    } else {
      roots.push(s);
    }
  }

  return (
    <div style={{ fontSize: 12, fontFamily: "monospace", color: "var(--color-text-muted)" }}>
      {/* Visual bar chart */}
      <TraceBar trace={trace} />

      {/* Detailed tree (collapsible) */}
      <Collapse
        ghost
        size="small"
        style={{ marginTop: 4, background: "transparent" }}
        items={[
          {
            key: "detail",
            label: (
              <span style={{ fontSize: 11, color: "var(--color-text-muted)" }}>Detailed view</span>
            ),
            children: (
              <div
                style={{ fontSize: 12, fontFamily: "monospace", color: "var(--color-text-muted)" }}
              >
                <div style={{ marginBottom: 4 }}>
                  Trace ID: {trace.trace_id} | Total: {Math.round(totalMs)}ms
                </div>
                {roots.map((s) => renderSpan(s, 0, totalMs, childMap))}
              </div>
            ),
          },
        ]}
      />
    </div>
  );
}
