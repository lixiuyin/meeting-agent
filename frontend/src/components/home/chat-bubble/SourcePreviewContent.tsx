import { FileSearchOutlined, PictureOutlined } from "@ant-design/icons";
import ReactMarkdown from "react-markdown";
import {
  normalizeLatexMathDelimiters,
  rehypePlugins,
  remarkPlugins,
} from "../../../utils/markdown";
import type { SourceItem } from "../../../api/client";
import { sourcePreviewSnippet } from "../../common/sourcePreview";
import { isImageDerivedSource } from "./sourceHelpers";
import { sourcePreviewImageUrl } from "./sourceLinks";

function TablePreview({ markdown }: { markdown: string }) {
  return (
    <div style={{ overflowX: "auto" }}>
      <ReactMarkdown remarkPlugins={remarkPlugins} rehypePlugins={rehypePlugins}>
        {normalizeLatexMathDelimiters(markdown)}
      </ReactMarkdown>
    </div>
  );
}

interface SourcePreviewContentProps {
  source: SourceItem;
}

export function SourcePreviewContent({ source }: SourcePreviewContentProps) {
  const preview = sourcePreviewSnippet(source);
  if (preview.tableMarkdown?.trim()) {
    return <TablePreview markdown={preview.tableMarkdown.trim()} />;
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {preview.caption && (
        <div>
          <div
            style={{
              fontSize: 11,
              color: "var(--color-text-muted)",
              marginBottom: 2,
              display: "flex",
              alignItems: "center",
              gap: 4,
            }}
          >
            <PictureOutlined style={{ fontSize: 11 }} />
            VLM Description
          </div>
          <div style={{ fontStyle: "italic", lineHeight: 1.5 }}>{preview.caption}</div>
        </div>
      )}
      {preview.ocr && (
        <div>
          <div
            style={{
              fontSize: 11,
              color: "var(--color-text-muted)",
              marginBottom: 2,
              display: "flex",
              alignItems: "center",
              gap: 4,
            }}
          >
            <FileSearchOutlined style={{ fontSize: 11 }} />
            OCR
          </div>
          <div
            style={{
              fontSize: 12,
              lineHeight: 1.4,
              fontFamily: "monospace",
              whiteSpace: "pre-wrap" as const,
            }}
          >
            {preview.ocr}
          </div>
        </div>
      )}
      {!preview.caption && !preview.ocr && <>{preview.text}</>}
    </div>
  );
}

interface FallbackSourceViewProps {
  source: SourceItem;
}

export function FallbackSourceView({ source }: FallbackSourceViewProps) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {sourcePreviewImageUrl(source) && (
        <div
          style={{
            position: "relative",
            marginBottom: 6,
            borderRadius: 6,
            overflow: "hidden",
            border: "1px solid var(--color-border)",
            background: "var(--color-bg-muted)",
          }}
        >
          <img
            src={sourcePreviewImageUrl(source)!}
            alt="Source preview"
            style={{
              width: "100%",
              maxHeight: isImageDerivedSource(source) ? 400 : 200,
              objectFit: "contain",
              display: "block",
            }}
          />
        </div>
      )}
      <SourcePreviewContent source={source} />
    </div>
  );
}
