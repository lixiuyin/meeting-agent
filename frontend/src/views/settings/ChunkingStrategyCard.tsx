import { Card, Form, InputNumber, Select } from "antd";
import { useIntl } from "react-intl";
import { settingsCardStyle, twoColGrid } from "./constants";
import SettingSwitch from "./SettingSwitch";

export function ChunkingStrategyCard() {
  const { formatMessage } = useIntl();

  return (
    <Card
      title={formatMessage({ id: "settings.rag.chunkingStrategy" })}
      style={{ ...settingsCardStyle, marginBottom: 16 }}
    >
      <Form.Item
        name={["rag", "parent_child_enabled"]}
        valuePropName="checked"
        style={{ marginBottom: 16 }}
      >
        <SettingSwitch label={formatMessage({ id: "settings.rag.parentChildChunking" })} />
      </Form.Item>

      <Form.Item
        name={["rag", "non_text_chunking_strategy"]}
        label={formatMessage({ id: "settings.rag.nonTextChunkingStrategy" })}
        extra={formatMessage({ id: "settings.rag.nonTextChunkingStrategyHelp" })}
      >
        <Select
          options={[
            {
              value: "native",
              label: formatMessage({ id: "settings.rag.nonTextChunkingStrategyNative" }),
            },
            {
              value: "text",
              label: formatMessage({ id: "settings.rag.nonTextChunkingStrategyText" }),
            },
          ]}
        />
      </Form.Item>

      <Form.Item
        noStyle
        shouldUpdate={(prev, curr) =>
          prev.rag?.parent_child_enabled !== curr.rag?.parent_child_enabled
        }
      >
        {({ getFieldValue }) =>
          getFieldValue(["rag", "parent_child_enabled"]) ? (
            <div style={twoColGrid}>
              <Form.Item
                name={["rag", "child_chunk_size_tokens"]}
                label={formatMessage({ id: "settings.rag.childChunkSize" })}
              >
                <InputNumber min={64} max={2048} step={64} style={{ width: "100%" }} />
              </Form.Item>
              <Form.Item
                name={["rag", "child_chunk_overlap_tokens"]}
                label={formatMessage({ id: "settings.rag.childOverlap" })}
              >
                <InputNumber min={0} max={512} step={16} style={{ width: "100%" }} />
              </Form.Item>
            </div>
          ) : null
        }
      </Form.Item>
    </Card>
  );
}
