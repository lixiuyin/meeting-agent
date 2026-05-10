export default function SkeletonRow() {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "12px 14px",
        borderRadius: 12,
        background: "var(--color-bg-muted)",
      }}
    >
      <div
        className="skeleton-shimmer"
        style={{ width: 16, height: 16, borderRadius: 4, flexShrink: 0 }}
      />
      <div
        className="skeleton-shimmer"
        style={{ width: 36, height: 36, borderRadius: 10, flexShrink: 0 }}
      />
      <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 8 }}>
        <div className="skeleton-shimmer" style={{ width: "60%", height: 14 }} />
        <div className="skeleton-shimmer" style={{ width: "40%", height: 12 }} />
      </div>
      <div
        className="skeleton-shimmer"
        style={{ width: 28, height: 28, borderRadius: 6, flexShrink: 0 }}
      />
    </div>
  );
}
