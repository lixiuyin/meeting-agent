import { Virtuoso } from "react-virtuoso";
import { formatLocalTime } from "../../utils/time";
import {
  Row,
  Col,
  Space,
  Spin,
  Empty,
  List,
  Tooltip,
  Button,
  Input,
  Modal,
  Tag,
  Checkbox,
  Popconfirm,
} from "antd";
import { useIntl } from "react-intl";
import {
  SearchOutlined,
  PlusOutlined,
  ImportOutlined,
  ExportOutlined,
  ReloadOutlined,
  EditOutlined,
  DeleteOutlined,
  CheckSquareOutlined,
} from "@ant-design/icons";
import { Typography } from "antd";
import type { MemoryItem } from "../../api/client";
import MemoryFormModal from "./MemoryFormModal";

const { Text, Paragraph } = Typography;
const { Search } = Input;

interface MemoryListProps {
  displayMemories: MemoryItem[];
  loading: boolean;
  search: string;
  onSearchChange: (value: string) => void;
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
  onEdit: (memory: MemoryItem) => void;
  onDelete: (key: string) => void;
  editMemory: MemoryItem | null;
  onEditClose: () => void;
  onEditSubmit: (values: {
    key: string;
    value: string;
    category?: string;
    importance?: number;
  }) => void;
  createOpen: boolean;
  onCreateClose: () => void;
  onCreateSubmit: (values: {
    key: string;
    value: string;
    category?: string;
    importance?: number;
    expiresInDays?: number;
  }) => void;
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
}

export default function MemoryList({
  displayMemories,
  loading,
  search,
  onSearchChange,
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
}: MemoryListProps) {
  const { formatMessage } = useIntl();

  const batchDeleteLabel = formatMessage(
    { id: "memory.list.batchDeleteTitle" },
    { count: selectedCount },
  );
  const batchDeleteDesc = formatMessage({ id: "memory.list.batchDeleteDesc" });

  return (
    <Space orientation="vertical" style={{ width: "100%" }} size="middle">
      <Row gutter={[16, 8]} align="middle" wrap>
        <Col flex="none">
          <Space.Compact>
            <Search
              placeholder={formatMessage({ id: "memory.list.searchPlaceholder" })}
              allowClear
              style={{ width: 280 }}
              value={search}
              onChange={(e) => {
                onSearchChange(e.target.value);
                if (semanticResults !== null) onClearSemantic();
              }}
              onSearch={onSemanticSearch}
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
        <Col flex="auto" />
        <Col flex="none">
          <Space wrap>
            <Button
              type={isSelectionMode ? "primary" : "default"}
              icon={<CheckSquareOutlined />}
              onClick={onToggleSelectionMode}
            >
              {isSelectionMode
                ? formatMessage({ id: "memory.list.selectModeDone" })
                : formatMessage({ id: "memory.list.selectMode" })}
            </Button>
            {!isSelectionMode && (
              <>
                <Button icon={<PlusOutlined />} onClick={onCreateOpen}>
                  {formatMessage({ id: "memory.list.add" })}
                </Button>
                <Button icon={<ImportOutlined />} onClick={onImportOpen}>
                  {formatMessage({ id: "memory.list.import" })}
                </Button>
                <Tooltip title={formatMessage({ id: "memory.list.exportTooltip" })}>
                  <Button icon={<ExportOutlined />} onClick={onExport}>
                    Export
                  </Button>
                </Tooltip>
                <Tooltip title={formatMessage({ id: "memory.list.decayTooltip" })}>
                  <Button icon={<ReloadOutlined />} loading={decaying} onClick={onDecay}>
                    {formatMessage({ id: "memory.list.decay" })}
                  </Button>
                </Tooltip>
              </>
            )}
            <Button icon={<ReloadOutlined />} loading={loading} onClick={onRefresh}>
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
              <Button danger size="small" icon={<DeleteOutlined />}>
                {formatMessage({ id: "memory.list.deleteTooltip" })}
              </Button>
            </Popconfirm>
          </Col>
        </Row>
      )}

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
            data={displayMemories}
            style={{ height: "100%", minHeight: 200 }}
            itemContent={(_index, m) => (
              <List.Item
                style={{
                  padding: "8px 0",
                  ...(isSelectionMode && selectedKeys.has(m.key)
                    ? { background: "var(--color-primary-alpha, rgba(79, 70, 229, 0.08))" }
                    : {}),
                }}
                actions={
                  isSelectionMode
                    ? []
                    : [
                        <Tooltip
                          key="edit"
                          title={formatMessage({ id: "memory.list.editTooltip" })}
                        >
                          <Button
                            type="text"
                            icon={<EditOutlined />}
                            aria-label={formatMessage({ id: "memory.list.editTooltip" })}
                            onClick={() => onEdit(m)}
                          />
                        </Tooltip>,
                        <Tooltip
                          key="del"
                          title={formatMessage({ id: "memory.list.deleteTooltip" })}
                        >
                          <Button
                            type="text"
                            danger
                            icon={<DeleteOutlined />}
                            aria-label={formatMessage({ id: "memory.list.deleteTooltip" })}
                            onClick={() => onDelete(m.key)}
                          />
                        </Tooltip>,
                      ]
                }
              >
                <List.Item.Meta
                  avatar={
                    isSelectionMode ? (
                      <Checkbox
                        checked={selectedKeys.has(m.key)}
                        onChange={() => onToggleSelect(m.key)}
                      />
                    ) : undefined
                  }
                  title={
                    <Space size={4}>
                      <Text strong>{m.key}</Text>
                      {m.category && <Tag color="geekblue">{m.category}</Tag>}
                      <Tag color={m.importance >= 4 ? "gold" : "default"}>
                        {formatMessage({ id: "memory.list.importanceLabel" }, { n: m.importance })}
                      </Tag>
                      {m.source === "auto_extracted" && (
                        <Tag color="cyan">{formatMessage({ id: "memory.list.autoTag" })}</Tag>
                      )}
                      {m.source === "consolidated" && (
                        <Tag color="purple">{formatMessage({ id: "memory.list.mergedTag" })}</Tag>
                      )}
                    </Space>
                  }
                  description={
                    <Space orientation="vertical" size={0}>
                      <Text>{m.value}</Text>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        Updated {formatLocalTime(m.updated_at, { dateOnly: true })}
                        {m.expires_at &&
                          ` · expires ${formatLocalTime(m.expires_at, { dateOnly: true })}`}
                      </Text>
                    </Space>
                  }
                />
              </List.Item>
            )}
          />
        )}
      </Spin>

      {/* Create Memory Modal */}
      <MemoryFormModal
        title={formatMessage({ id: "memory.list.createTitle" })}
        open={createOpen}
        onClose={onCreateClose}
        onSubmit={onCreateSubmit}
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
              }
            : undefined
        }
        onSubmit={onEditSubmit}
        disableKey
      />

      {/* Import Modal */}
      <Modal
        title={formatMessage({ id: "memory.list.importTitle" })}
        open={importOpen}
        onCancel={onImportClose}
        onOk={onImportSubmit}
        okText={formatMessage({ id: "memory.list.importOk" })}
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
    </Space>
  );
}
