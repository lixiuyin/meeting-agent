/**
 * Trace utility functions for the pipeline trace bar visualization.
 *
 * Pure functions for grouping spans, assigning colors, and computing
 * bar segments from trace data.
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface TraceSpan {
  label: string;
  phase: string;
  duration_ms: number | null;
  status: string;
  metadata?: Record<string, unknown>;
  parent_label?: string;
  skipped?: boolean;
  tokens_in?: number;
  tokens_out?: number;
  docs_retrieved?: number;
}

export interface BarSegment {
  label: string;
  startPct: number;
  widthPct: number;
  duration_ms: number | null;
  phase: string;
  status: string;
  skipped: boolean;
  metadata?: Record<string, unknown>;
  color: string;
  tokens_in?: number;
  tokens_out?: number;
  docs_retrieved?: number;
}

export interface SpanGroup {
  roots: TraceSpan[];
  children: Map<string, TraceSpan[]>;
}

// ---------------------------------------------------------------------------
// Color mapping
// ---------------------------------------------------------------------------

const PHASE_COLORS: Record<string, string> = {
  routing: "#94a3b8",
  skill: "#06b6d4",
  session: "#a855f7",
  retrieve: "#3b82f6",
  memory: "#f97316",
  search: "#eab308",
  assemble: "#ec4899",
  generate: "#ef4444",
  persist: "#6b7280",
  pipeline: "#475569",
};

/** Assign a color to a span based on its phase field. */
export function assignColor(phase: string): string {
  return PHASE_COLORS[phase] ?? "#475569";
}

// ---------------------------------------------------------------------------
// Grouping
// ---------------------------------------------------------------------------

/**
 * Split spans into root spans (no parent_label) and child spans grouped by
 * parent_label. Returns a new immutable SpanGroup.
 */
export function groupSpansByLevel(spans: readonly TraceSpan[]): SpanGroup {
  const roots: TraceSpan[] = [];
  const children = new Map<string, TraceSpan[]>();

  for (const s of spans) {
    if (s.parent_label) {
      const list = children.get(s.parent_label) ?? [];
      list.push(s);
      children.set(s.parent_label, list);
    } else {
      roots.push(s);
    }
  }

  return { roots, children };
}

// ---------------------------------------------------------------------------
// Bar segment computation
// ---------------------------------------------------------------------------

const MIN_SEGMENT_WIDTH_PCT = 0.4;

/**
 * Compute proportional bar segments from root spans.
 *
 * Each segment represents one root span, positioned proportionally within
 * the total pipeline duration. Segments are guaranteed a minimum width so
 * tiny spans remain visible.
 */
export function computeBarSegments(rootSpans: readonly TraceSpan[], totalMs: number): BarSegment[] {
  if (totalMs <= 0 || rootSpans.length === 0) return [];

  const segments: BarSegment[] = [];
  let offsetPct = 0;

  for (const span of rootSpans) {
    const rawPct = span.duration_ms != null ? (span.duration_ms / totalMs) * 100 : 0;
    const widthPct = Math.max(rawPct, MIN_SEGMENT_WIDTH_PCT);

    segments.push({
      label: span.label,
      startPct: offsetPct,
      widthPct,
      duration_ms: span.duration_ms,
      phase: span.phase,
      status: span.status,
      skipped: span.skipped ?? false,
      metadata: span.metadata,
      color: span.skipped ? "#6b7280" : assignColor(span.phase),
      tokens_in: span.tokens_in,
      tokens_out: span.tokens_out,
      docs_retrieved: span.docs_retrieved,
    });

    offsetPct += rawPct;
  }

  return segments;
}
