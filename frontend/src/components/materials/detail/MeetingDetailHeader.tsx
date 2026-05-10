import { Button, Dropdown, Tag, Tooltip } from "antd";
import { formatLocalTime } from "../../../utils/time";
import { EditOutlined, FileTextOutlined } from "@ant-design/icons";
import type { ReactNode } from "react";
import { STATUS_CONFIG, TYPE_CONFIG } from "../../../constants";

interface Props {
  title: string;
  createdAt: string;
  status: string;
  detailFileType: string | null;
  exporting: boolean;
  onEdit: () => void;
  onExport: (format: "json" | "markdown" | "txt") => void;
}

function DropdownMenu({
  items,
  onSelect,
  loading,
}: {
  items: { key: string; label: string }[];
  onSelect: (key: string) => void;
  loading: boolean;
}) {
  return (
    <Dropdown
      menu={{
        items: items.map((item) => ({
          key: item.key,
          label: item.label,
          onClick: () => onSelect(item.key),
        })),
      }}
    >
      <Button icon={<FileTextOutlined />} loading={loading}>
        Export Meeting
      </Button>
    </Dropdown>
  );
}

export function MeetingDetailHeader({
  title,
  createdAt,
  status,
  detailFileType,
  exporting,
  onEdit,
  onExport,
}: Props) {
  const badgeType = detailFileType ?? "image";
  const typeConfig = TYPE_CONFIG[badgeType];
  const statusConfig = STATUS_CONFIG[status];

  return (
    <div style={{ display: "flex", alignItems: "flex-start", gap: 16, marginBottom: 24 }}>
      <div
        style={{
          width: 56,
          height: 56,
          borderRadius: 16,
          background: typeConfig?.gradient || "var(--gradient-primary)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: "#fff",
          fontSize: 24,
          flexShrink: 0,
          boxShadow: "var(--shadow-md)",
        }}
      >
        {(typeConfig?.icon as ReactNode) || <FileTextOutlined />}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
          <h2
            style={{
              margin: 0,
              fontSize: 20,
              fontWeight: 700,
              color: "var(--color-text-primary)",
            }}
          >
            {title}
          </h2>
          <Tooltip title="Edit meeting info">
            <Button
              type="text"
              size="small"
              icon={<EditOutlined />}
              onClick={onEdit}
              aria-label="Edit meeting info"
            />
          </Tooltip>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <Tag
            style={{
              borderRadius: 20,
              background: typeConfig?.bg || "var(--color-bg-muted)",
              color: typeConfig?.color || "var(--color-text-secondary)",
              border: "none",
              fontWeight: 500,
            }}
          >
            {badgeType.toUpperCase()}
          </Tag>
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              padding: "4px 10px",
              borderRadius: 20,
              background: statusConfig?.bg || "var(--color-bg-muted)",
              color: statusConfig?.color || "var(--color-text-secondary)",
              fontSize: 12,
              fontWeight: 500,
            }}
          >
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: "50%",
                background: statusConfig?.dot || "#6b7280",
              }}
            />
            {statusConfig?.label || status}
          </span>
          <span style={{ fontSize: 13, color: "var(--color-text-muted)" }}>
            {formatLocalTime(createdAt)}
          </span>
        </div>
      </div>
      <DropdownMenu
        items={[
          { key: "json", label: "Export as JSON" },
          { key: "markdown", label: "Export as Markdown" },
          { key: "txt", label: "Export as Text" },
        ]}
        onSelect={(key) => onExport(key as "json" | "markdown" | "txt")}
        loading={exporting}
      />
    </div>
  );
}
