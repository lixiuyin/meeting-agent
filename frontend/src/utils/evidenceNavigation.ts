export interface EvidenceViewerCoordinates {
  page: number;
  seekTo?: number;
  seekEnd?: number;
  sourceRevision?: string;
  chunkIndex?: number;
  windowStart?: number;
  windowEnd?: number;
  evidenceExcerpt?: string;
}

const EVIDENCE_QUERY_FIELDS = {
  sourceRevision: "source_revision",
  pageNumber: "page_number",
  slideNumber: "slide_number",
  timestampStart: "timestamp_start",
  timestampEnd: "timestamp_end",
  chunkIndex: "chunk_index",
  windowStart: "window_start",
  windowEnd: "window_end",
  evidenceExcerpt: "evidence_excerpt",
} as const;

export function buildEvidenceSearchParams(
  meetingId: number,
  fileId: number,
  ref: Record<string, unknown>,
): URLSearchParams {
  const params = new URLSearchParams({
    meetingId: String(meetingId),
    fileId: String(fileId),
  });
  for (const [queryName, fieldName] of Object.entries(EVIDENCE_QUERY_FIELDS)) {
    const value = ref[fieldName];
    if (value !== null && value !== undefined && String(value).trim()) {
      params.set(queryName, String(value));
    }
  }
  return params;
}

/** A scope is a set, not an ordered list of meeting/file pairs. Only use it
 * to fill a missing meeting when that meeting is unambiguous. */
export function getEvidenceTarget(ref: Record<string, unknown>, meetingIds?: number[] | null) {
  const meetingId = Number(ref.meeting_id ?? (meetingIds?.length === 1 ? meetingIds[0] : NaN));
  const fileId = Number(ref.file_id);
  return Number.isInteger(meetingId) && meetingId > 0 && Number.isInteger(fileId) && fileId > 0
    ? { meetingId, fileId }
    : null;
}

export function parseEvidenceViewerCoordinates(params: URLSearchParams): EvidenceViewerCoordinates {
  const pageParam = params.get("pageNumber") ?? params.get("slideNumber");
  const timestampParam = params.get("timestampStart");
  const timestampEndParam = params.get("timestampEnd");
  const chunkParam = params.get("chunkIndex");
  const windowStartParam = params.get("windowStart");
  const windowEndParam = params.get("windowEnd");
  const parsedPage = pageParam === null ? Number.NaN : Number(pageParam);
  const parsedTimestamp = timestampParam === null ? Number.NaN : Number(timestampParam);
  const parsedTimestampEnd = timestampEndParam === null ? Number.NaN : Number(timestampEndParam);
  const parsedChunk = chunkParam === null ? Number.NaN : Number(chunkParam);
  const parsedWindowStart = windowStartParam === null ? Number.NaN : Number(windowStartParam);
  const parsedWindowEnd = windowEndParam === null ? Number.NaN : Number(windowEndParam);
  const seekTo =
    Number.isFinite(parsedTimestamp) && parsedTimestamp >= 0 ? parsedTimestamp : undefined;
  return {
    page: Number.isInteger(parsedPage) && parsedPage > 0 ? parsedPage : 1,
    seekTo,
    seekEnd:
      seekTo !== undefined && Number.isFinite(parsedTimestampEnd) && parsedTimestampEnd > seekTo
        ? parsedTimestampEnd
        : undefined,
    sourceRevision: params.get("sourceRevision") || undefined,
    evidenceExcerpt: params.get("evidenceExcerpt") || undefined,
    chunkIndex: Number.isInteger(parsedChunk) && parsedChunk >= 0 ? parsedChunk : undefined,
    windowStart:
      Number.isInteger(parsedWindowStart) && parsedWindowStart >= 0 ? parsedWindowStart : undefined,
    windowEnd:
      Number.isInteger(parsedWindowEnd) && parsedWindowEnd >= 0 ? parsedWindowEnd : undefined,
  };
}
