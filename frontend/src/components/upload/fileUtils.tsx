import type { ReactNode } from "react";
import {
  AudioOutlined,
  FileImageOutlined,
  FileOutlined,
  FilePdfOutlined,
  FileTextOutlined,
  VideoCameraOutlined,
} from "@ant-design/icons";

export const MAX_FILE_SIZE_BYTES = 500 * 1024 * 1024; // 500 MB per file
export const MAX_FILES = 10;

const FILE_TYPE_COLORS: Record<string, string> = {
  pdf: "#f59e0b",
  video: "#f43f5e",
  audio: "#8b5cf6",
  image: "#3b82f6",
  ppt: "#10b981",
  doc: "#3b82f6",
  xls: "#10b981",
  csv: "#6b7280",
  txt: "#6b7280",
};

const FILE_TYPE_ICONS: Record<string, ReactNode> = {
  pdf: <FilePdfOutlined />,
  video: <VideoCameraOutlined />,
  audio: <AudioOutlined />,
  image: <FileImageOutlined />,
  ppt: <FileTextOutlined />,
  doc: <FileTextOutlined />,
  xls: <FileTextOutlined />,
  csv: <FileTextOutlined />,
  txt: <FileTextOutlined />,
  default: <FileOutlined />,
};

export function getFileIcon(filename: string) {
  const ext = filename.split(".").pop()?.toLowerCase() || "";
  if (["pdf"].includes(ext)) return { icon: FILE_TYPE_ICONS.pdf, color: FILE_TYPE_COLORS.pdf };
  if (["mp4", "mov", "avi", "mkv", "webm", "m4v", "3gp"].includes(ext))
    return { icon: FILE_TYPE_ICONS.video, color: FILE_TYPE_COLORS.video };
  if (["mp3", "wav", "aac", "flac", "m4a", "ogg", "wma", "opus"].includes(ext))
    return { icon: FILE_TYPE_ICONS.audio, color: FILE_TYPE_COLORS.audio };
  if (["png", "jpg", "jpeg", "gif", "bmp", "webp", "tiff"].includes(ext))
    return { icon: FILE_TYPE_ICONS.image, color: FILE_TYPE_COLORS.image };
  if (["ppt", "pptx"].includes(ext))
    return { icon: FILE_TYPE_ICONS.ppt, color: FILE_TYPE_COLORS.ppt };
  if (["doc", "docx"].includes(ext))
    return { icon: FILE_TYPE_ICONS.doc, color: FILE_TYPE_COLORS.doc };
  if (["xls", "xlsx"].includes(ext))
    return { icon: FILE_TYPE_ICONS.xls, color: FILE_TYPE_COLORS.xls };
  if (["csv"].includes(ext)) return { icon: FILE_TYPE_ICONS.csv, color: FILE_TYPE_COLORS.csv };
  if (["txt", "md", "json", "xml", "html"].includes(ext))
    return { icon: FILE_TYPE_ICONS.txt, color: FILE_TYPE_COLORS.txt };
  return { icon: FILE_TYPE_ICONS.default, color: "#6b7280" };
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

export type PreviewMode = "image" | "video" | "pdf" | null;

export function getPreviewMode(fileName: string): PreviewMode {
  const ext = fileName.split(".").pop()?.toLowerCase() || "";
  if (["png", "jpg", "jpeg", "gif", "bmp", "webp", "tiff"].includes(ext)) return "image";
  if (["mp4", "mov", "avi", "mkv", "webm", "m4v", "3gp"].includes(ext)) return "video";
  if (ext === "pdf") return "pdf";
  return null;
}

export function createPreviewUrl(file: File): string | null {
  if (typeof URL.createObjectURL !== "function") return null;
  return URL.createObjectURL(file);
}

export function revokePreviewUrl(url?: string): void {
  if (!url || typeof URL.revokeObjectURL !== "function") return;
  URL.revokeObjectURL(url);
}
