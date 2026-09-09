import { Button, Collapse, Dropdown, Popconfirm, Spin, Tag, Tooltip } from "antd";
import { useIntl } from "react-intl";
import {
  DeleteOutlined,
  DownloadOutlined,
  EyeOutlined,
  FileImageOutlined,
  FilePdfOutlined,
  FileTextOutlined,
  HistoryOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  SettingOutlined,
  SyncOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { STATUS_CONFIG, TYPE_CONFIG } from "../../../constants";
import { getKindCapabilities, supportsTimeline } from "../../../types/fileKinds";
import type { DrawerFileItem } from "./types";
import { MeetingFilePanel } from "./MeetingFilePanel";
import type { ApprovalStatus, MaterialRole, BusinessDomain } from "../../../api/client";

interface Props {
  meetingId: number;
  files: DrawerFileItem[];
  detailLoading: boolean;
  expandedSummaryFileId: number | null;
  onSetExpandedSummaryFileId: (id: number | null) => void;
  onViewTimestamps: (meetingId: number, fileId: number) => void;
  onOpenSpeakers: (meetingId: number, fileId: number) => void;
  onReprocessFile: (meetingId: number, fileId: number) => void;
  onDownloadFile: (meetingId: number, fileId: number, fileName: string) => void;
  onDeleteFile: (meetingId: number, fileId: number) => void;
  onOpenDocumentViewer: (file: DrawerFileItem) => void;
  onDownloadFileMarkdown: (file: DrawerFileItem) => Promise<void>;
  onUpdateFileSemantics: (
    file: DrawerFileItem,
    update: {
      material_role?: MaterialRole;
      approval_status?: ApprovalStatus;
      business_domain?: BusinessDomain;
    },
  ) => Promise<void>;
  onOpenSemanticHistory: (file: DrawerFileItem) => void;
}

export function MeetingFilesSection({
  meetingId,
  files,
  detailLoading,
  expandedSummaryFileId,
  onSetExpandedSummaryFileId,
  onViewTimestamps,
  onOpenSpeakers,
  onReprocessFile,
  onDownloadFile,
  onDeleteFile,
  onOpenDocumentViewer,
  onDownloadFileMarkdown,
  onUpdateFileSemantics,
  onOpenSemanticHistory,
}: Props) {
  const intl = useIntl();
  const semanticLabel = (kind: string, value: string) =>
    intl.formatMessage({ id: `materials.${kind}.${value}`, defaultMessage: value });
  const activeSummaryKey =
    expandedSummaryFileId && files.some((file) => file.id === expandedSummaryFileId)
      ? [String(expandedSummaryFileId)]
      : [];

  return (
    <div style={{ marginBottom: 24 }}>
      <div
        style={{
          fontSize: 13,
          fontWeight: 600,
          color: "var(--color-text-secondary)",
          marginBottom: 8,
        }}
      >
        Files
      </div>
      {detailLoading ? (
        <Spin size="small" />
      ) : files.length ? (
        <Collapse
          accordion
          activeKey={activeSummaryKey}
          onChange={(keys) => {
            const keyList = Array.isArray(keys) ? keys : [keys];
            const lastKey = keyList[keyList.length - 1];
            if (lastKey) {
              onSetExpandedSummaryFileId(Number(lastKey));
            } else {
              onSetExpandedSummaryFileId(null);
            }
          }}
          items={files.map((file) => {
            const caps = getKindCapabilities(file.file_type);
            const timelineIcon = caps.hasTimeline ? (
              <PlayCircleOutlined />
            ) : caps.hasPages ? (
              <FilePdfOutlined />
            ) : caps.hasImages ? (
              <FileImageOutlined />
            ) : (
              <FileTextOutlined />
            );

            return {
              key: String(file.id),
              label: (
                <div
                  data-meeting-file={file.id}
                  style={{ display: "flex", alignItems: "center", gap: 12, width: "100%" }}
                >
                  <div
                    style={{
                      width: 28,
                      height: 28,
                      borderRadius: 6,
                      background:
                        TYPE_CONFIG[file.file_type ?? "image"]?.gradient ||
                        "var(--gradient-primary)",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      color: "#fff",
                      fontSize: 12,
                      flexShrink: 0,
                    }}
                  >
                    {timelineIcon}
                  </div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <span
                      style={{ fontSize: 13, fontWeight: 500, color: "var(--color-text-primary)" }}
                    >
                      {file.file_name}
                    </span>
                  </div>
                  <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
                    <Tag>{semanticLabel("role", file.material_role ?? "attachment")}</Tag>
                    <Tag color={file.approval_status === "approved" ? "green" : "default"}>
                      {semanticLabel("approval", file.approval_status ?? "unreviewed")}
                    </Tag>
                    {file.evidence_sync_status === "syncing" && (
                      <Tag color="blue" icon={<SyncOutlined spin />}>
                        {intl.formatMessage({ id: "materials.evidenceSyncing" })}
                      </Tag>
                    )}
                    {file.evidence_sync_status === "failed" && (
                      <Tooltip title={file.evidence_sync_error || "Evidence index rebuild failed"}>
                        <Tag color="red">
                          {intl.formatMessage({ id: "materials.evidenceFailed" })}
                        </Tag>
                      </Tooltip>
                    )}
                    <Tag
                      style={{
                        fontSize: 10,
                        borderRadius: 10,
                        padding: "0 6px",
                        lineHeight: "16px",
                      }}
                      color={
                        file.status !== "ready"
                          ? STATUS_CONFIG[file.status]?.color || "default"
                          : file.summary_status === "ready"
                            ? "green"
                            : file.summary_status === "failed"
                              ? "orange"
                              : file.summary_status === "generating"
                                ? "blue"
                                : "default"
                      }
                    >
                      {file.status !== "ready"
                        ? STATUS_CONFIG[file.status]?.label || file.status
                        : file.summary_status === "ready"
                          ? "Ready"
                          : file.summary_status === "failed"
                            ? "Summary failed"
                            : file.summary_status === "generating"
                              ? "Summarizing…"
                              : "Not summarized"}
                    </Tag>
                    {supportsTimeline(file.file_type) && file.status === "ready" && (
                      <Tooltip title="View transcript timestamps">
                        <Button
                          size="small"
                          type="text"
                          icon={<EyeOutlined />}
                          aria-label="View transcript timestamps"
                          onClick={(e) => {
                            e.stopPropagation();
                            onViewTimestamps(meetingId, file.id);
                          }}
                        />
                      </Tooltip>
                    )}
                    {supportsTimeline(file.file_type) && file.status === "ready" && (
                      <Tooltip title="Identify speakers">
                        <Button
                          size="small"
                          type="text"
                          icon={<UserOutlined />}
                          aria-label="Identify speakers"
                          onClick={(e) => {
                            e.stopPropagation();
                            onOpenSpeakers(meetingId, file.id);
                          }}
                        />
                      </Tooltip>
                    )}
                    {!supportsTimeline(file.file_type) && file.status === "ready" && (
                      <Tooltip title="View document">
                        <Button
                          size="small"
                          type="text"
                          icon={<EyeOutlined />}
                          aria-label="View document"
                          onClick={(e) => {
                            e.stopPropagation();
                            onOpenDocumentViewer(file);
                          }}
                        />
                      </Tooltip>
                    )}
                    <Dropdown
                      trigger={["click"]}
                      menu={{
                        selectedKeys: [
                          `role:${file.material_role ?? "attachment"}`,
                          `approval:${file.approval_status ?? "unreviewed"}`,
                        ],
                        items: [
                          {
                            key: "domain",
                            type: "group",
                            label: "Material domain",
                            children: ["unspecified", "meeting", "course", "research"].map(
                              (value) => ({
                                key: `domain:${value}`,
                                label: semanticLabel("domain", value),
                              }),
                            ),
                          },
                          {
                            key: "roles",
                            type: "group",
                            label: "Material role",
                            children: [
                              "transcript",
                              "minutes",
                              "agenda",
                              "decision_log",
                              "attachment",
                            ].map((role) => ({
                              key: `role:${role}`,
                              label: semanticLabel("role", role),
                            })),
                          },
                          {
                            key: "approval",
                            type: "group",
                            label: "Approval status",
                            children: [
                              "unreviewed",
                              "draft",
                              "reviewed",
                              "approved",
                              "rejected",
                            ].map((status) => ({
                              key: `approval:${status}`,
                              label: semanticLabel("approval", status),
                            })),
                          },
                        ],
                        onClick: ({ key, domEvent }) => {
                          domEvent.stopPropagation();
                          const [kind, value] = key.split(":");
                          if (kind === "role") {
                            void onUpdateFileSemantics(file, {
                              material_role: value as MaterialRole,
                            });
                          } else if (kind === "domain") {
                            void onUpdateFileSemantics(file, {
                              business_domain: value as BusinessDomain,
                            });
                          } else {
                            void onUpdateFileSemantics(file, {
                              approval_status: value as ApprovalStatus,
                            });
                          }
                        },
                      }}
                    >
                      <Tooltip title="Evidence role and approval">
                        <Button
                          size="small"
                          type="text"
                          icon={<SettingOutlined />}
                          aria-label="Edit evidence semantics"
                          onClick={(event) => event.stopPropagation()}
                        />
                      </Tooltip>
                    </Dropdown>
                    <Tooltip title="Review evidence history">
                      <Button
                        size="small"
                        type="text"
                        icon={<HistoryOutlined />}
                        aria-label="Review evidence history"
                        onClick={(event) => {
                          event.stopPropagation();
                          onOpenSemanticHistory(file);
                        }}
                      />
                    </Tooltip>
                    <Tooltip title="Reprocess this file">
                      <Button
                        size="small"
                        type="text"
                        icon={<ReloadOutlined />}
                        aria-label="Reprocess file"
                        onClick={(e) => {
                          e.stopPropagation();
                          onReprocessFile(meetingId, file.id);
                        }}
                      />
                    </Tooltip>
                    <Dropdown
                      trigger={["click"]}
                      menu={{
                        items: [
                          {
                            key: "raw",
                            label: "Download original file",
                            icon: <DownloadOutlined />,
                          },
                          {
                            key: "markdown",
                            label: "Download markdown (ZIP)",
                            icon: <FileTextOutlined />,
                          },
                        ],
                        onClick: ({ key, domEvent }) => {
                          domEvent.stopPropagation();
                          if (key === "raw") onDownloadFile(meetingId, file.id, file.file_name);
                          else void onDownloadFileMarkdown(file);
                        },
                      }}
                    >
                      <Tooltip title="Download options">
                        <Button
                          size="small"
                          type="text"
                          icon={<DownloadOutlined />}
                          aria-label="Download options"
                          onClick={(e) => e.stopPropagation()}
                        />
                      </Tooltip>
                    </Dropdown>
                    <Popconfirm
                      title="Delete this file?"
                      description="Associated vectors will also be removed."
                      onConfirm={() => onDeleteFile(meetingId, file.id)}
                      okText="Delete"
                      okButtonProps={{ danger: true }}
                    >
                      <Tooltip title="Delete file">
                        <Button
                          size="small"
                          type="text"
                          danger
                          icon={<DeleteOutlined />}
                          aria-label="Delete file"
                          onClick={(e) => e.stopPropagation()}
                        />
                      </Tooltip>
                    </Popconfirm>
                  </div>
                </div>
              ),
              children: <MeetingFilePanel file={file} />,
            };
          })}
          size="small"
        />
      ) : (
        <div style={{ fontSize: 13, color: "var(--color-text-muted)" }}>No files attached.</div>
      )}
    </div>
  );
}
