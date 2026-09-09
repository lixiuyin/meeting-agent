import type { FileTimelineResponse } from "../api/client";
import type { EvidenceViewerCoordinates } from "./evidenceNavigation";

/** Resolve ingestion offsets against the persisted per-file transcript, never
 * against the raw PDF or a concatenation of unrelated meeting files. */
export function resolveEvidenceLocation(
  source: string,
  timeline: FileTimelineResponse,
  coordinates: EvidenceViewerCoordinates,
  excerpt?: string | null,
): EvidenceViewerCoordinates | null {
  const points = Array.from(source);
  const { windowStart, windowEnd } = coordinates;
  const hasWindow = windowStart !== undefined && windowEnd !== undefined;
  if (hasWindow && (windowStart < 0 || windowEnd <= windowStart || windowEnd > points.length)) {
    return null;
  }
  let start = hasWindow ? windowStart : 0;
  let end = hasWindow ? windowEnd : points.length;
  if (excerpt?.trim()) {
    const windowText = points.slice(start, end).join("");
    // Only whitespace is normalized. Fuzzy matching could silently select a
    // similar but different assertion or a repeated quotation on another page.
    const pattern = excerpt
      .trim()
      .split(/\s+/)
      .map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
      .join("\\s+");
    const matches = [...windowText.matchAll(new RegExp(pattern, "gu"))];
    if (matches.length !== 1) return null;
    start += Array.from(windowText.slice(0, matches[0].index)).length;
    end = start + Array.from(matches[0][0]).length;
  } else if (!hasWindow) {
    return null;
  }
  const resolved = {
    ...coordinates,
    windowStart: start,
    windowEnd: end,
    evidenceExcerpt: excerpt?.trim() ? points.slice(start, end).join("") : undefined,
  };
  if (timeline.kind === "text") return resolved;

  const parts =
    timeline.kind === "pages"
      ? timeline.pages
      : timeline.kind === "segments"
        ? timeline.segments
        : [];
  let cursor = 0;
  let pointCursor = 0;
  const intersecting: number[] = [];
  for (let i = 0; i < parts.length; i += 1) {
    const text = parts[i].text;
    if (!text.trim()) continue;
    const found = source.indexOf(text, cursor);
    if (found < 0) return null;
    const partStart = pointCursor + Array.from(source.slice(cursor, found)).length;
    const partEnd = partStart + Array.from(text).length;
    cursor = found + text.length;
    pointCursor = partEnd;
    if (partStart < end && partEnd > start) intersecting.push(i);
  }
  if (!intersecting.length) return null;
  if (timeline.kind === "pages") {
    return { ...resolved, page: timeline.pages[intersecting[0]].page_num };
  }
  if (timeline.kind === "segments") {
    return {
      ...resolved,
      seekTo: timeline.segments[intersecting[0]].start,
      seekEnd: timeline.segments[intersecting[intersecting.length - 1]].end,
    };
  }
  return null;
}
