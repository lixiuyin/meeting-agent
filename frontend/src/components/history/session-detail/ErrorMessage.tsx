import { Button } from "antd";

export function ErrorMessage({ message: msg, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div
      style={{
        padding: "14px 16px",
        borderRadius: 10,
        background: "rgba(239, 68, 68, 0.08)",
        border: "1px solid rgba(239, 68, 68, 0.3)",
        color: "var(--color-text-secondary)",
        marginBottom: 16,
      }}
    >
      <div style={{ marginBottom: 8 }}>{msg ?? "Failed to load messages"}</div>
      <Button size="small" onClick={onRetry}>
        Retry
      </Button>
    </div>
  );
}
