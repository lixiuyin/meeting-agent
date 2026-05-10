import { getMeetingAssetUrl, type SourceItem } from "../../../api/client";
import {
  sourcePrimaryImageUrl as resolveSourcePrimaryImageUrl,
  sourcePreviewImageUrl as resolveSourcePreviewImageUrl,
} from "../../common/sourcePreview";

export const sourcePreviewImageUrl = (source: SourceItem) => {
  return resolveSourcePreviewImageUrl(source, getMeetingAssetUrl);
};

export const sourcePrimaryImageUrl = (source: SourceItem) => {
  return resolveSourcePrimaryImageUrl(source, getMeetingAssetUrl);
};

export const canOpenSource = (source: SourceItem) =>
  (source.file_id != null && source.meeting_id != null) ||
  sourcePrimaryImageUrl(source) != null ||
  (source.source_kind === "meeting_summary" && source.meeting_id != null) ||
  (source.source_kind === "file_summary" && source.meeting_id != null);
