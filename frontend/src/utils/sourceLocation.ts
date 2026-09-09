import { getMeeting, locateFileEvidence } from "../api/client";
import type { SourceItem } from "../api/client";
import type { ViewerRequest } from "../contexts/ViewerContext";
import { getKindCapabilities } from "../types/fileKinds";

export function sourceToViewerRequest(source: SourceItem): ViewerRequest | null {
  if (!source.meeting_id || !source.file_id) return null;
  return {
    meetingId: source.meeting_id,
    fileId: source.file_id,
    fileName: source.file_name ?? source.meeting_title,
    fileType: source.file_type ?? "unknown",
    meetingTitle: source.meeting_title,
    sourceRevision: source.document_revision ?? undefined,
    page: source.page_number ?? source.slide_number ?? undefined,
    chunkIndex: source.chunk_index ?? undefined,
    windowStart: source.window_start ?? undefined,
    windowEnd: source.window_end ?? undefined,
    evidenceExcerpt:
      source.evidence_excerpt ??
      (source.content_type === "text" && source.content && !source.content.endsWith("…")
        ? source.content
        : undefined),
    seekTo: getKindCapabilities(source.file_type).hasTimeline
      ? (source.timestamp_start ?? undefined)
      : undefined,
    seekEnd: getKindCapabilities(source.file_type).hasTimeline
      ? (source.timestamp_end ?? undefined)
      : undefined,
  };
}

/** All in-app sources, including old conversations, pass this version fence. */
export async function resolveViewerRequest(req: ViewerRequest, signal: AbortSignal) {
  const { data: meeting } = await getMeeting(req.meetingId, { signal });
  const file = meeting.files.find((item) => item.id === req.fileId);
  if (!file) throw new Error("viewer.sourceUnavailable");
  if (req.sourceRevision && !(file.source_revisions ?? []).includes(req.sourceRevision))
    throw new Error("viewer.sourceVersionChanged");
  const current = {
    ...req,
    fileName: file.file_name,
    fileType: file.file_type,
    meetingTitle: meeting.title,
  };
  if (req.windowStart === undefined && !req.evidenceExcerpt && !req.page)
    return { request: current, warning: undefined };
  const { data: location } = await locateFileEvidence(
    req.meetingId,
    req.fileId,
    {
      source_revision: req.sourceRevision,
      window_start: req.windowStart,
      window_end: req.windowEnd,
      excerpt: req.evidenceExcerpt,
      // A legacy chunk can span pages; its exact quote/window takes precedence.
      page: req.windowStart === undefined ? req.page : undefined,
    },
    { signal },
  );
  if (location.status === "version_changed") throw new Error("viewer.sourceVersionChanged");
  if (location.status === "exact" || location.status === "page_only") {
    return {
      request: {
        ...current,
        page: location.page ?? req.page,
        windowStart: location.window_start ?? undefined,
        windowEnd: location.window_end ?? undefined,
        evidenceExcerpt: location.excerpt ?? undefined,
        seekTo: location.timestamp_start ?? req.seekTo,
        seekEnd: location.timestamp_end ?? req.seekEnd,
        sourceRevision: location.source_revision,
      },
      warning: location.status === "page_only" ? "viewer.exactLocationUnavailable" : undefined,
    };
  }
  return {
    request: {
      ...current,
      page: req.page ?? 1,
      windowStart: undefined,
      windowEnd: undefined,
      evidenceExcerpt: undefined,
    },
    warning: "viewer.exactLocationUnavailable",
  };
}
