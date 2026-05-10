/* eslint-disable react-refresh/only-export-components */
import {
  FileImageOutlined,
  FileTextOutlined,
  VideoCameraOutlined,
  FilePdfOutlined,
  AudioOutlined,
  AppstoreOutlined,
} from "@ant-design/icons";

export const MEETING_TYPE_CONFIG: Record<
  string,
  { icon: React.ReactNode; gradient: string; label: string; color: string; bg: string }
> = {
  video: {
    icon: <VideoCameraOutlined />,
    gradient: "linear-gradient(135deg, #f43f5e 0%, #e11d48 100%)",
    label: "Video",
    color: "#f43f5e",
    bg: "rgba(244, 63, 94, 0.1)",
  },
  audio: {
    icon: <AudioOutlined />,
    gradient: "linear-gradient(135deg, #8b5cf6 0%, #7c3aed 100%)",
    label: "Audio",
    color: "#8b5cf6",
    bg: "rgba(139, 92, 246, 0.1)",
  },
  pdf: {
    icon: <FilePdfOutlined />,
    gradient: "linear-gradient(135deg, #f59e0b 0%, #d97706 100%)",
    label: "PDF",
    color: "#f59e0b",
    bg: "rgba(245, 158, 11, 0.1)",
  },
  ppt: {
    icon: <FileTextOutlined />,
    gradient: "linear-gradient(135deg, #10b981 0%, #059669 100%)",
    label: "Presentation",
    color: "#10b981",
    bg: "rgba(16, 185, 129, 0.1)",
  },
  doc: {
    icon: <FileTextOutlined />,
    gradient: "linear-gradient(135deg, #3b82f6 0%, #2563eb 100%)",
    label: "Document",
    color: "#3b82f6",
    bg: "rgba(59, 130, 246, 0.1)",
  },
  xls: {
    icon: <FileTextOutlined />,
    gradient: "linear-gradient(135deg, #10b981 0%, #059669 100%)",
    label: "Spreadsheet",
    color: "#10b981",
    bg: "rgba(16, 185, 129, 0.1)",
  },
  csv: {
    icon: <FileTextOutlined />,
    gradient: "linear-gradient(135deg, #6b7280 0%, #4b5563 100%)",
    label: "CSV",
    color: "#6b7280",
    bg: "rgba(107, 114, 128, 0.1)",
  },
  txt: {
    icon: <FileTextOutlined />,
    gradient: "linear-gradient(135deg, #6b7280 0%, #4b5563 100%)",
    label: "Text",
    color: "#6b7280",
    bg: "rgba(107, 114, 128, 0.1)",
  },
  image: {
    icon: <FileImageOutlined />,
    gradient: "linear-gradient(135deg, #3b82f6 0%, #2563eb 100%)",
    label: "Image",
    color: "#3b82f6",
    bg: "rgba(59, 130, 246, 0.1)",
  },
  mixed: {
    icon: <AppstoreOutlined />,
    gradient: "linear-gradient(135deg, #6366f1 0%, #4f46e5 100%)",
    label: "Mixed",
    color: "#6366f1",
    bg: "rgba(99, 102, 241, 0.1)",
  },
};

export type MeetingTypeConfig = (typeof MEETING_TYPE_CONFIG)[keyof typeof MEETING_TYPE_CONFIG];

export const MEETING_STATUS_CONFIG: Record<
  string,
  { color: string; bg: string; label: string; dot: string }
> = {
  uploading: {
    color: "#6b7280",
    bg: "rgba(107, 114, 128, 0.1)",
    label: "Uploading",
    dot: "#6b7280",
  },
  processing: {
    color: "#3b82f6",
    bg: "rgba(59, 130, 246, 0.1)",
    label: "Processing",
    dot: "#3b82f6",
  },
  summarizing: {
    color: "#8b5cf6",
    bg: "rgba(139, 92, 246, 0.1)",
    label: "Summarizing",
    dot: "#8b5cf6",
  },
  ready: { color: "#10b981", bg: "rgba(16, 185, 129, 0.1)", label: "Ready", dot: "#10b981" },
  failed: { color: "#ef4444", bg: "rgba(239, 68, 68, 0.1)", label: "Failed", dot: "#ef4444" },
  error: { color: "#ef4444", bg: "rgba(239, 68, 68, 0.1)", label: "Failed", dot: "#ef4444" },
};

export type MeetingStatusConfig =
  (typeof MEETING_STATUS_CONFIG)[keyof typeof MEETING_STATUS_CONFIG];

export const STATUS_CONFIG_FALLBACK = Object.freeze({
  color: "#6b7280",
  bg: "rgba(107, 114, 128, 0.1)",
  label: "Unknown",
  dot: "#6b7280",
});

export const SPEAKER_TAG_STYLE = Object.freeze({
  fontSize: 10,
  borderRadius: 10,
  padding: "0 8px",
  lineHeight: "18px" as const,
  borderColor: "rgba(244, 63, 94, 0.35)",
  background: "rgba(244, 63, 94, 0.12)",
  color: "#be123c",
});

export const TIMESTAMP_BADGE_STYLE = Object.freeze({
  display: "inline-flex" as const,
  alignItems: "center",
  gap: 5,
  fontSize: 11,
  fontWeight: 600,
  color: "#0f766e",
  background: "rgba(20, 184, 166, 0.14)",
  border: "1px solid rgba(20, 184, 166, 0.35)",
  borderRadius: 999,
  padding: "2px 8px",
});

export const TIMESPAN_DOT_STYLE = Object.freeze({
  width: 6,
  height: 6,
  borderRadius: "50%" as const,
  background: "#14b8a6",
});

/** Allowed file extensions for upload */
export const ALLOWED_UPLOAD_EXTENSIONS = new Set([
  ".mp4",
  ".webm",
  ".mov",
  ".avi",
  ".mkv",
  ".m4v",
  ".3gp",
  ".mp3",
  ".wav",
  ".m4a",
  ".ogg",
  ".flac",
  ".aac",
  ".wma",
  ".opus",
  ".pdf",
  ".ppt",
  ".pptx",
  ".doc",
  ".docx",
  ".xls",
  ".xlsx",
  ".csv",
  ".txt",
  ".md",
  ".json",
  ".xml",
  ".html",
  ".png",
  ".jpg",
  ".jpeg",
  ".gif",
  ".bmp",
  ".webp",
  ".svg",
  ".tiff",
  ".tif",
]);

export const LAYOUT = {
  PAGE_MAX_WIDTH: 1200,
  HEADER_HEIGHT: 72,
} as const;

export { MEETING_TYPE_CONFIG as TYPE_CONFIG, MEETING_STATUS_CONFIG as STATUS_CONFIG };
