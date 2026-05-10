import ReactMarkdown from "react-markdown";
import {
  normalizeLatexMathDelimiters,
  rehypePlugins,
  remarkPlugins,
  resolveMarkdownImageSrc,
} from "../../../utils/markdown";
import { getMeetingAssetUrl, type SourceItem } from "../../../api/client";
import { sourcePreviewSnippet } from "../../common/sourcePreview";

function TablePreview({ markdown }: { markdown: string }) {
  return (
    <div style={{ overflowX: "auto" }}>
      <ReactMarkdown
        remarkPlugins={remarkPlugins}
        rehypePlugins={rehypePlugins}
        components={{
          img: ({ src, alt }) => (
            <img
              src={resolveMarkdownImageSrc(src, getMeetingAssetUrl) ?? src}
              alt={alt || ""}
              loading="lazy"
            />
          ),
        }}
      >
        {normalizeLatexMathDelimiters(markdown)}
      </ReactMarkdown>
    </div>
  );
}

export function SourcePreviewContent({ source }: { source: SourceItem }) {
  const preview = sourcePreviewSnippet(source);
  if (preview.tableMarkdown?.trim()) {
    return <TablePreview markdown={preview.tableMarkdown.trim()} />;
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {preview.caption && (
        <div>
          <div style={{ fontSize: 11, color: "var(--color-text-muted)", marginBottom: 2 }}>
            Caption
          </div>
          <div>{preview.caption}</div>
        </div>
      )}
      {preview.ocr && (
        <div>
          <div style={{ fontSize: 11, color: "var(--color-text-muted)", marginBottom: 2 }}>OCR</div>
          <div>{preview.ocr}</div>
        </div>
      )}
      {!preview.caption && !preview.ocr && <>{preview.text}</>}
    </div>
  );
}
