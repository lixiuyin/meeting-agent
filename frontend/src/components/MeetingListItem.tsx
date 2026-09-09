import { memo, useCallback, useMemo } from "react";
import { Checkbox, Button, Tooltip } from "antd";
import { DeleteOutlined, ReloadOutlined, ExclamationCircleOutlined } from "@ant-design/icons";
import { motion } from "framer-motion";
import type { MeetingInfo } from "../api/client";
import {
  MEETING_TYPE_CONFIG as TYPE_CONFIG,
  MEETING_STATUS_CONFIG as STATUS_CONFIG,
  STATUS_CONFIG_FALLBACK,
} from "../constants";
import { formatLocalTime } from "../utils/time";

interface MeetingListItemProps {
  meeting: MeetingInfo;
  isSelected: boolean;
  isSelectionMode: boolean;
  onClick: (meeting: MeetingInfo) => void;
  onToggleSelect: (id: number, e: React.MouseEvent | React.KeyboardEvent) => void;
  onDelete: (id: number) => void;
  onReprocess: (id: number) => void;
}

// Pre-computed styles
const containerBaseStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 16,
  padding: "16px 20px",
  borderRadius: 12,
  transition: "all 0.2s ease",
  cursor: "pointer",
  position: "relative",
};

const iconContainerStyle: React.CSSProperties = {
  width: 40,
  height: 40,
  borderRadius: 10,
  color: "#fff",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  fontSize: 16,
  flexShrink: 0,
};

function MeetingListItem({
  meeting,
  isSelected,
  isSelectionMode,
  onClick,
  onToggleSelect,
  onDelete,
  onReprocess,
}: MeetingListItemProps) {
  const typeKey =
    meeting.file_types && meeting.file_types.length > 1 ? "mixed" : (meeting.file_type ?? "txt");
  const typeConfig = TYPE_CONFIG[typeKey] || TYPE_CONFIG.txt;
  const statusConfig = STATUS_CONFIG[meeting.status] ?? STATUS_CONFIG_FALLBACK;

  const handleClick = useCallback(() => {
    onClick(meeting);
  }, [meeting, onClick]);

  const handleCheckboxClick = useCallback(
    (e: React.MouseEvent) => {
      onToggleSelect(meeting.id, e);
    },
    [meeting.id, onToggleSelect],
  );

  const handleDelete = useCallback(() => {
    onDelete(meeting.id);
  }, [meeting.id, onDelete]);

  const handleReprocess = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      onReprocess(meeting.id);
    },
    [meeting.id, onReprocess],
  );

  const containerStyle: React.CSSProperties = useMemo(
    () => ({
      ...containerBaseStyle,
      background: isSelected ? "rgba(79, 70, 229, 0.08)" : "var(--color-bg-surface)",
      border: `1px solid ${isSelected ? "var(--color-primary)" : "var(--color-border)"}`,
    }),
    [isSelected],
  );

  return (
    <motion.div
      layout
      initial={{ opacity: 0, x: -20 }}
      animate={{ opacity: 1, x: 0 }}
      style={containerStyle}
    >
      <button
        type="button"
        className="card-primary-action"
        aria-label={`Open meeting ${meeting.title}`}
        onClick={handleClick}
      />
      {isSelectionMode ? (
        <div
          className="card-interactive-control"
          onClick={handleCheckboxClick}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              onToggleSelect(meeting.id, e);
            }
          }}
          role="checkbox"
          aria-checked={isSelected}
          tabIndex={0}
        >
          <Checkbox checked={isSelected} />
        </div>
      ) : (
        <div style={{ ...iconContainerStyle, background: typeConfig.gradient }}>
          {typeConfig.icon}
        </div>
      )}

      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontSize: 14,
            fontWeight: 600,
            color: "var(--color-text-primary)",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
          title={meeting.title}
        >
          {meeting.title}
        </div>
        <div
          style={{
            fontSize: 12,
            color: "var(--color-text-tertiary)",
            marginTop: 2,
          }}
        >
          {typeConfig.label} ·{" "}
          {meeting.file_types && meeting.file_types.length > 1
            ? `${meeting.file_types.length} formats`
            : meeting.file_name}
        </div>
      </div>

      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              padding: "4px 10px",
              borderRadius: 20,
              background: statusConfig.bg,
              color: statusConfig.color,
              fontSize: 12,
              fontWeight: 500,
            }}
          >
            {statusConfig.label}
          </span>
          {meeting.status === "failed" && meeting.error_message && (
            <Tooltip title={meeting.error_message}>
              <ExclamationCircleOutlined style={{ color: "#ef4444", fontSize: 14 }} />
            </Tooltip>
          )}
        </div>

        <span
          style={{
            fontSize: 12,
            color: "var(--color-text-tertiary)",
            whiteSpace: "nowrap",
          }}
        >
          {formatLocalTime(meeting.created_at, { dateOnly: true })}
        </span>

        {!isSelectionMode && (
          <div
            className="card-interactive-control"
            style={{ display: "flex", gap: 8 }}
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => e.stopPropagation()}
            role="toolbar"
          >
            {meeting.status === "failed" && (
              <Button size="small" icon={<ReloadOutlined />} onClick={handleReprocess}>
                Retry
              </Button>
            )}
            <Button
              size="small"
              danger
              icon={<DeleteOutlined />}
              aria-label="Delete meeting"
              onClick={(e) => {
                e.stopPropagation();
                handleDelete();
              }}
            />
          </div>
        )}
      </div>
    </motion.div>
  );
}

export default memo(MeetingListItem);
