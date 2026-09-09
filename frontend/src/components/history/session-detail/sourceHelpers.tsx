import type { SourceItem } from "../../../api/client";
import { getMeetingAssetUrl } from "../../../api/client";
import type { ViewerRequest } from "../../../contexts/ViewerContext";
import {
  sourcePrimaryImageUrl as resolveSourcePrimaryImageUrl,
  sourcePreviewImageUrl as resolveSourcePreviewImageUrl,
} from "../../common/sourcePreview";
import { isAgentRole, type SessionMessage } from "./types";
import { openExternalInNewTab } from "../../../utils/url";
import { sourceToViewerRequest } from "../../../utils/sourceLocation";

export const preprocessCitations = (content: string) =>
  content.replace(/\[(\d+)\](?!\()/g, "[$1](#cite-$1)");

export const sourceKeyFor = (source: SourceItem, index: number) =>
  `${index}-${source.meeting_id}-${source.file_id ?? "na"}-${source.chunk_index ?? "na"}`;

export const messageKeyFor = (msg: SessionMessage, originalIndex: number) =>
  `${msg.role}-${originalIndex}`;

export const isImageDerivedSource = (source: SourceItem) =>
  source.content_type === "image_caption" ||
  source.content_type === "image_ocr" ||
  source.content_type === "image_combined";

export const isAvSource = (source: SourceItem) =>
  source.file_type === "audio" || source.file_type === "video";

export const sourcePrimaryImageUrl = (source: SourceItem): string | null =>
  resolveSourcePrimaryImageUrl(source, getMeetingAssetUrl);

export const sourcePreviewImageUrl = (source: SourceItem): string | null =>
  resolveSourcePreviewImageUrl(source, getMeetingAssetUrl);

export const canOpenSource = (source: SourceItem) =>
  (source.file_id != null && source.meeting_id != null) || sourcePrimaryImageUrl(source) != null;

export const openSourceFromCitation = async (
  openViewer: (args: ViewerRequest) => void,
  source: SourceItem,
) => {
  const request = sourceToViewerRequest(source);
  if (request) {
    openViewer(request);
    return;
  }
  const imageUrl = isImageDerivedSource(source) ? sourcePrimaryImageUrl(source) : null;
  if (imageUrl) {
    openExternalInNewTab(imageUrl);
    return;
  }
};

export function formatTime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}:${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export function formatSourceLocation(s: SourceItem): string {
  const parts: string[] = [];
  if (s.timestamp_start != null) {
    parts.push(formatTime(s.timestamp_start));
    if (s.timestamp_end != null) parts.push(`– ${formatTime(s.timestamp_end)}`);
  }
  if (s.page_number != null) parts.push(`Page ${s.page_number}`);
  if (s.chunk_index != null) parts.push(`Chunk ${s.chunk_index}`);
  return parts.join(" · ");
}

export const collectSummarySources = (messages: SessionMessage[]): SourceItem[] => {
  const deduped = new Map<string, SourceItem>();
  for (const msg of messages) {
    if (!isAgentRole(msg.role)) continue;
    for (const source of msg.sources ?? []) {
      const key = [
        source.meeting_id,
        source.file_id ?? "na",
        source.chunk_index ?? "na",
        source.page_number ?? "na",
        source.timestamp_start ?? "na",
        (source.content || "").trim().slice(0, 80),
      ].join("|");
      if (!deduped.has(key)) deduped.set(key, source);
    }
  }
  return Array.from(deduped.values());
};
