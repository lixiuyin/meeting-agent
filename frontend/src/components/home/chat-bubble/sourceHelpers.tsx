import {
  AudioOutlined,
  FileImageOutlined,
  FilePdfOutlined,
  FileTextOutlined,
  ProfileOutlined,
  ReadOutlined,
  VideoCameraOutlined,
} from "@ant-design/icons";
import type { SourceItem } from "../../../api/client";

const formatMmSs = (seconds: number) => {
  const m = Math.floor(seconds / 60)
    .toString()
    .padStart(2, "0");
  const s = Math.floor(seconds % 60)
    .toString()
    .padStart(2, "0");
  return `${m}:${s}`;
};

export const sourceTypeIcon = (source: SourceItem) => {
  if (source.source_kind === "meeting_summary") return <ReadOutlined />;
  if (source.source_kind === "file_summary") return <ProfileOutlined />;
  if (source.file_type === "audio") return <AudioOutlined />;
  if (source.file_type === "video") return <VideoCameraOutlined />;
  if (source.file_type === "pdf") return <FilePdfOutlined />;
  if (source.file_type === "image") return <FileImageOutlined />;
  if (source.content_type === "image_caption" || source.content_type === "image_combined")
    return <FileImageOutlined />;
  return <FileTextOutlined />;
};

export const formatContentType = (source: SourceItem) => {
  if (source.content_type === "table") return "Table";
  if (source.content_type === "image_caption") return "Image caption";
  if (source.content_type === "image_ocr") return "Image OCR";
  if (source.content_type === "image_combined") return "Image (caption + OCR)";
  return "Text";
};

export const formatSourceLocation = (source: SourceItem) => {
  const kind = source.source_kind;
  if (kind === "meeting_summary") return "[Meeting Summary]";
  if (kind === "file_summary") return "[File Summary]";
  if (kind === "timestamp" && source.timestamp_start != null) {
    const label = formatMmSs(source.timestamp_start);
    const speaker = source.speaker ? ` · ${source.speaker}` : "";
    return `[${label}${speaker}]`;
  }
  if (kind === "slide" && source.page_number != null)
    return `Slide ${source.slide_number ?? source.page_number}`;
  if (kind === "page" && source.page_number != null) return `Page ${source.page_number}`;
  if (kind === "image") return "[Figure]";
  if (source.timestamp_start != null) return `[${formatMmSs(source.timestamp_start)}]`;
  if (source.page_number != null) return `p.${source.page_number}`;
  return null;
};

export const sourceKeyFor = (msgKey: string, source: SourceItem) =>
  `${msgKey}-${source.meeting_id}-${source.file_id ?? "na"}-${source.chunk_index ?? "na"}`;

export const sanitizeAgentAnswer = (content: string) =>
  content
    .replace(/(\uff09\u4fe1\u606f\u6765\u6e90[:\uff1a][^\uff09]*\uff09)\s*$/u, "")
    .replace(/\(\u4fe1\u606f\u6765\u6e90[:\uff1a][^)]*\)\s*$/u, "")
    .trim();

export const topSources = (sources?: SourceItem[]) => (sources ?? []).slice(0, 5);
export const isImageDerivedSource = (source: SourceItem) =>
  source.content_type === "image_caption" ||
  source.content_type === "image_ocr" ||
  source.content_type === "image_combined";
export const isAvSource = (source: SourceItem) =>
  source.file_type === "audio" || source.file_type === "video";
