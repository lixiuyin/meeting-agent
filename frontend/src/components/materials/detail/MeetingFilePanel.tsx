import { useIntl } from "react-intl";
import ReactMarkdown from "react-markdown";
import {
  remarkPlugins,
  rehypePlugins,
  normalizeLatexMathDelimiters,
  resolveMarkdownImageSrc,
} from "../../../utils/markdown";
import { getMeetingAssetUrl } from "../../../api/client";
import type { DrawerFileItem } from "./types";

function FileSummaryBlock({ summaryText }: { summaryText: string }) {
  const { formatMessage } = useIntl();
  return (
    <div
      style={{
        padding: "8px 10px",
        borderRadius: 8,
        border: "1px solid var(--color-border)",
        background: "var(--color-bg-muted)",
        fontSize: 12,
        color: "var(--color-text-secondary)",
      }}
    >
      <div style={{ fontWeight: 600, marginBottom: 6 }}>
        {formatMessage({ id: "materials.file.summary" })}
      </div>
      <div className="markdown-body" style={{ fontSize: 12, lineHeight: 1.55 }}>
        <ReactMarkdown
          remarkPlugins={remarkPlugins}
          rehypePlugins={rehypePlugins}
          components={{
            img: ({ src, alt }) => {
              const resolved = resolveMarkdownImageSrc(src, getMeetingAssetUrl);
              if (resolved) return <img src={resolved} alt={alt || ""} loading="lazy" />;
              return null;
            },
          }}
        >
          {normalizeLatexMathDelimiters(summaryText)}
        </ReactMarkdown>
      </div>
    </div>
  );
}

interface Props {
  file: DrawerFileItem;
}

export function MeetingFilePanel({ file }: Props) {
  const summaryText = file.summary?.trim() || "Per-file summary not yet available";
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <FileSummaryBlock summaryText={summaryText} />
    </div>
  );
}
