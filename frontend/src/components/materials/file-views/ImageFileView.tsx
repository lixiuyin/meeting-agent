/**
 * ImageFileView — split view for image inspection.
 * Left: original image. Right: VLM and OCR descriptions only.
 */
import ReactMarkdown from "react-markdown";
import {
  remarkPlugins,
  rehypePlugins,
  normalizeLatexMathDelimiters,
} from "../../../utils/markdown";

interface ImageFileViewProps {
  fileUrl: string;
  fileName: string;
  captions: Array<{ caption: string | null; ocr_text: string | null }>;
}

export default function ImageFileView({ fileUrl, fileName, captions }: ImageFileViewProps) {
  const normalizedCaptions = captions
    .map((item, index) => {
      const caption = item.caption?.trim() || "";
      const ocr = item.ocr_text?.trim() || "";
      const hasCaption = caption.length > 0;
      const hasOcr = ocr.length > 0;

      if (!hasCaption && !hasOcr) return null;

      return {
        key: `${index}-${caption}-${ocr}`,
        caption: hasCaption ? caption : null,
        ocr: hasCaption && hasOcr ? ocr : null,
        merged: !hasCaption && hasOcr ? ocr : null,
      };
    })
    .filter((item): item is NonNullable<typeof item> => item !== null);

  const hasDescriptions = normalizedCaptions.length > 0;

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 420px), 1fr))",
        gap: 12,
      }}
    >
      <div
        style={{
          border: "1px solid var(--color-border)",
          borderRadius: 8,
          overflow: "hidden",
          background: "var(--color-bg-elevated)",
        }}
      >
        <div
          style={{
            padding: "8px 12px",
            borderBottom: "1px solid var(--color-border)",
            background: "var(--color-bg-muted)",
            fontSize: 12,
            color: "var(--color-text-secondary)",
            fontWeight: 600,
          }}
        >
          Original Image
        </div>
        <div
          style={{
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            padding: 12,
            minHeight: 320,
            background: "var(--color-bg-muted)",
          }}
        >
          <img
            src={fileUrl}
            alt={fileName}
            loading="lazy"
            style={{
              maxWidth: "100%",
              maxHeight: 560,
              objectFit: "contain",
              borderRadius: 6,
            }}
          />
        </div>
      </div>

      <div
        style={{
          border: "1px solid var(--color-border)",
          borderRadius: 8,
          background: "var(--color-bg-elevated)",
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
        }}
      >
        <div
          style={{
            padding: "8px 12px",
            borderBottom: "1px solid var(--color-border)",
            background: "var(--color-bg-muted)",
            fontSize: 12,
            color: "var(--color-text-secondary)",
            fontWeight: 600,
          }}
        >
          VLM & OCR Descriptions
        </div>
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 10,
            padding: 10,
            maxHeight: 620,
            overflow: "auto",
          }}
        >
          {hasDescriptions ? (
            normalizedCaptions.map((item, index) => (
              <div
                key={item.key}
                style={{
                  border: "1px solid var(--color-border)",
                  borderRadius: 8,
                  padding: 10,
                  background: "var(--color-bg-muted)",
                }}
              >
                <div
                  style={{
                    fontSize: 11,
                    fontWeight: 700,
                    color: "var(--color-text-secondary)",
                    marginBottom: 6,
                  }}
                >
                  Result {index + 1}
                </div>
                {item.caption ? (
                  <div className="markdown-body" style={{ fontSize: 13, lineHeight: 1.6 }}>
                    <ReactMarkdown
                      remarkPlugins={remarkPlugins}
                      rehypePlugins={rehypePlugins}
                      components={{ img: () => null }}
                    >
                      {normalizeLatexMathDelimiters(item.caption)}
                    </ReactMarkdown>
                  </div>
                ) : null}
                {item.ocr ? (
                  <div
                    style={{
                      marginTop: 8,
                      paddingTop: 8,
                      borderTop: "1px dashed var(--color-border)",
                    }}
                  >
                    <div
                      style={{
                        fontSize: 11,
                        fontWeight: 700,
                        color: "var(--color-text-secondary)",
                        marginBottom: 6,
                      }}
                    >
                      OCR
                    </div>
                    <div className="markdown-body" style={{ fontSize: 13, lineHeight: 1.6 }}>
                      <ReactMarkdown
                        remarkPlugins={remarkPlugins}
                        rehypePlugins={rehypePlugins}
                        components={{ img: () => null }}
                      >
                        {normalizeLatexMathDelimiters(item.ocr)}
                      </ReactMarkdown>
                    </div>
                  </div>
                ) : null}
                {item.merged ? (
                  <div className="markdown-body" style={{ fontSize: 13, lineHeight: 1.6 }}>
                    <ReactMarkdown
                      remarkPlugins={remarkPlugins}
                      rehypePlugins={rehypePlugins}
                      components={{ img: () => null }}
                    >
                      {normalizeLatexMathDelimiters(item.merged)}
                    </ReactMarkdown>
                  </div>
                ) : null}
              </div>
            ))
          ) : (
            <div style={{ fontSize: 13, color: "var(--color-text-muted)", fontStyle: "italic" }}>
              No parsed description available.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
