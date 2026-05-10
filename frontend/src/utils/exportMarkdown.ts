import type { FileTimelineResponse } from "../api/client";
import { normalizeLatexMathDelimiters } from "./markdown";

export interface MarkdownArchiveAsset {
  sourceUrl: string;
  archivePath: string;
}

interface BuildPerFileMarkdownInput {
  meetingTitle: string;
  meetingId: number;
  file: {
    id: number;
    file_name: string;
    file_type: string;
    summary?: string | null;
    status?: string;
  };
  timeline: FileTimelineResponse | null;
  resolveAssetUrl: (storagePath: string) => string;
  resolveFileUrl: (meetingId: number, fileId: number) => string;
  usedAssetPaths?: Map<string, number>;
}

const MARKDOWN_IMAGE_RE = /!\[([^\]]*)\]\(([^)]+)\)/g;
const HTML_IMAGE_RE = /<img(\s+[^>]*?)src=(['"])(.*?)\2([^>]*)>/gi;

/** Storage-path prefixes that indicate a resolvable asset. */
const _RESOLVABLE_PREFIXES = [
  "meeting_assets/",
  "/meeting_assets/",
  "uploads/meeting_assets/",
  "/uploads/meeting_assets/",
];

/** Check whether a raw markdown image src can be resolved to a valid URL. */
function isResolvableImageSrc(src: string): boolean {
  const trimmed = src.trim();
  if (!trimmed) return false;
  const lower = trimmed.toLowerCase();
  if (
    lower.startsWith("data:") ||
    lower.startsWith("blob:") ||
    lower.startsWith("http://") ||
    lower.startsWith("https://")
  )
    return true;
  // Check storage-path prefixes
  const noHash = trimmed.split("#", 1)[0];
  const noQuery = noHash.split("?", 1)[0];
  return _RESOLVABLE_PREFIXES.some((p) => noQuery.startsWith(p));
}

/** Convert unresolvable markdown/HTML images to visible alt text.
 *
 * Parser output may embed VLM descriptions and OCR content as image alt text
 * (e.g. `![VLM description](images/fig.png)`). Instead of removing these
 * entirely, we convert them to italic text so the VLM/OCR content is preserved.
 */
const stripUnresolvableImages = (text: string): string => {
  const cleanedMd = text.replace(MARKDOWN_IMAGE_RE, (full, alt: string, rawUrl: string) => {
    if (isResolvableImageSrc(rawUrl)) return full;
    // Preserve meaningful alt text (VLM descriptions, OCR content) as italic text
    const altText = alt.trim();
    return altText ? `*${altText}*` : "";
  });
  return cleanedMd.replace(
    HTML_IMAGE_RE,
    (full, _before: string, _quote: string, rawUrl: string) => {
      if (isResolvableImageSrc(rawUrl)) return full;
      return "";
    },
  );
};

const formatTimestamp = (seconds: number) => {
  const m = Math.floor(seconds / 60)
    .toString()
    .padStart(2, "0");
  const s = Math.floor(seconds % 60)
    .toString()
    .padStart(2, "0");
  return `${m}:${s}`;
};

export const sanitizeMarkdownFileName = (fileName: string) =>
  fileName
    .replace(/[/\\:*?"<>|]+/g, "_")
    .replace(/\.[A-Za-z0-9]+$/, "")
    .trim() || "meeting-file";

export const normalizeLatexToMarkdownMath = normalizeLatexMathDelimiters;

export const triggerBlobDownload = (fileName: string, content: Blob): void => {
  const url = URL.createObjectURL(content);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fileName;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};

const buildArchiveAssetPath = (
  storagePath: string,
  usedPaths: Map<string, number>,
  assetDir = "assets",
): string => {
  const fileName = storagePath.split("/").pop() || "asset";
  const basePath = `${assetDir}/${fileName}`;
  const seen = usedPaths.get(basePath) ?? 0;
  usedPaths.set(basePath, seen + 1);
  if (seen === 0) return basePath;
  const dot = basePath.lastIndexOf(".");
  if (dot <= `${assetDir}/`.length) return `${basePath}-${seen + 1}`;
  return `${basePath.slice(0, dot)}-${seen + 1}${basePath.slice(dot)}`;
};

export const rewriteMeetingAssetLinks = (
  markdown: string,
  options?: { assetDir?: string; resolveAssetUrl?: (storagePath: string) => string },
): { markdown: string; assets: MarkdownArchiveAsset[] } => {
  const assetDir = options?.assetDir ?? "assets";
  const resolveAssetUrl = options?.resolveAssetUrl;
  const seen = new Map<string, number>();
  const sourceToArchive = new Map<string, string>();
  const assets: MarkdownArchiveAsset[] = [];

  const toStoragePath = (rawUrl: string): string | null => {
    const trimmed = rawUrl.trim();
    if (!trimmed) return null;
    const normalized = trimmed.replace(/^<|>$/g, "");
    const noHash = normalized.split("#", 1)[0];
    const noQuery = noHash.split("?", 1)[0];
    const prefixes = [
      "meeting_assets/",
      "/meeting_assets/",
      "uploads/meeting_assets/",
      "/uploads/meeting_assets/",
    ];
    for (const prefix of prefixes) {
      if (!noQuery.startsWith(prefix)) continue;
      return noQuery.replace(/^\/?uploads\//, "").replace(/^\/+/, "");
    }
    return null;
  };

  const resolveAssetInfo = (rawUrl: string): { sourceUrl: string; archivePath: string } | null => {
    const targetRaw = rawUrl.trim();
    const target = targetRaw.startsWith("<")
      ? targetRaw.slice(1, targetRaw.indexOf(">") > 0 ? targetRaw.indexOf(">") : undefined).trim()
      : targetRaw.split(/\s+/, 1)[0];
    if (!target) return null;
    const storagePath = toStoragePath(target);
    if (storagePath && resolveAssetUrl) {
      const existingArchivePath = sourceToArchive.get(storagePath);
      if (existingArchivePath) {
        return { sourceUrl: resolveAssetUrl(storagePath), archivePath: existingArchivePath };
      }
      const archivePath = buildArchiveAssetPath(storagePath, seen, assetDir);
      sourceToArchive.set(storagePath, archivePath);
      assets.push({ sourceUrl: resolveAssetUrl(storagePath), archivePath });
      return { sourceUrl: resolveAssetUrl(storagePath), archivePath };
    }
    try {
      const absolute = new URL(target, window.location.origin);
      if (!absolute.pathname.endsWith("/meetings/assets")) return null;
      const path = absolute.searchParams.get("path");
      if (!path) return null;
      const decodedPath = decodeURIComponent(path);
      const existingArchivePath = sourceToArchive.get(decodedPath);
      if (existingArchivePath) {
        return { sourceUrl: absolute.toString(), archivePath: existingArchivePath };
      }
      const archivePath = buildArchiveAssetPath(decodedPath, seen, assetDir);
      sourceToArchive.set(decodedPath, archivePath);
      assets.push({ sourceUrl: absolute.toString(), archivePath });
      return { sourceUrl: absolute.toString(), archivePath };
    } catch {
      return null;
    }
  };

  const markdownRewritten = markdown.replace(
    MARKDOWN_IMAGE_RE,
    (full, alt: string, rawUrl: string) => {
      const info = resolveAssetInfo(rawUrl);
      if (!info) return full;
      return `![${alt}](${info.archivePath})`;
    },
  );

  const htmlRewritten = markdownRewritten.replace(
    HTML_IMAGE_RE,
    (full, before: string, quote: string, rawUrl: string, after: string) => {
      const info = resolveAssetInfo(rawUrl);
      if (!info) return full;
      return `<img${before}src=${quote}${info.archivePath}${quote}${after}>`;
    },
  );

  return { markdown: htmlRewritten, assets };
};

export const buildPerFileMarkdownBundle = ({
  meetingTitle,
  meetingId,
  file,
  timeline,
  resolveAssetUrl,
  resolveFileUrl,
  usedAssetPaths: externalUsedAssetPaths,
}: BuildPerFileMarkdownInput): { markdown: string; assets: MarkdownArchiveAsset[] } => {
  const parts: string[] = [];
  const archiveAssets: MarkdownArchiveAsset[] = [];
  const usedAssetPaths = externalUsedAssetPaths ?? new Map<string, number>();
  parts.push(`# ${file.file_name}`);
  parts.push("");
  parts.push(`- Meeting: ${meetingTitle}`);
  parts.push(`- File type: ${file.file_type.toUpperCase()}`);
  parts.push(`- Exported at: ${new Date().toISOString()}`);
  parts.push("");
  parts.push("## Summary");
  parts.push("");
  parts.push(file.summary?.trim() || "Per-file summary not yet available.");
  parts.push("");

  if (!timeline) {
    parts.push("## Details");
    parts.push("");
    parts.push("_No detail data available._");
    parts.push("");
    return { markdown: normalizeLatexToMarkdownMath(parts.join("\n")), assets: archiveAssets };
  }

  if (timeline.kind === "segments") {
    parts.push("## Transcript Timestamps");
    parts.push("");
    if (timeline.segments.length === 0) {
      parts.push("_No segments available._");
      parts.push("");
    } else {
      for (const seg of timeline.segments) {
        const speakerPrefix = seg.speaker ? `**${seg.speaker}** · ` : "";
        parts.push(
          `- [${formatTimestamp(seg.start)} - ${formatTimestamp(seg.end)}] ${speakerPrefix}${seg.text}`,
        );
      }
      parts.push("");
    }
  } else if (timeline.kind === "pages") {
    parts.push("## Pages");
    parts.push("");
    if (timeline.pages.length === 0) {
      parts.push("_No pages available._");
      parts.push("");
    } else {
      for (const page of timeline.pages) {
        parts.push(`### Page ${page.page_num}${page.heading ? `: ${page.heading}` : ""}`);
        parts.push("");
        // Strip unresolvable image references from page text to prevent
        // broken ![](relative/path.png) in the exported markdown.
        const cleanText = stripUnresolvableImages(page.text || "");
        parts.push(cleanText.trim() || "_Empty page._");
        parts.push("");
        const pageAssets = page.image_assets ?? [];
        if (pageAssets.length > 0) {
          parts.push("#### Images");
          parts.push("");
          pageAssets.forEach((asset, index) => {
            const alt = (asset.caption || `Page ${page.page_num} image ${index + 1}`).replace(
              /[\r\n]+/g,
              " ",
            );
            const archivePath = buildArchiveAssetPath(asset.storage_path, usedAssetPaths);
            parts.push(`![${alt}](${archivePath})`);
            archiveAssets.push({ sourceUrl: resolveAssetUrl(asset.storage_path), archivePath });
            if (asset.caption) {
              parts.push("");
              parts.push(`**VLM Description:** ${asset.caption}`);
            }
            if (asset.ocr_text) {
              parts.push("");
              parts.push(`**OCR:** ${asset.ocr_text}`);
            }
            parts.push("");
          });
        }
      }
    }
  } else if (timeline.kind === "captions") {
    // Include the original image file in the export bundle
    const imageSourceUrl = resolveFileUrl(meetingId, file.id);
    const ext = file.file_name.includes(".") ? `.${file.file_name.split(".").pop()}` : ".png";
    const imageArchivePath = buildArchiveAssetPath(`image${ext}`, usedAssetPaths, "linked-assets");
    parts.push("## Image");
    parts.push("");
    parts.push(`![${file.file_name}](${imageArchivePath})`);
    parts.push("");
    archiveAssets.push({ sourceUrl: imageSourceUrl, archivePath: imageArchivePath });

    parts.push("## Captions / OCR");
    parts.push("");
    if (timeline.captions.length === 0) {
      parts.push("_No captions available._");
      parts.push("");
    } else {
      timeline.captions.forEach((caption, index) => {
        parts.push(`### Item ${index + 1}`);
        parts.push("");
        if (caption.caption) {
          parts.push(`**Caption**: ${caption.caption}`);
          parts.push("");
        }
        if (caption.ocr_text) {
          parts.push("**OCR Text**:");
          parts.push("");
          parts.push(caption.ocr_text);
          parts.push("");
        }
      });
    }
  } else if (timeline.kind === "text") {
    parts.push("## Text");
    parts.push("");
    parts.push(`Word count: ${timeline.word_count}`);
    parts.push("");
    parts.push(timeline.text || "_No text available._");
    parts.push("");
  }

  return { markdown: normalizeLatexToMarkdownMath(parts.join("\n")), assets: archiveAssets };
};

export async function createMarkdownZipBlob(
  markdownFileName: string,
  markdown: string,
  assets: MarkdownArchiveAsset[],
): Promise<{ blob: Blob; failedAssets: string[] }> {
  const { default: JSZip } = await import("jszip");
  const zip = new JSZip();
  zip.file(markdownFileName, markdown);
  const failedAssets: string[] = [];
  for (const asset of assets) {
    try {
      const response = await fetch(asset.sourceUrl);
      if (!response.ok) {
        failedAssets.push(asset.sourceUrl);
        continue;
      }
      const data = await response.arrayBuffer();
      zip.file(asset.archivePath, data);
    } catch {
      failedAssets.push(asset.sourceUrl);
    }
  }
  const blob = await zip.generateAsync({ type: "blob" });
  return { blob, failedAssets };
}
