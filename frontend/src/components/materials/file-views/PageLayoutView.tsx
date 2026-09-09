/**
 * PageLayoutView — shared page layout for rendering page text + image assets.
 * Used in DocFileView (materials variant) and chat citation modal.
 */
import { useState } from "react";
import { useIntl } from "react-intl";
import ReactMarkdown from "react-markdown";
import { getMeetingAssetUrl } from "../../../api/client";
import {
  remarkPlugins,
  rehypePlugins,
  normalizeLatexMathDelimiters,
  resolveMarkdownImageSrc,
} from "../../../utils/markdown";
import ImageAssetCard, { type ImageAsset } from "./ImageAssetCard";
import { rehypeEvidenceHighlight } from "../../../utils/evidenceHighlight";

/** Render a markdown image only when src can be resolved to a real asset URL. */
function MarkdownImage({ src, alt }: { src?: string; alt?: string }) {
  const [failed, setFailed] = useState(false);
  const { formatMessage: t } = useIntl();
  const resolved = resolveMarkdownImageSrc(src, getMeetingAssetUrl);
  if (resolved && !failed) {
    return <img src={resolved} alt={alt || ""} loading="lazy" onError={() => setFailed(true)} />;
  }
  return (
    <span
      role="note"
      style={{
        display: "block",
        padding: 12,
        border: "1px dashed var(--color-border)",
        borderRadius: 8,
      }}
    >
      {t({ id: "viewer.imageUnavailable" })}
      {alt ? ` — ${alt}` : ""}
    </span>
  );
}

export interface PageLayoutViewProps {
  pageNum: number;
  heading: string | null;
  text: string;
  imageAssets: ImageAsset[];
  label?: "Page" | "Slide";
  variant?: "materials" | "modal";
  evidenceExcerpt?: string;
}

export default function PageLayoutView({
  pageNum,
  text,
  imageAssets,
  label = "Page",
  variant = "materials",
  evidenceExcerpt,
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
            rehypePlugins={[
              ...rehypePlugins,
              [rehypeEvidenceHighlight, { excerpt: evidenceExcerpt }],
            ]}
            components={{
              img: ({ src, alt }) => <MarkdownImage key={src} src={src} alt={alt} />,
              mark: ({ children }) => (
                <mark
                  data-evidence-highlight="true"
                  style={{ background: "#ffe58f", color: "#262626", borderRadius: 2 }}
                >
                  {children}
                </mark>
              ),
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
