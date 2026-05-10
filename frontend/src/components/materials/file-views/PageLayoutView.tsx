/**
 * PageLayoutView — shared page layout for rendering page text + image assets.
 * Used in DocFileView (materials variant) and chat citation modal.
 */
import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import { getMeetingAssetUrl } from "../../../api/client";
import {
  remarkPlugins,
  rehypePlugins,
  normalizeLatexMathDelimiters,
  resolveMarkdownImageSrc,
} from "../../../utils/markdown";
import ImageAssetCard, { type ImageAsset } from "./ImageAssetCard";

/** Render a markdown image only when src can be resolved to a real asset URL. */
function renderMarkdownImage(src: string | undefined, alt: string | undefined): ReactNode {
  const resolved = resolveMarkdownImageSrc(src, getMeetingAssetUrl);
  if (resolved) {
    return <img src={resolved} alt={alt || ""} loading="lazy" />;
  }
  return null;
}

export interface PageLayoutViewProps {
  pageNum: number;
  heading: string | null;
  text: string;
  imageAssets: ImageAsset[];
  label?: "Page" | "Slide";
  variant?: "materials" | "modal";
}

export default function PageLayoutView({
  pageNum,
  text,
  imageAssets,
  label = "Page",
  variant = "materials",
}: PageLayoutViewProps) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div
        className="markdown-body"
        style={{
          fontSize: 13,
          lineHeight: 1.6,
          color: "var(--color-text-primary)",
          ...(variant === "materials" ? { maxHeight: 300, overflow: "auto" } : {}),
        }}
      >
        {text ? (
          <ReactMarkdown
            remarkPlugins={remarkPlugins}
            rehypePlugins={rehypePlugins}
            components={{
              img: ({ src, alt }) => renderMarkdownImage(src, alt),
            }}
          >
            {normalizeLatexMathDelimiters(text)}
          </ReactMarkdown>
        ) : (
          <span style={{ color: "var(--color-text-muted)" }}>Empty {label.toLowerCase()}</span>
        )}
      </div>
      {imageAssets.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {imageAssets.map((asset, imageIndex) => (
            <ImageAssetCard
              key={`${asset.storage_path}-${imageIndex}`}
              asset={asset}
              pageIndex={pageNum}
              imageIndex={imageIndex}
            />
          ))}
        </div>
      )}
    </div>
  );
}
