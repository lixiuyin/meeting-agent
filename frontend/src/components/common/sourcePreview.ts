import type { SourceItem } from "../../api/client";

const OCR_PREVIEW_MAX_CHARS = 320;
const TEXT_PREVIEW_MAX_CHARS = 500;

const normalizeText = (value: string) => value.replace(/\s+/g, " ").trim();

const truncateWithEllipsis = (value: string, maxChars: number) => {
  if (value.length <= maxChars) return value;
  return `${value.slice(0, maxChars - 1).trimEnd()}…`;
};

const parseCombinedImageContent = (content: string) => {
  const captionMatch = content.match(/\[Caption\]\s*([\s\S]*?)(?=\n\s*\[OCR\]|\r\n\s*\[OCR\]|$)/i);
  const ocrMatch = content.match(/\[OCR\]\s*([\s\S]*)$/i);
  return {
    caption: captionMatch?.[1] ? normalizeText(captionMatch[1]) : "",
    ocr: ocrMatch?.[1] ? normalizeText(ocrMatch[1]) : "",
  };
};

export interface SourcePreviewSnippet {
  title?: string;
  text: string;
  tableMarkdown?: string;
  caption?: string;
  ocr?: string;
}

export const sourcePreviewImageUrl = (
  source: SourceItem,
  assetUrlFor: (path: string) => string,
): string | null => {
  const path =
    source.page_image_thumbnail_path ||
    source.page_image_path ||
    source.image_thumbnail_path ||
    source.image_path;
  return path ? assetUrlFor(path) : null;
};

export const sourcePrimaryImageUrl = (
  source: SourceItem,
  assetUrlFor: (path: string) => string,
): string | null => {
  const path = source.image_thumbnail_path || source.image_path;
  return path ? assetUrlFor(path) : null;
};

export const sourcePreviewSnippet = (source: SourceItem): SourcePreviewSnippet => {
  if (source.content_type === "table" && source.table_markdown?.trim()) {
    return {
      title: "Table content",
      text: source.table_markdown.trim(),
      tableMarkdown: source.table_markdown,
    };
  }

  if (source.content_type === "image_caption") {
    const caption = normalizeText(source.image_caption || source.content || "");
    const ocr = truncateWithEllipsis(normalizeText(source.image_ocr || ""), OCR_PREVIEW_MAX_CHARS);
    const text =
      [caption ? `Caption: ${caption}` : "", ocr ? `OCR: ${ocr}` : ""].filter(Boolean).join("\n") ||
      "(No snippet)";
    return { title: "Image context", text, caption: caption || undefined, ocr: ocr || undefined };
  }

  if (source.content_type === "image_ocr") {
    const ocr = truncateWithEllipsis(
      normalizeText(source.image_ocr || source.content || ""),
      OCR_PREVIEW_MAX_CHARS,
    );
    const caption = normalizeText(source.image_caption || "");
    const text =
      [caption ? `Caption: ${caption}` : "", ocr ? `OCR: ${ocr}` : ""].filter(Boolean).join("\n") ||
      "(No snippet)";
    return { title: "Image context", text, caption: caption || undefined, ocr: ocr || undefined };
  }

  if (source.content_type === "image_combined") {
    const parsed = parseCombinedImageContent(source.content || "");
    const caption = parsed.caption || normalizeText(source.image_caption || "");
    const ocr = truncateWithEllipsis(parsed.ocr, OCR_PREVIEW_MAX_CHARS);
    if (caption || ocr) {
      const parts = [caption ? `Caption: ${caption}` : "", ocr ? `OCR: ${ocr}` : ""].filter(
        Boolean,
      );
      return {
        title: "Image context",
        text: parts.join("\n"),
        caption: caption || undefined,
        ocr: ocr || undefined,
      };
    }
  }

  const text = truncateWithEllipsis(normalizeText(source.content || ""), TEXT_PREVIEW_MAX_CHARS);
  return { title: "Snippet", text: text || "(No snippet)" };
};
