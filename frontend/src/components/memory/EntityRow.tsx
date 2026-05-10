import { useState } from "react";
import { useIntl } from "react-intl";
import { Button, Checkbox, Collapse, List, Space, Spin, Tag, Tooltip, Typography } from "antd";
import { DeleteOutlined, NodeIndexOutlined } from "@ant-design/icons";
import { getEntity, type EntityItem, type EntityRelation } from "../../api/client";

const ENTITY_TYPE_COLORS: Record<string, string> = {
  person: "blue",
  project: "purple",
  topic: "cyan",
  organization: "gold",
  tool: "green",
  concept: "orange",
  location: "red",
};

function RelationBadge({ rel }: { rel: EntityRelation }) {
  const arrow = rel.direction === "outgoing" ? "→" : "←";
  return (
    <Tag color={ENTITY_TYPE_COLORS[rel.other_type] ?? "default"}>
      {arrow} {rel.predicate} {rel.other_name}
    </Tag>
  );
}

export interface EntityRowProps {
  entity: EntityItem;
  userId: string;
  onDelete: (name: string) => void;
  isSelected: boolean;
  onToggleSelect: () => void;
}

export default function EntityRow({
  entity,
  userId,
  onDelete,
  isSelected,
  onToggleSelect,
}: EntityRowProps) {
  const { formatMessage } = useIntl();
  const deleteTooltip = formatMessage({ id: "memory.entity.deleteTooltip" });
  const showRelationsLabel = formatMessage({ id: "memory.entity.showRelations" });
  const noRelationsLabel = formatMessage({ id: "memory.entity.noRelations" });

  const [relations, setRelations] = useState<EntityRelation[] | null>(null);
  const [loadingRel, setLoadingRel] = useState(false);

  const { Text } = Typography;

  const loadRelations = async () => {
    if (relations !== null) return;
    setLoadingRel(true);
    try {
      const res = await getEntity(entity.name, userId);
      setRelations(res.data.relations);
    } catch {
      setRelations([]);
    } finally {
      setLoadingRel(false);
    }
  };

  return (
    <List.Item
      actions={[
        <Tooltip key="del" title={deleteTooltip}>
          <Button
            type="text"
            danger
            icon={<DeleteOutlined />}
            aria-label={deleteTooltip}
            onClick={() => onDelete(entity.name)}
          />
        </Tooltip>,
      ]}
    >
      <List.Item.Meta
        avatar={<Checkbox checked={isSelected} onChange={onToggleSelect} />}
        title={
          <Space size={4}>
            <Text strong>{entity.name}</Text>
            {entity.description && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                — {entity.description}
              </Text>
            )}
            <Tag color="default">x{entity.mention_count}</Tag>
          </Space>
        }
        description={
          <Collapse
            ghost
            size="small"
            onChange={(keys) => {
              if (keys.length > 0) void loadRelations();
            }}
            items={[
              {
                key: "rel",
                label: (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    <NodeIndexOutlined /> {showRelationsLabel}
                  </Text>
                ),
                children: loadingRel ? (
                  <Spin size="small" />
                ) : relations && relations.length > 0 ? (
                  <Space wrap>
                    {relations.map((r) => (
                      <RelationBadge key={`${r.other_id}-${r.predicate}`} rel={r} />
                    ))}
                  </Space>
                ) : (
                  <Text type="secondary">{noRelationsLabel}</Text>
                ),
              },
            ]}
          />
        }
      />
    </List.Item>
  );
}

export { ENTITY_TYPE_COLORS };
