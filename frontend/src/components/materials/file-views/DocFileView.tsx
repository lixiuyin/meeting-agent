/**
 * DocFileView — renders a structured page outline for PDF/PPTX documents.
 * Shows a clean, flat table-of-contents with consistent bold headings.
 * Each page heading is rendered as a summary line; clicking expands the detail.
 */
import type { ReactNode } from "react";
import { Collapse, Empty, Tag } from "antd";
import { FileTextOutlined } from "@ant-design/icons";
import ReactMarkdown from "react-markdown";
import { getMeetingAssetUrl } from "../../../api/client";
import {
  remarkPlugins,
  rehypePlugins,
  normalizeLatexMathDelimiters,
  resolveMarkdownImageSrc,
} from "../../../utils/markdown";
import PageLayoutView from "./PageLayoutView";
import type { PageItem } from "./types";

/** Render a markdown image only when src can be resolved to a real asset URL. */
function renderMarkdownImage(src: string | undefined, alt: string | undefined): ReactNode {
  const resolved = resolveMarkdownImageSrc(src, getMeetingAssetUrl);
  if (resolved) {
    return <img src={resolved} alt={alt || ""} loading="lazy" />;
  }
  return null;
}

interface DocFileViewProps {
  fileName: string;
  kind: "pdf" | "pptx";
  pages: PageItem[];
  loading?: boolean;
}

/** Strip leading `#` markers and trailing `---` from raw heading. */
function cleanHeading(raw: string): string {
  return raw
    .replace(/^#{1,6}\s*/, "")
    .replace(/\s*---+\s*$/, "")
    .trim();
}

/** Extract a one-line summary from page text (first meaningful line). */
function extractPreview(text: string): string {
  const lines = text.split("\n");
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || /^#{1,6}\s/.test(trimmed) || /^---+$/.test(trimmed) || /^!\[/.test(trimmed)) {
      continue;
    }
    return trimmed.length > 150 ? trimmed.slice(0, 147) + "..." : trimmed;
  }
  return "";
}

/** Deduplicate consecutive pages that share the same cleaned heading. */
function dedupPages(pages: PageItem[]): PageItem[] {
  const result: PageItem[] = [];
  let lastHeading = "";
  for (const page of pages) {
    const h = page.heading ? cleanHeading(page.heading) : "";
    if (h && h === lastHeading) {
      continue;
    }
    lastHeading = h;
    result.push(page);
  }
  return result;
}

export default function DocFileView({ fileName, kind, pages, loading }: DocFileViewProps) {
  if (loading) {
    return <div style={{ textAlign: "center", padding: 40 }}>Loading pages...</div>;
  }

  if (pages.length === 0) {
    return <Empty description="No page content available" />;
  }

  const label = kind === "pptx" ? "Slide" : "Page";
  const uniquePages = dedupPages(pages);

  const items = uniquePages.map((page) => {
    const headingText = page.heading ? cleanHeading(page.heading) : null;
    const preview = extractPreview(page.text);

    return {
      key: String(page.page_num),
      label: (
        <div
          style={{
            display: "flex",
            alignItems: "flex-start",
            gap: 8,
            lineHeight: 1.6,
          }}
        >
          <Tag
            style={{
              fontSize: 10,
              borderRadius: 10,
              padding: "0 8px",
              lineHeight: "18px",
              marginTop: 2,
              flexShrink: 0,
            }}
            color="blue"
          >
            {label} {page.page_num}
          </Tag>
          <div style={{ flex: 1, minWidth: 0 }}>
            {headingText ? (
              <span
                style={{
                  fontSize: 13,
                  fontWeight: 600,
                  color: "var(--color-text-primary)",
                }}
              >
                <ReactMarkdown
                  remarkPlugins={remarkPlugins}
                  rehypePlugins={rehypePlugins}
                  components={{
                    p: ({ children }) => <span>{children}</span>,
                    strong: ({ children }) => <span>{children}</span>,
                    em: ({ children }) => <span style={{ fontStyle: "italic" }}>{children}</span>,
                    img: ({ src, alt }) => renderMarkdownImage(src, alt),
                  }}
                >
                  {normalizeLatexMathDelimiters(headingText)}
                </ReactMarkdown>
              </span>
            ) : preview ? (
              <span
                style={{
                  fontSize: 12,
                  color: "var(--color-text-secondary)",
                  fontWeight: 600,
                }}
              >
                <ReactMarkdown
                  remarkPlugins={remarkPlugins}
                  rehypePlugins={rehypePlugins}
                  components={{
                    p: ({ children }) => <span>{children}</span>,
                    strong: ({ children }) => <span>{children}</span>,
                    img: ({ src, alt }) => renderMarkdownImage(src, alt),
                  }}
                >
                  {normalizeLatexMathDelimiters(preview)}
                </ReactMarkdown>
              </span>
            ) : (
              <span style={{ fontSize: 12, color: "var(--color-text-muted)" }}>
                Empty {label.toLowerCase()}
              </span>
            )}
          </div>
        </div>
      ),
      children: (
        <PageLayoutView
          pageNum={page.page_num}
          heading={page.heading}
          text={page.text}
          imageAssets={page.image_assets ?? []}
          label={label}
          variant="materials"
        />
      ),
    };
  });

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginBottom: 4,
        }}
      >
        <FileTextOutlined style={{ color: "var(--color-text-muted)" }} />
        <span style={{ fontSize: 13, color: "var(--color-text-secondary)" }}>
          {fileName} &middot; {pages.length}{" "}
          {pages.length === 1 ? label.toLowerCase() : label.toLowerCase() + "s"}
        </span>
      </div>
      <Collapse items={items} size="small" style={{ maxHeight: 500, overflow: "auto" }} />
    </div>
  );
}
