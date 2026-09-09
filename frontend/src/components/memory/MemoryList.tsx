import { Virtuoso } from "react-virtuoso";
import { formatLocalTime, toLocalDateTimeInput } from "../../utils/time";
import {
  Row,
  Col,
  Space,
  Spin,
  Empty,
  Tooltip,
  Button,
  Input,
  Modal,
  Tag,
  Checkbox,
  Popconfirm,
  message,
  Select,
  AutoComplete,
  Dropdown,
} from "antd";
import { useIntl } from "react-intl";
import { useViewer } from "../../contexts/ViewerContext";
import { useNavigate } from "react-router-dom";
import { useState } from "react";
import {
  SearchOutlined,
  PlusOutlined,
  ImportOutlined,
  ExportOutlined,
  ReloadOutlined,
  EditOutlined,
  DeleteOutlined,
  CheckSquareOutlined,
  LikeOutlined,
  DislikeOutlined,
  CheckOutlined,
  StopOutlined,
  HistoryOutlined,
  LinkOutlined,
} from "@ant-design/icons";
import { Typography } from "antd";
import type { MemoryItem } from "../../api/client";
import { formatApiErrorMessage, listMemoryVersions, type MemoryVersion } from "../../api/client";
import {
  buildEvidenceSearchParams,
  getEvidenceTarget,
  parseEvidenceViewerCoordinates,
} from "../../utils/evidenceNavigation";
import MemoryFormModal from "./MemoryFormModal";
import MemoryActions from "./MemoryActions";
import type { MemoryFormValues } from "./MemoryFormModal";

const { Text, Paragraph } = Typography;
const { Search } = Input;

interface MemoryListProps {
  displayMemories: MemoryItem[];
  loading: boolean;
  loadingMore: boolean;
  hasMore: boolean;
  total: number;
  onLoadMore: () => void;
  search: string;
  onSearchChange: (value: string) => void;
  factTypeFilter?: string;
  onFactTypeFilterChange: (value: string | undefined) => void;
  statusFilter?: string;
  onStatusFilterChange: (value: string | undefined) => void;
  projectFilter?: string;
  projectOptions?: string[];
  onProjectFilterChange?: (value: string | undefined) => void;
  onSemanticSearch: () => void;
  searching: boolean;
  semanticResults: MemoryItem[] | null;
  onClearSemantic: () => void;
  onRefresh: () => void;
  onCreateOpen: () => void;
  onImportOpen: () => void;
  onExport: () => void;
  onDecay: () => void;
  decaying: boolean;
  activeAction: string | null;
  feedbackKey: string | null;
  onFeedback: (key: string, useful: boolean) => void;
  onStatusChange: (memory: MemoryItem, status: "confirmed" | "retracted" | "disputed") => void;
  onEdit: (memory: MemoryItem) => void;
  onDelete: (key: string) => void;
  editMemory: MemoryItem | null;
  onEditClose: () => void;
  onEditSubmit: (values: MemoryFormValues & { key: string; value: string }) => void;
  createOpen: boolean;
  onCreateClose: () => void;
  onCreateSubmit: (
    values: MemoryFormValues & { key: string; value: string; expiresInDays?: number },
  ) => void;
  importOpen: boolean;
  onImportClose: () => void;
  importText: string;
  onImportTextChange: (text: string) => void;
  onImportSubmit: () => void;
  // Selection props
  isSelectionMode: boolean;
  selectedKeys: Set<string>;
  selectedCount: number;
  onToggleSelectionMode: () => void;
  onToggleSelect: (key: string) => void;
  onSelectAll: () => void;
  onClearSelection: () => void;
  onBatchDelete: (keys: string[]) => void;
  onRetryIndex?: (key: string) => void;
}

export default function MemoryList({
  displayMemories,
  loading,
  loadingMore,
  hasMore,
  total,
  onLoadMore,
  search,
  onSearchChange,
  factTypeFilter,
  onFactTypeFilterChange,
  statusFilter,
  onStatusFilterChange,
  projectFilter,
  projectOptions = [],
  onProjectFilterChange = () => undefined,
  onSemanticSearch,
  searching,
  semanticResults,
  onClearSemantic,
  onRefresh,
  onCreateOpen,
  onImportOpen,
  onExport,
  onDecay,
  decaying,
  activeAction,
  feedbackKey,
  onFeedback,
  onStatusChange,
  onEdit,
  onDelete,
  editMemory,
  onEditClose,
  onEditSubmit,
  createOpen,
  onCreateClose,
  onCreateSubmit,
  importOpen,
  onImportClose,
  importText,
  onImportTextChange,
  onImportSubmit,
  isSelectionMode,
  selectedKeys,
  selectedCount,
  onToggleSelectionMode,
  onToggleSelect,
  onSelectAll,
  onClearSelection,
  onBatchDelete,
  onRetryIndex,
}: MemoryListProps) {
  const { formatMessage } = useIntl();
  const { openViewer } = useViewer();
  const navigate = useNavigate();
  const [historyMemory, setHistoryMemory] = useState<MemoryItem | null>(null);
  const [versions, setVersions] = useState<MemoryVersion[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);

  const openEvidence = (
    ref: Record<string, unknown>,
    fallbackMeetingIds?: number[] | null,
    excerpt?: string | null,
  ) => {
    const target = getEvidenceTarget(ref, fallbackMeetingIds);
    if (!target) return;
    const params = buildEvidenceSearchParams(target.meetingId, target.fileId, {
      ...ref,
      evidence_excerpt: excerpt,
    });
    openViewer({
      ...parseEvidenceViewerCoordinates(params),
      page:
        params.has("pageNumber") || params.has("slideNumber")
          ? parseEvidenceViewerCoordinates(params).page
          : undefined,
      meetingId: target.meetingId,
      fileId: target.fileId,
      fileName: "",
      fileType: "unknown",
    });
  };

  const materialTargets = (memory: MemoryItem): Record<string, unknown>[] => {
    const refs = (memory.evidence_refs ?? []).filter((ref) =>
      getEvidenceTarget(ref, memory.meeting_ids),
    );
    if (refs.length) return refs;
    if (memory.meeting_ids?.length === 1 && memory.file_ids?.length === 1) {
      return [{ meeting_id: memory.meeting_ids[0], file_id: memory.file_ids[0] }];
    }
    return [];
  };

  const showHistory = async (memory: MemoryItem) => {
    setHistoryMemory(memory);
    setVersions([]);
    setHistoryLoading(true);
    try {
      const response = await listMemoryVersions(memory.key);
      setVersions(response.data);
    } catch (error) {
      message.error(formatApiErrorMessage(error, "Failed to load memory history"));
    } finally {
      setHistoryLoading(false);
    }
  };

  const statusColor = (status: string) => {
    if (status === "confirmed") return "#047857";
    if (status === "pending") return "#1d4ed8";
    if (status === "disputed") return "#a16207";
    return "#4b5563";
  };

  const batchDeleteLabel = formatMessage(
    { id: "memory.list.batchDeleteTitle" },
    { count: selectedCount },
  );
  const batchDeleteDesc = formatMessage({ id: "memory.list.batchDeleteDesc" });

  return (
    <div className="memory-list-layout">
      <Row gutter={[16, 8]} align="middle" wrap>
        <Col flex="none">
          <Space.Compact>
            <Search
              placeholder={formatMessage({ id: "memory.list.searchPlaceholder" })}
              allowClear
              style={{ width: 280 }}
              value={search}
              onChange={(e) => {
                if (semanticResults !== null) onClearSemantic();
                onSearchChange(e.target.value);
              }}
              onSearch={() => {
                if (statusFilter && statusFilter !== "confirmed") {
                  message.info(formatMessage({ id: "memory.list.semanticActiveOnly" }));
                  return;
                }
                onSemanticSearch();
              }}
              loading={searching}
              enterButton={<SearchOutlined />}
            />
          </Space.Compact>
          {semanticResults !== null && (
            <Button type="link" size="small" onClick={onClearSemantic}>
              {formatMessage(
                { id: "memory.list.clearSemantic" },
                { count: semanticResults.length },
              )}
            </Button>
          )}
        </Col>
        <Col flex="none">
          <AutoComplete
            allowClear
            aria-label={formatMessage({ id: "memory.list.projectFilter" })}
            placeholder={formatMessage({ id: "memory.list.projectFilter" })}
            value={projectFilter}
            onChange={(value) => onProjectFilterChange(value || undefined)}
            style={{ width: 150 }}
            options={projectOptions.map((value) => ({ value, label: value }))}
          />
        </Col>
        <Col flex="none">
          <Select
            allowClear
            virtual={false}
            aria-label={formatMessage({ id: "memory.list.typeFilter" })}
            placeholder={formatMessage({ id: "memory.list.typeFilter" })}
            value={factTypeFilter}
            onChange={onFactTypeFilterChange}
            style={{ width: 150 }}
            options={["fact", "preference", "project_fact", "decision", "action_item"].map(
              (value) => ({ value, label: value }),
            )}
          />
        </Col>
        <Col flex="none">
          <Select
            allowClear
            virtual={false}
            aria-label={formatMessage({ id: "memory.list.statusFilter" })}
            placeholder={formatMessage({ id: "memory.list.statusFilter" })}
            value={statusFilter}
            onChange={onStatusFilterChange}
            style={{ width: 180, maxWidth: "100%" }}
            options={["pending", "confirmed", "disputed", "superseded", "retracted"].map(
              (value) => ({ value, label: value }),
            )}
          />
        </Col>
        <Col flex="auto" />
        <Col flex="none">
          <Space wrap>
            <Button
              type={isSelectionMode ? "primary" : "default"}
              icon={<CheckSquareOutlined />}
              onClick={onToggleSelectionMode}
              disabled={activeAction !== null}
            >
              {isSelectionMode
                ? formatMessage({ id: "memory.list.selectModeDone" })
                : formatMessage({ id: "memory.list.selectMode" })}
            </Button>
            {!isSelectionMode && (
              <>
                <Button
                  icon={<PlusOutlined />}
                  onClick={onCreateOpen}
                  disabled={activeAction !== null}
                >
                  {formatMessage({ id: "memory.list.add" })}
                </Button>
                <Button
                  icon={<ImportOutlined />}
                  onClick={onImportOpen}
                  disabled={activeAction !== null}
                >
                  {formatMessage({ id: "memory.list.import" })}
                </Button>
                <Tooltip title={formatMessage({ id: "memory.list.exportTooltip" })}>
                  <Button
                    icon={<ExportOutlined />}
                    onClick={onExport}
                    loading={activeAction === "export"}
                    disabled={activeAction !== null && activeAction !== "export"}
                  >
                    {formatMessage({ id: "memory.list.export" })}
                  </Button>
                </Tooltip>
                <Tooltip title={formatMessage({ id: "memory.list.decayTooltip" })}>
                  <Button
                    icon={<ReloadOutlined />}
                    loading={decaying}
                    onClick={onDecay}
                    disabled={activeAction !== null}
                  >
                    {formatMessage({ id: "memory.list.decay" })}
                  </Button>
                </Tooltip>
              </>
            )}
            <Button
              icon={<ReloadOutlined />}
              loading={loading}
              onClick={onRefresh}
              disabled={activeAction !== null}
            >
              {formatMessage({ id: "memory.list.refresh" })}
            </Button>
          </Space>
        </Col>
      </Row>

      {/* Batch delete toolbar */}
      {isSelectionMode && selectedCount > 0 && (
        <Row
          gutter={[12, 0]}
          align="middle"
          style={{
            padding: "8px 16px",
            background: "var(--color-primary-alpha, rgba(79, 70, 229, 0.08))",
            borderRadius: 8,
          }}
        >
          <Col flex="auto">
            <Space>
              <span style={{ fontSize: 13, fontWeight: 600 }}>
                {formatMessage({ id: "memory.list.selectedCount" }, { count: selectedCount })}
              </span>
              <Button size="small" onClick={onSelectAll}>
                {formatMessage({ id: "memory.list.selectAll" })}
              </Button>
              <Button size="small" onClick={onClearSelection}>
                {formatMessage({ id: "memory.list.clearSelection" })}
              </Button>
            </Space>
          </Col>
          <Col>
            <Popconfirm
              title={batchDeleteLabel}
              description={batchDeleteDesc}
              onConfirm={() => onBatchDelete(Array.from(selectedKeys))}
              okText="Delete"
              okButtonProps={{ danger: true }}
            >
              <Button
                danger
                size="small"
                icon={<DeleteOutlined />}
                loading={activeAction === "batch-delete"}
                disabled={activeAction !== null && activeAction !== "batch-delete"}
              >
                {formatMessage({ id: "memory.list.deleteTooltip" })}
              </Button>
            </Popconfirm>
          </Col>
        </Row>
      )}

      <div className="memory-list-scroll-region">
        <Spin spinning={loading}>
          {displayMemories.length === 0 ? (
            <Empty
              description={
                semanticResults !== null
                  ? formatMessage({ id: "memory.list.noSemanticMatches" })
                  : formatMessage({ id: "memory.list.noMemories" })
              }
            />
          ) : (
            <Virtuoso
              className="memory-list-virtuoso"
              data={displayMemories}
              style={{ height: "100%" }}
              endReached={() => {
                if (hasMore && semanticResults === null && !search) onLoadMore();
              }}
              components={{
                Footer: () => (
                  <div style={{ padding: 12, textAlign: "center" }}>
                    {loadingMore ? (
                      <Spin size="small" />
                    ) : hasMore && semanticResults === null && !search ? (
                      <Button size="small" onClick={onLoadMore}>
                        {formatMessage({ id: "memory.list.loadMore" })}
                      </Button>
                    ) : (
                      <Text type="secondary">
                        {formatMessage(
                          { id: "memory.list.loadedCount" },
                          { shown: displayMemories.length, total },
                        )}
                      </Text>
                    )}
                  </div>
                ),
              }}
              itemContent={(_index, m) => (
                <div
                  className="ant-list-item memory-list-item"
                  style={{
                    display: "flex",
                    alignItems: "flex-start",
                    gap: 12,
                    padding: "8px 0",
                    ...(isSelectionMode && selectedKeys.has(m.key)
                      ? { background: "var(--color-primary-alpha, rgba(79, 70, 229, 0.08))" }
                      : {}),
                  }}
                >
                  {isSelectionMode && (
                    <Checkbox
                      checked={selectedKeys.has(m.key)}
                      onChange={() => onToggleSelect(m.key)}
                      aria-label={`Select ${m.key}`}
                      style={{ marginTop: 4 }}
                    />
                  )}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <Space size={4} wrap>
                      <Text strong className="memory-readable-title" title={m.key}>
                        {m.key.split(".").slice(1).join(" · ").replace(/_/g, " ") || m.key}
                      </Text>
                      <Tag color={statusColor(m.assertion_status)}>
                        {m.assertion_status === "confirmed"
                          ? formatMessage({
                              id:
                                m.source === "manual"
                                  ? "memory.trust.manualConfirmed"
                                  : "memory.trust.autoConfirmed",
                            })
                          : m.assertion_status}
                      </Tag>
                      {m.archived_at && <Tag>{formatMessage({ id: "workflow.archived" })}</Tag>}
                      {m.project_id && <Tag color="blue">{m.project_id}</Tag>}
                      {m.action_status && <Tag>{m.action_status}</Tag>}
                    </Space>
                    <details style={{ marginTop: 4 }}>
                      <summary style={{ cursor: "pointer", fontSize: 12 }}>
                        {formatMessage({ id: "memory.details" })}
                      </summary>
                      {m.key.includes(".") && (
                        <code style={{ overflowWrap: "anywhere" }}>{m.key}</code>
                      )}
                      <Space size={4} wrap>
                        {m.category && <Tag color="geekblue">{m.category}</Tag>}
                        <Tag color={statusColor(m.assertion_status)}>{m.assertion_status}</Tag>
                        <Tag>{m.fact_type}</Tag>
                        {m.project_id && <Tag color="blue">{m.project_id}</Tag>}
                        {m.action_status && <Tag color="orange">{m.action_status}</Tag>}
                        {m.assignee && <Tag>{m.assignee}</Tag>}
                        <Tag color={m.importance >= 4 ? "gold" : "default"}>
                          {formatMessage(
                            { id: "memory.list.importanceLabel" },
                            { n: m.importance },
                          )}
                        </Tag>
                        {m.source === "auto_extracted" && (
                          <Tag color="cyan">{formatMessage({ id: "memory.list.autoTag" })}</Tag>
                        )}
                        {m.source === "consolidated" && (
                          <Tag color="purple">{formatMessage({ id: "memory.list.mergedTag" })}</Tag>
                        )}
                        {m.vector_state &&
                          m.vector_state !== "synced" &&
                          m.vector_state !== "inactive" && (
                            <Tag color={m.vector_state === "failed" ? "error" : "processing"}>
                              {formatMessage(
                                { id: "memory.list.vectorState" },
                                { state: m.vector_state },
                              )}
                            </Tag>
                          )}
                        {m.vector_state === "failed" && onRetryIndex && (
                          <Button
                            size="small"
                            disabled={activeAction !== null}
                            onClick={() => onRetryIndex(m.key)}
                          >
                            {formatMessage({ id: "memory.list.retryIndex" })}
                          </Button>
                        )}
                        {(m.meeting_ids?.length || m.file_ids?.length) && (
                          <Tag>
                            {formatMessage(
                              { id: "memory.list.scope" },
                              {
                                meetings: m.meeting_ids?.length ?? 0,
                                files: m.file_ids?.length ?? 0,
                              },
                            )}
                          </Tag>
                        )}
                      </Space>
                    </details>
                    <Space orientation="vertical" size={0} style={{ display: "flex" }}>
                      <Text>{m.value}</Text>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        {formatMessage(
                          { id: "memory.list.updated" },
                          { date: formatLocalTime(m.updated_at, { dateOnly: true }) },
                        )}
                        {m.expires_at &&
                          ` · ${formatMessage(
                            { id: "memory.list.expires" },
                            { date: formatLocalTime(m.expires_at, { dateOnly: true }) },
                          )}`}
                        {m.usefulness_count > 0 &&
                          ` · ${formatMessage(
                            { id: "memory.list.usefulness" },
                            {
                              percent: Math.round(m.usefulness_score * 100),
                              count: m.usefulness_count,
                            },
                          )}`}
                        {m.due_at &&
                          ` · ${formatMessage(
                            { id: "memory.list.dueAt" },
                            { date: formatLocalTime(m.due_at) },
                          )}`}
                      </Text>
                      {(m.valid_from || m.valid_to) && (
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          {formatMessage(
                            { id: "memory.list.validity" },
                            {
                              from: m.valid_from ? formatLocalTime(m.valid_from) : "—",
                              to: m.valid_to ? formatLocalTime(m.valid_to) : "—",
                            },
                          )}
                        </Text>
                      )}
                      {m.evidence_excerpt && (
                        <Paragraph
                          type="secondary"
                          ellipsis={{ rows: 2, expandable: true, symbol: "more" }}
                          style={{ margin: 0, fontSize: 12 }}
                        >
                          {formatMessage({ id: "memory.list.evidence" })}: {m.evidence_excerpt}
                        </Paragraph>
                      )}
                      {m.evidence_refs?.map((ref, index) => (
                        <Button
                          key={`${String(ref.file_id ?? "source")}-${String(ref.window_hash ?? index)}`}
                          type="link"
                          size="small"
                          icon={<LinkOutlined />}
                          disabled={!getEvidenceTarget(ref, m.meeting_ids)}
                          aria-label={formatMessage({ id: "memory.list.openExactEvidence" })}
                          style={{
                            display: "block",
                            height: "auto",
                            paddingInline: 0,
                            fontSize: 11,
                          }}
                          onClick={() => openEvidence(ref, m.meeting_ids, m.evidence_excerpt)}
                        >
                          {formatMessage(
                            { id: "memory.list.evidenceRef" },
                            {
                              file: String(ref.file_id ?? "?"),
                              start: String(ref.window_start ?? "?"),
                              end: String(ref.window_end ?? "?"),
                              revision: String(ref.source_revision ?? "?").slice(0, 12),
                            },
                          )}
                        </Button>
                      ))}
                      {m.session_id && (
                        <Button
                          type="link"
                          size="small"
                          aria-label={formatMessage({ id: "memory.list.openSource" })}
                          icon={<LinkOutlined />}
                          style={{ paddingInline: 0 }}
                          onClick={() =>
                            navigate(`/?sessionId=${encodeURIComponent(m.session_id!)}`)
                          }
                        >
                          {formatMessage({ id: "memory.list.openSource" })}
                        </Button>
                      )}
                      {!m.session_id && (m.meeting_ids?.length || m.file_ids?.length) && (
                        <Dropdown
                          disabled={materialTargets(m).length <= 1}
                          trigger={["click"]}
                          menu={{
                            items: materialTargets(m).map((ref, index) => ({
                              key: String(index),
                              label: formatMessage(
                                { id: "memory.list.sourceChoice" },
                                { n: index + 1, file: String(ref.file_id) },
                              ),
                              onClick: () => openEvidence(ref, m.meeting_ids, m.evidence_excerpt),
                            })),
                          }}
                        >
                          <Button
                            type="link"
                            size="small"
                            aria-label={formatMessage({ id: "memory.list.openSourceMaterial" })}
                            icon={<LinkOutlined />}
                            style={{ paddingInline: 0 }}
                            disabled={materialTargets(m).length === 0}
                            onClick={() => {
                              const targets = materialTargets(m);
                              if (targets.length === 1)
                                openEvidence(targets[0], m.meeting_ids, m.evidence_excerpt);
                            }}
                          >
                            {formatMessage({ id: "memory.list.openSourceMaterial" })}
                          </Button>
                        </Dropdown>
                      )}
                    </Space>
                  </div>
                  {!isSelectionMode && (
                    <MemoryActions>
                      <Space size={0} wrap>
                        {(m.assertion_status === "pending" ||
                          m.assertion_status === "disputed") && (
                          <Tooltip title={formatMessage({ id: "memory.list.confirmTooltip" })}>
                            <Button
                              type="text"
                              icon={<CheckOutlined />}
                              aria-label={formatMessage({ id: "memory.list.confirmTooltip" })}
                              onClick={() => onStatusChange(m, "confirmed")}
                              disabled={activeAction !== null}
                            />
                          </Tooltip>
                        )}
                        {m.assertion_status !== "retracted" && (
                          <Popconfirm
                            title={formatMessage({ id: "memory.list.retractConfirm" })}
                            onConfirm={() => onStatusChange(m, "retracted")}
                          >
                            <Tooltip title={formatMessage({ id: "memory.list.retractTooltip" })}>
                              <Button
                                type="text"
                                icon={<StopOutlined />}
                                aria-label={formatMessage({ id: "memory.list.retractTooltip" })}
                                disabled={activeAction !== null}
                              />
                            </Tooltip>
                          </Popconfirm>
                        )}
                        <Tooltip title={formatMessage({ id: "memory.list.historyTooltip" })}>
                          <Button
                            type="text"
                            icon={<HistoryOutlined />}
                            aria-label={formatMessage({ id: "memory.list.historyTooltip" })}
                            onClick={() => void showHistory(m)}
                          />
                        </Tooltip>
                        <Tooltip title={formatMessage({ id: "memory.list.usefulTooltip" })}>
                          <Button
                            type="text"
                            icon={<LikeOutlined />}
                            aria-label={formatMessage({ id: "memory.list.usefulTooltip" })}
                            onClick={() => onFeedback(m.key, true)}
                            loading={feedbackKey === m.key}
                            disabled={feedbackKey !== null || activeAction !== null}
                          />
                        </Tooltip>
                        <Tooltip title={formatMessage({ id: "memory.list.notUsefulTooltip" })}>
                          <Button
                            type="text"
                            icon={<DislikeOutlined />}
                            aria-label={formatMessage({ id: "memory.list.notUsefulTooltip" })}
                            onClick={() => onFeedback(m.key, false)}
                            disabled={feedbackKey !== null || activeAction !== null}
                          />
                        </Tooltip>
                        <Tooltip title={formatMessage({ id: "memory.list.editTooltip" })}>
                          <Button
                            type="text"
                            icon={<EditOutlined />}
                            aria-label={formatMessage({ id: "memory.list.editTooltip" })}
                            onClick={() => onEdit(m)}
                            disabled={activeAction !== null}
                          />
                        </Tooltip>
                        <Tooltip title={formatMessage({ id: "memory.list.deleteTooltip" })}>
                          <Button
                            type="text"
                            danger
                            icon={<DeleteOutlined />}
                            aria-label={formatMessage({ id: "memory.list.deleteTooltip" })}
                            onClick={() => onDelete(m.key)}
                            disabled={activeAction !== null}
                          />
                        </Tooltip>
                      </Space>
                    </MemoryActions>
                  )}
                </div>
              )}
            />
          )}
        </Spin>
      </div>

      {/* Create Memory Modal */}
      <Modal
        title={formatMessage({ id: "memory.list.historyTitle" }, { key: historyMemory?.key ?? "" })}
        open={historyMemory !== null}
        footer={null}
        onCancel={() => setHistoryMemory(null)}
      >
        <Spin spinning={historyLoading}>
          <Space orientation="vertical" style={{ width: "100%" }}>
            {versions.map((version) => (
              <div
                key={version.revision}
                style={{ borderBottom: "1px solid #eee", paddingBlock: 8 }}
              >
                <Space wrap>
                  <Text strong>v{version.revision}</Text>
                  <Tag color={statusColor(version.assertion_status)}>
                    {version.assertion_status}
                  </Tag>
                  <Tag>{version.fact_type}</Tag>
                  {version.action_status && <Tag color="orange">{version.action_status}</Tag>}
                  {version.assignee && <Tag>{version.assignee}</Tag>}
                  <Text type="secondary">{formatLocalTime(version.recorded_at)}</Text>
                </Space>
                <Text type="secondary" style={{ display: "block", fontSize: 12 }}>
                  {formatMessage(
                    { id: "memory.list.recordedValidity" },
                    {
                      from: formatLocalTime(version.recorded_at),
                      to: version.recorded_to ? formatLocalTime(version.recorded_to) : "—",
                    },
                  )}
                </Text>
                <Paragraph style={{ marginBlock: 4 }}>{version.value}</Paragraph>
                {(version.valid_from || version.valid_to) && (
                  <Text type="secondary" style={{ display: "block", fontSize: 12 }}>
                    {formatMessage(
                      { id: "memory.list.validity" },
                      {
                        from: version.valid_from ? formatLocalTime(version.valid_from) : "—",
                        to: version.valid_to ? formatLocalTime(version.valid_to) : "—",
                      },
                    )}
                  </Text>
                )}
                {version.evidence_excerpt && (
                  <Paragraph type="secondary" ellipsis={{ rows: 2, expandable: true }}>
                    {formatMessage({ id: "memory.list.evidence" })}: {version.evidence_excerpt}
                  </Paragraph>
                )}
                {version.evidence_refs?.map((ref, index) => (
                  <Button
                    key={`${String(ref.file_id ?? "source")}-${String(ref.window_hash ?? index)}`}
                    type="link"
                    size="small"
                    icon={<LinkOutlined />}
                    disabled={!getEvidenceTarget(ref)}
                    aria-label={formatMessage({ id: "memory.list.openExactEvidence" })}
                    style={{ display: "block", height: "auto", paddingInline: 0, fontSize: 11 }}
                    onClick={() => openEvidence(ref, undefined, version.evidence_excerpt)}
                  >
                    {formatMessage(
                      { id: "memory.list.evidenceRef" },
                      {
                        file: String(ref.file_id ?? "?"),
                        start: String(ref.window_start ?? "?"),
                        end: String(ref.window_end ?? "?"),
                        revision: String(ref.source_revision ?? "?").slice(0, 12),
                      },
                    )}
                  </Button>
                ))}
              </div>
            ))}
            {!historyLoading && versions.length === 0 && (
              <Empty description={formatMessage({ id: "memory.list.historyEmpty" })} />
            )}
          </Space>
        </Spin>
      </Modal>

      <MemoryFormModal
        title={formatMessage({ id: "memory.list.createTitle" })}
        open={createOpen}
        onClose={onCreateClose}
        onSubmit={onCreateSubmit}
        submitting={activeAction === "create"}
      />

      {/* Edit Memory Modal */}
      <MemoryFormModal
        title={formatMessage({ id: "memory.list.editTitle" })}
        open={!!editMemory}
        onClose={onEditClose}
        initialValues={
          editMemory
            ? {
                key: editMemory.key,
                value: editMemory.value,
                category: editMemory.category ?? undefined,
                importance: editMemory.importance,
                factType: editMemory.fact_type as MemoryFormValues["factType"],
                assertionStatus: editMemory.assertion_status as MemoryFormValues["assertionStatus"],
                projectId: editMemory.project_id ?? undefined,
                validFrom: toLocalDateTimeInput(editMemory.valid_from),
                validTo: toLocalDateTimeInput(editMemory.valid_to),
                actionStatus:
                  (editMemory.action_status as MemoryFormValues["actionStatus"]) ?? undefined,
                assignee: editMemory.assignee ?? undefined,
                dueAt: toLocalDateTimeInput(editMemory.due_at),
              }
            : undefined
        }
        onSubmit={onEditSubmit}
        disableKey
        submitting={activeAction === "edit"}
      />

      {/* Import Modal */}
      <Modal
        title={formatMessage({ id: "memory.list.importTitle" })}
        open={importOpen}
        onCancel={onImportClose}
        onOk={onImportSubmit}
        okText={formatMessage({ id: "memory.list.importOk" })}
        confirmLoading={activeAction === "import"}
        okButtonProps={{ disabled: activeAction !== null && activeAction !== "import" }}
        width="min(94vw, 760px)"
        styles={{ body: { maxHeight: "calc(100vh - 220px)", overflowY: "auto" } }}
      >
        <Paragraph type="secondary" style={{ marginBottom: 12 }}>
          {formatMessage({ id: "memory.list.importDesc" })}
        </Paragraph>
        <Input.TextArea
          rows={10}
          value={importText}
          onChange={(e) => onImportTextChange(e.target.value)}
          placeholder='[
  {"key": "project_name", "value": "Meeting Agent", "category": "project", "importance": 4, "expires_in_days": 30}
]'
          style={{ fontFamily: "monospace", fontSize: 13 }}
        />
      </Modal>
    </div>
  );
}
