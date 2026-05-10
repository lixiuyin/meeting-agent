/**
 * FileKind capability table — mirrors backend FileKindSpec.
 *
 * Replaces scattered `if (file_type === "video")` checks with
 * a single lookup table so conditional rendering is data-driven.
 */

export type FileKind = "video" | "audio" | "pdf" | "pptx" | "doc" | "xls" | "csv" | "txt" | "image";

export interface FileKindCapabilities {
  readonly kind: FileKind;
  readonly hasTimeline: boolean;
  readonly hasPages: boolean;
  readonly hasImages: boolean;
  readonly viewerHint: "video" | "audio" | "pdf" | "slides" | "image" | "text";
}

const KIND_CAPABILITIES: Record<FileKind, FileKindCapabilities> = {
  video: {
    kind: "video",
    hasTimeline: true,
    hasPages: false,
    hasImages: false,
    viewerHint: "video",
  },
  audio: {
    kind: "audio",
    hasTimeline: true,
    hasPages: false,
    hasImages: false,
    viewerHint: "audio",
  },
  pdf: {
    kind: "pdf",
    hasTimeline: false,
    hasPages: true,
    hasImages: true,
    viewerHint: "pdf",
  },
  pptx: {
    kind: "pptx",
    hasTimeline: false,
    hasPages: true,
    hasImages: true,
    viewerHint: "slides",
  },
  doc: {
    kind: "doc",
    hasTimeline: false,
    hasPages: false,
    hasImages: false,
    viewerHint: "text",
  },
  xls: {
    kind: "xls",
    hasTimeline: false,
    hasPages: false,
    hasImages: false,
    viewerHint: "text",
  },
  csv: {
    kind: "csv",
    hasTimeline: false,
    hasPages: false,
    hasImages: false,
    viewerHint: "text",
  },
  txt: {
    kind: "txt",
    hasTimeline: false,
    hasPages: false,
    hasImages: false,
    viewerHint: "text",
  },
  image: {
    kind: "image",
    hasTimeline: false,
    hasPages: false,
    hasImages: true,
    viewerHint: "image",
  },
};

/** Map of legacy file_type values from the API to their canonical kind. */
const FILE_TYPE_TO_KIND: Record<string, FileKind> = {
  video: "video",
  audio: "audio",
  pdf: "pdf",
  ppt: "pptx",
  pptx: "pptx",
  doc: "doc",
  docx: "doc",
  xls: "xls",
  xlsx: "xls",
  csv: "csv",
  txt: "txt",
  image: "image",
};

/**
 * Resolve a file_type string from the API into its capability table entry.
 * Falls back to "txt" for unknown types.
 */
export function getKindCapabilities(fileType: string | null | undefined): FileKindCapabilities {
  const kind = FILE_TYPE_TO_KIND[fileType ?? ""] ?? "txt";
  return KIND_CAPABILITIES[kind];
}

/**
 * Check whether a file type supports timeline/segments.
 */
export function supportsTimeline(fileType: string | null | undefined): boolean {
  return getKindCapabilities(fileType).hasTimeline;
}

/**
 * Check whether a file type has pages/slides.
 */
export function supportsPages(fileType: string | null | undefined): boolean {
  return getKindCapabilities(fileType).hasPages;
}
