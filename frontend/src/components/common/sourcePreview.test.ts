import { describe, expect, it } from "vitest";
import type { SourceItem } from "../../api/client";
import {
  sourcePreviewImageUrl,
  sourcePreviewSnippet,
  sourcePrimaryImageUrl,
} from "./sourcePreview";

const baseSource = (): SourceItem => ({
  meeting_id: 1,
  meeting_title: "Meeting",
  content: "",
  score: 0.9,
  file_id: 1,
  file_name: "doc.pdf",
  file_type: "pdf",
  chunk_index: 1,
  page_number: 1,
  timestamp_start: null,
  timestamp_end: null,
  speaker: null,
  source_kind: "page",
});

describe("sourcePreviewSnippet", () => {
  it("formats image_combined with caption first and condensed OCR", () => {
    const preview = sourcePreviewSnippet({
      ...baseSource(),
      content_type: "image_combined",
      content: "[Caption] Team roadmap\n[OCR]  Q1   deliverables\n  Q2   migration ",
    });

    expect(preview.caption).toBe("Team roadmap");
    expect(preview.ocr).toBe("Q1 deliverables Q2 migration");
  });

  it("truncates long OCR safely", () => {
    const preview = sourcePreviewSnippet({
      ...baseSource(),
      content_type: "image_ocr",
      content: "word ".repeat(120),
    });

    expect(preview.ocr).toBeDefined();
    expect(preview.ocr!.length).toBeLessThanOrEqual(320);
    expect(preview.ocr!.endsWith("…")).toBe(true);
  });

  it("uses table markdown as the primary preview for table sources", () => {
    const preview = sourcePreviewSnippet({
      ...baseSource(),
      content_type: "table",
      table_markdown: "| Quarter | Revenue |\n|---|---|\n| Q1 | 120 |",
      content: "fallback",
    });

    expect(preview.title).toBe("Table content");
    expect(preview.text).toContain("| Quarter | Revenue |");
    expect(preview.tableMarkdown).toContain("| Q1 | 120 |");
  });

  it("falls back to image_caption when combined caption block is missing", () => {
    const preview = sourcePreviewSnippet({
      ...baseSource(),
      content_type: "image_combined",
      image_caption: "Product dashboard screenshot",
      content: "[OCR] Gross margin +12% YoY",
    });

    expect(preview.title).toBe("Image context");
    expect(preview.caption).toBe("Product dashboard screenshot");
    expect(preview.ocr).toBe("Gross margin +12% YoY");
    expect(preview.text).toContain("Caption: Product dashboard screenshot");
  });
});

describe("sourcePreviewImageUrl", () => {
  it("prefers page image preview over asset thumbnail", () => {
    const source = {
      ...baseSource(),
      image_thumbnail_path: "assets/image-thumb.webp",
      page_image_thumbnail_path: "assets/page-thumb.webp",
    };
    const url = sourcePreviewImageUrl(source, (path) => `/api/${path}`);
    expect(url).toBe("/api/assets/page-thumb.webp");
  });
});

describe("sourcePrimaryImageUrl", () => {
  it("prefers image thumbnail and falls back to image path", () => {
    const source = {
      ...baseSource(),
      image_thumbnail_path: "assets/image-thumb.webp",
      image_path: "assets/image.png",
    };
    const url = sourcePrimaryImageUrl(source, (path) => `/api/${path}`);
    expect(url).toBe("/api/assets/image-thumb.webp");
  });
});
