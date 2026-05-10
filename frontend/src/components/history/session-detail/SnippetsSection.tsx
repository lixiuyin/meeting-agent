import type { SessionSearchResult } from "../../../api/client";

export function SnippetsSection({ snippets }: { snippets: SessionSearchResult[] }) {
  return (
    <div style={{ marginBottom: 16 }}>
      <div
        style={{
          fontSize: 13,
          fontWeight: 600,
          color: "var(--color-text-secondary)",
          marginBottom: 8,
        }}
      >
        Matching content
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {snippets.map((s, idx) =>
          s.content ? (
            <div
              key={`${s.type}-${s.content.slice(0, 40)}-${idx}`}
              style={{
                padding: "10px 12px",
                borderRadius: 10,
                background: "var(--color-bg-surface)",
                border: "1px solid var(--color-border)",
                fontSize: 13,
                color: "var(--color-text-secondary)",
              }}
            >
              <span
                style={{
                  fontSize: 11,
                  color: "var(--color-primary)",
                  fontWeight: 600,
                  textTransform: "capitalize" as const,
                  marginRight: 8,
                }}
              >
                {s.type}
              </span>
              {s.content}
            </div>
          ) : null,
        )}
      </div>
    </div>
  );
}
