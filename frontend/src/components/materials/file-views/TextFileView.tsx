/**
 * TextFileView — markdown-friendly text viewer with KaTeX support.
 */
import { Empty } from "antd";
import ReactMarkdown from "react-markdown";
import {
  remarkPlugins,
  rehypePlugins,
  normalizeLatexMathDelimiters,
} from "../../../utils/markdown";

interface TextFileViewProps {
  fileName: string;
  text: string;
  wordCount: number;
}

export default function TextFileView({ fileName, text, wordCount }: TextFileViewProps) {
  if (!text) {
    return <Empty description="No text content available" />;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginBottom: 4,
          fontSize: 13,
          color: "var(--color-text-secondary)",
        }}
      >
        <span>{fileName}</span>
        <span>&middot;</span>
        <span>{wordCount} words</span>
      </div>
      <div
        style={{
          padding: 12,
          borderRadius: 8,
          background: "var(--color-bg-muted)",
          fontSize: 13,
          lineHeight: 1.6,
          color: "var(--color-text-primary)",
          maxHeight: 560,
          overflow: "auto",
          border: "1px solid var(--color-border)",
        }}
      >
        <div className="markdown-body">
          <ReactMarkdown remarkPlugins={remarkPlugins} rehypePlugins={rehypePlugins}>
            {normalizeLatexMathDelimiters(text)}
          </ReactMarkdown>
        </div>
      </div>
    </div>
  );
}
