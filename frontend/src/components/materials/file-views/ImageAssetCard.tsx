/**
 * ImageAssetCard — shared card for rendering an image with VLM caption + OCR text.
 * Extracted from DocFileView for reuse in ImageFileView and chat citations.
 */
import { useState } from "react";
import { PictureOutlined, FileSearchOutlined } from "@ant-design/icons";
import { getMeetingAssetUrl } from "../../../api/client";
import ReactMarkdown from "react-markdown";
import {
  remarkPlugins,
  rehypePlugins,
  normalizeLatexMathDelimiters,
} from "../../../utils/markdown";

export interface ImageAsset {
  storage_path: string;
  thumbnail_path: string | null;
  caption: string | null;
  ocr_text: string | null;
}

export interface ImageAssetCardProps {
  asset: ImageAsset;
  pageIndex?: number;
  imageIndex?: number;
  /** Custom URL resolver — defaults to getMeetingAssetUrl. Pass `(s) => s` to short-circuit. */
  resolveUrl?: (storagePath: string) => string;
}

export default function ImageAssetCard({
  asset,
  pageIndex = 0,
  imageIndex = 0,
  resolveUrl,
}: ImageAssetCardProps) {
  const [showOcr, setShowOcr] = useState(false);
  const imageUrl = resolveUrl
    ? resolveUrl(asset.storage_path)
    : getMeetingAssetUrl(asset.storage_path);
  const captionText = asset.caption?.trim() || "";
  const ocrText = asset.ocr_text?.trim() || "";
  const hasCaption = captionText.length > 0;
  const hasOcr = ocrText.length > 0;
  const showOcrSection = hasCaption && hasOcr;
  const hasAnyDescription = hasCaption || hasOcr;

  return (
    <div
      style={{
        border: "1px solid var(--color-border)",
        borderRadius: 8,
        overflow: "hidden",
        background: "var(--color-bg-elevated)",
      }}
    >
      {/* Original image */}
      <div
        style={{
          background: "var(--color-bg-muted)",
          display: "flex",
          justifyContent: "center",
          padding: 8,
        }}
      >
        <img
          src={imageUrl}
          alt={asset.caption || `Page ${pageIndex} image ${imageIndex + 1}`}
          style={{
            maxWidth: "100%",
            maxHeight: 400,
            objectFit: "contain",
            borderRadius: 4,
          }}
          loading="lazy"
        />
      </div>

      {/* Metadata section */}
      <div style={{ padding: "8px 12px", display: "flex", flexDirection: "column", gap: 6 }}>
        {/* VLM Caption */}
        {hasCaption ? (
          <div style={{ display: "flex", gap: 6, alignItems: "flex-start" }}>
            <PictureOutlined
              style={{
                color: "var(--color-text-muted)",
                fontSize: 12,
                marginTop: 3,
                flexShrink: 0,
              }}
            />
            <span
              className="markdown-body"
              style={{ fontSize: 12, color: "var(--color-text-primary)", lineHeight: 1.5 }}
            >
              <ReactMarkdown remarkPlugins={remarkPlugins} rehypePlugins={rehypePlugins}>
                {normalizeLatexMathDelimiters(captionText)}
              </ReactMarkdown>
            </span>
          </div>
        ) : null}

        {/* OCR Text (collapsible) */}
        {showOcrSection ? (
          <div>
            <button
              onClick={() => setShowOcr(!showOcr)}
              style={{
                background: "none",
                border: "none",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                gap: 4,
                padding: 0,
                fontSize: 12,
                color: "var(--color-text-secondary)",
              }}
            >
              <FileSearchOutlined style={{ fontSize: 12 }} />
              {showOcr ? "Hide OCR" : "Show OCR"}
              <span style={{ fontSize: 10 }}>({ocrText.length} chars)</span>
            </button>
            {showOcr && (
              <div
                className="markdown-body"
                style={{
                  marginTop: 6,
                  padding: 8,
                  background: "var(--color-bg-muted)",
                  borderRadius: 4,
                  fontSize: 11,
                  lineHeight: 1.4,
                  color: "var(--color-text-primary)",
                  maxHeight: 200,
                  overflow: "auto",
                  border: "1px solid var(--color-border)",
                }}
              >
                <ReactMarkdown
                  remarkPlugins={remarkPlugins}
                  rehypePlugins={rehypePlugins}
                  components={{ img: () => null }}
                >
                  {normalizeLatexMathDelimiters(ocrText)}
                </ReactMarkdown>
              </div>
            )}
          </div>
        ) : null}

        {!hasCaption && hasOcr ? (
          <div
            className="markdown-body"
            style={{ fontSize: 12, color: "var(--color-text-primary)" }}
          >
            <ReactMarkdown
              remarkPlugins={remarkPlugins}
              rehypePlugins={rehypePlugins}
              components={{ img: () => null }}
            >
              {normalizeLatexMathDelimiters(ocrText)}
            </ReactMarkdown>
          </div>
        ) : null}

        {!hasAnyDescription ? (
          <div style={{ fontSize: 12, color: "var(--color-text-muted)", fontStyle: "italic" }}>
            No parsed description available.
          </div>
        ) : null}
      </div>
    </div>
  );
}
