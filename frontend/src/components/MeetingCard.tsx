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

interface MeetingCardProps {
  meeting: MeetingInfo;
  isSelected: boolean;
  isSelectionMode: boolean;
  onClick: (meeting: MeetingInfo) => void;
  onToggleSelect: (id: number, e: React.MouseEvent | React.KeyboardEvent) => void;
  onDelete?: (id: number) => void;
  onReprocess?: (id: number) => void;
}

// Pre-computed style objects to avoid recreation
const cardBaseStyle: React.CSSProperties = {
  borderRadius: 20,
  padding: "20px",
  minHeight: 140,
  display: "flex",
  flexDirection: "column",
  position: "relative",
  transition: "all 0.2s ease",
  cursor: "pointer",
};

const checkboxContainerStyle: React.CSSProperties = {
  position: "absolute",
  top: 16,
  right: 16,
  zIndex: 1,
};

const headerStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 14,
  marginBottom: 16,
};

const iconContainerStyle: React.CSSProperties = {
  width: 48,
  height: 48,
  borderRadius: 14,
  color: "#fff",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  fontSize: 20,
  boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
};

function MeetingCard({
  meeting,
  isSelected,
  isSelectionMode,
  onClick,
  onToggleSelect,
  onDelete,
  onReprocess,
}: MeetingCardProps) {
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
    onDelete?.(meeting.id);
  }, [meeting.id, onDelete]);

  const handleReprocess = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      onReprocess?.(meeting.id);
    },
    [meeting.id, onReprocess],
  );

  const cardStyle: React.CSSProperties = useMemo(
    () => ({
      ...cardBaseStyle,
      background: isSelected ? "rgba(79, 70, 229, 0.08)" : "var(--color-bg-surface)",
      border: `2px solid ${isSelected ? "var(--color-primary)" : "var(--color-border)"}`,
      boxShadow: isSelected ? "var(--glow-primary)" : "var(--shadow-sm)",
    }),
    [isSelected],
  );

  return (
    <motion.div
      layout
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      whileHover={{ y: -4, transition: { duration: 0.2 } }}
      onClick={handleClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          handleClick();
        }
      }}
      role="button"
      tabIndex={0}
      style={cardStyle}
    >
      {isSelectionMode && (
        <div
          style={checkboxContainerStyle}
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
      )}

      <div style={headerStyle}>
        <div
          style={{
            ...iconContainerStyle,
            background: typeConfig.gradient,
          }}
        >
          {typeConfig.icon}
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              fontSize: 15,
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
            {typeConfig.label} · {formatLocalTime(meeting.created_at, { dateOnly: true })}
          </div>
        </div>
      </div>

      <div
        style={{
          marginTop: "auto",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div
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
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: "50%",
                background: statusConfig.dot,
              }}
            />
            {statusConfig.label}
          </div>
          {meeting.status === "failed" && meeting.error_message && (
            <Tooltip title={meeting.error_message}>
              <ExclamationCircleOutlined style={{ color: "#ef4444", fontSize: 14 }} />
            </Tooltip>
          )}
        </div>

        {!isSelectionMode && (
          <div style={{ display: "flex", gap: 4 }}>
            {meeting.status === "failed" && (
              <Button
                size="small"
                icon={<ReloadOutlined />}
                onClick={handleReprocess}
                aria-label="Reprocess meeting"
                style={{ borderRadius: 8 }}
              />
            )}
            <Button
              size="small"
              danger
              icon={<DeleteOutlined />}
              onClick={(e) => {
                e.stopPropagation();
                handleDelete();
              }}
              aria-label="Delete meeting"
              style={{ borderRadius: 8 }}
            />
          </div>
        )}
      </div>
    </motion.div>
  );
}

export default memo(MeetingCard);
