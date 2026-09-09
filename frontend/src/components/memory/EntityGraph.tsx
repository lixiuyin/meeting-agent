import { useState, useCallback, useEffect, useMemo, useRef } from "react";
import { useIntl } from "react-intl";
import {
  Badge,
  Button,
  Collapse,
  Empty,
  Input,
  Modal,
  Popconfirm,
  Row,
  Col,
  Select,
  Space,
  Spin,
  Tag,
  message,
} from "antd";
import {
  DeleteOutlined,
  MergeCellsOutlined,
  ReloadOutlined,
  SearchOutlined,
} from "@ant-design/icons";
import {
  deleteEntity,
  batchDeleteEntities,
  listAllEntities,
  mergeEntities,
  formatApiErrorMessage,
  isRequestCanceled,
  type EntityItem,
} from "../../api/client";
import { useUndoStack } from "../../hooks/useUndoStack";
import { useDebounce } from "../../hooks/useDebounce";
import EntityRow, { ENTITY_TYPE_COLORS } from "./EntityRow";

const ENTITY_TYPES = ["person", "project", "topic", "organization", "tool", "concept", "location"];

interface EntityGraphProps {
  userId: string;
}

export default function EntityGraph({ userId }: EntityGraphProps) {
  const { formatMessage } = useIntl();
  const noEntitiesMsg = formatMessage({ id: "memory.entity.noEntities" });
  const searchPlaceholder = formatMessage({ id: "memory.entity.searchPlaceholder" });
  const typeFilterLabel = formatMessage({ id: "memory.entity.typeFilter" });
  const mergeTitle = formatMessage({ id: "memory.entity.mergeTitle" });
  const mergeBtnLabel = formatMessage({ id: "memory.entity.mergeBtn" });
  const deleteTooltip = formatMessage({ id: "memory.entity.deleteTooltip" });
  const selectedEntitiesLabel = formatMessage({ id: "memory.entity.selectedEntities" });
  const targetNameLabel = formatMessage({ id: "memory.entity.targetNameLabel" });
  const targetNamePlaceholder = formatMessage({ id: "memory.entity.targetNamePlaceholder" });
  const selectAllLabel = formatMessage({ id: "memory.entity.selectAll" });
  const clearLabel = formatMessage({ id: "memory.entity.clear" });
  const mergeBtnTooltip = formatMessage({ id: "memory.entity.mergeBtnTooltip" });
  const refreshLabel = formatMessage({ id: "memory.entity.refresh" });
  const [entities, setEntities] = useState<EntityItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [typeFilter, setTypeFilter] = useState<string | undefined>(undefined);
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounce(search, 100);
  const [selectedNames, setSelectedNames] = useState<Set<string>>(new Set());
  const [mergeTarget, setMergeTarget] = useState("");
  const [mergeOpen, setMergeOpen] = useState(false);
  const [merging, setMerging] = useState(false);
  const loadAbortRef = useRef<AbortController | null>(null);
  const { enqueueUndo } = useUndoStack();
  const mergeDesc = formatMessage({ id: "memory.entity.mergeDesc" }, { count: selectedNames.size });
  const selectedCountLabel = formatMessage(
    { id: "memory.entity.selectedCount" },
    { count: selectedNames.size },
  );

  const load = useCallback(async () => {
    loadAbortRef.current?.abort();
    const controller = new AbortController();
    loadAbortRef.current = controller;
    setLoading(true);
    try {
      const all = await listAllEntities(userId, {
        entityType: typeFilter,
        signal: controller.signal,
      });
      if (!controller.signal.aborted) setEntities(all);
    } catch (err) {
      if (!isRequestCanceled(err)) {
        message.error(
          formatApiErrorMessage(err, formatMessage({ id: "memory.entity.loadFailed" })),
        );
      }
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, [userId, typeFilter, formatMessage]);

  useEffect(() => () => loadAbortRef.current?.abort(), []);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      void load();
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [load]);

  const filtered = useMemo(() => {
    const q = debouncedSearch.toLowerCase();
    return q ? entities.filter((e) => e.name.toLowerCase().includes(q)) : entities;
  }, [debouncedSearch, entities]);

  const handleDelete = async (name: string) => {
    Modal.confirm({
      width: "min(92vw, 680px)",
      title: formatMessage({ id: "memory.entity.deleteTitle" }, { name }),
      okType: "danger",
      onOk: async () => {
        try {
          await deleteEntity(name, userId);
          await load();
          message.success(formatMessage({ id: "memory.entity.deleted" }));
        } catch (err) {
          message.error(
            formatApiErrorMessage(err, formatMessage({ id: "memory.entity.deleteFailed" })),
          );
        }
      },
    });
  };

  const handleBatchDelete = async (names: string[]) => {
    if (names.length === 0) return;
    try {
      const response = await batchDeleteEntities(names, userId);
      if (response.data.deleted > 0) {
        message.success(
          formatMessage({ id: "memory.entity.batchDeleted" }, { count: response.data.deleted }),
        );
      }
      await load();
      if (response.data.missing.length > 0) {
        message.warning(
          formatMessage(
            { id: "memory.entity.batchDeletePartial" },
            { failed: response.data.missing.length, total: names.length },
          ),
        );
      }
      setSelectedNames(new Set(response.data.missing));
    } catch {
      message.error(
        formatMessage(
          { id: "memory.entity.batchDeletePartial" },
          { failed: names.length, total: names.length },
        ),
      );
    }
  };

  const toggleSelection = (name: string) => {
    setSelectedNames((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  const selectAllVisible = () => {
    setSelectedNames(new Set(filtered.map((e) => e.name)));
  };

  const clearSelection = () => {
    setSelectedNames(new Set());
  };

  const handleMerge = async () => {
    if (selectedNames.size < 2) {
      message.warning(formatMessage({ id: "memory.entity.selectTwoToMerge" }));
      return;
    }
    if (!mergeTarget.trim()) {
      message.warning(formatMessage({ id: "memory.entity.enterTargetName" }));
      return;
    }
    const sources = Array.from(selectedNames).filter((n) => n !== mergeTarget.trim());
    if (sources.length === 0) {
      message.warning(formatMessage({ id: "memory.entity.targetDifferent" }));
      return;
    }
    setMerging(true);
    try {
      await mergeEntities(userId, sources, mergeTarget.trim());
      message.success(formatMessage({ id: "memory.entitiesMerged" }));
      if (sources.length === 1) {
        const sourceName = sources[0];
        const targetName = mergeTarget.trim();
        enqueueUndo({
          key: `entity-merge-${sourceName}-${targetName}`,
          content: `Merged "${sourceName}" into "${targetName}"`,
          onUndo: async () => {
            await mergeEntities(userId, [targetName], sourceName);
            await load();
            message.success(formatMessage({ id: "memory.mergeReverted" }));
          },
        });
      }
      setMergeOpen(false);
      setMergeTarget("");
      setSelectedNames(new Set());
      await load();
    } catch (err) {
      message.error(formatApiErrorMessage(err, formatMessage({ id: "memory.mergeFailed" })));
    } finally {
      setMerging(false);
    }
  };

  const grouped = ENTITY_TYPES.reduce<Record<string, EntityItem[]>>((acc, t) => {
    const items = filtered.filter((e) => e.entity_type === t);
    if (items.length > 0) acc[t] = items;
    return acc;
  }, {});

  return (
    <Space orientation="vertical" style={{ width: "100%" }} size="middle">
      <Row gutter={12} align="middle">
        <Col>
          <Input
            placeholder={searchPlaceholder}
            allowClear
            style={{ width: 240 }}
            onChange={(e) => setSearch(e.target.value)}
            prefix={<SearchOutlined />}
          />
        </Col>
        <Col>
          <Select
            allowClear
            placeholder={typeFilterLabel}
            style={{ width: 180 }}
            value={typeFilter}
            onChange={setTypeFilter}
            options={ENTITY_TYPES.map((t) => ({ label: t, value: t }))}
          />
        </Col>
        <Col flex="auto" />
        <Col>
          <Space>
            {selectedNames.size > 0 && (
              <>
                <span style={{ fontSize: 13, color: "var(--color-text-secondary)" }}>
                  {selectedCountLabel}
                </span>
                <Button size="small" onClick={selectAllVisible}>
                  {selectAllLabel}
                </Button>
                <Button size="small" onClick={clearSelection}>
                  {clearLabel}
                </Button>
                <Button
                  type="primary"
                  size="small"
                  icon={<MergeCellsOutlined />}
                  onClick={() => setMergeOpen(true)}
                >
                  {mergeBtnTooltip}
                </Button>
                <Popconfirm
                  title={formatMessage(
                    { id: "memory.entity.batchDeleteTitle" },
                    { count: selectedNames.size },
                  )}
                  description={formatMessage({ id: "memory.list.batchDeleteDesc" })}
                  onConfirm={() => handleBatchDelete(Array.from(selectedNames))}
                  okText="Delete"
                  okButtonProps={{ danger: true }}
                >
                  <Button danger size="small" icon={<DeleteOutlined />}>
                    {deleteTooltip}
                  </Button>
                </Popconfirm>
              </>
            )}
            <Button icon={<ReloadOutlined />} loading={loading} onClick={load}>
              {refreshLabel}
            </Button>
          </Space>
        </Col>
      </Row>

      <Spin spinning={loading}>
        {Object.keys(grouped).length === 0 ? (
          <Empty description={noEntitiesMsg} />
        ) : (
          <Collapse
            items={Object.entries(grouped).map(([type, items]) => ({
              key: type,
              label: (
                <Space>
                  <Tag color={ENTITY_TYPE_COLORS[type] ?? "default"}>{type}</Tag>
                  <Badge count={items.length} showZero color="gray" />
                </Space>
              ),
              children: (
                <div className="entity-list" role="list">
                  {items.map((ent) => (
                    <EntityRow
                      key={`${ent.entity_type}:${ent.name}`}
                      entity={ent}
                      userId={userId}
                      onDelete={handleDelete}
                      isSelected={selectedNames.has(ent.name)}
                      onToggleSelect={() => toggleSelection(ent.name)}
                    />
                  ))}
                </div>
              ),
            }))}
          />
        )}
      </Spin>

      <Modal
        title={mergeTitle}
        open={mergeOpen}
        onCancel={() => {
          setMergeOpen(false);
          setMergeTarget("");
        }}
        onOk={handleMerge}
        okText={mergeBtnLabel}
        confirmLoading={merging}
        width="min(94vw, 680px)"
        styles={{ body: { maxHeight: "calc(100vh - 220px)", overflowY: "auto" } }}
      >
        <p style={{ color: "var(--color-text-secondary)" }}>{mergeDesc}</p>
        <div style={{ marginBottom: 12 }}>
          <strong>{selectedEntitiesLabel}</strong>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 6 }}>
            {Array.from(selectedNames).map((n) => (
              <Tag key={n}>{n}</Tag>
            ))}
          </div>
        </div>
        <div>
          <strong style={{ display: "block", marginBottom: 4 }}>{targetNameLabel}</strong>
          <Input
            value={mergeTarget}
            onChange={(e) => setMergeTarget(e.target.value)}
            placeholder={targetNamePlaceholder}
          />
        </div>
      </Modal>
    </Space>
  );
}
