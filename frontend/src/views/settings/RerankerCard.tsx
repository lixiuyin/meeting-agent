import { Card, Form, Input, InputNumber, Select } from "antd";
import { useIntl } from "react-intl";
import { settingsCardStyle, twoColGrid } from "./constants";
import type { SettingsBindings } from "./types";

interface Props {
  bindings: SettingsBindings;
}

export function RerankerCard({ bindings }: Props) {
  const { formatMessage } = useIntl();
  const disabledLabel = formatMessage({ id: "settings.models.disabled" });

  return (
    <Card
      title={formatMessage({ id: "settings.rag.reranker" })}
      style={{ ...settingsCardStyle, marginBottom: 16 }}
    >
      <div style={twoColGrid}>
        <Form.Item
          name={["rag", "reranker_binding"]}
          label={formatMessage({ id: "settings.rag.rerankerProvider" })}
        >
          <Select
            allowClear
            placeholder={disabledLabel}
            options={bindings.reranker.map((b) => ({ label: b || disabledLabel, value: b }))}
          />
        </Form.Item>
        <Form.Item
          name={["rag", "reranker_top_n"]}
          label={formatMessage({ id: "settings.rag.rerankerTopN" })}
        >
          <InputNumber min={1} max={20} style={{ width: "100%" }} />
        </Form.Item>
      </div>

      <Form.Item
        name={["rag", "reranker_model"]}
        label={formatMessage({ id: "settings.rag.rerankerModel" })}
      >
        <Input placeholder="e.g., cohere/rerank-4-pro" />
      </Form.Item>

      <Form.Item
        name={["rag", "reranker_base_url"]}
        label={formatMessage({ id: "settings.rag.rerankerBaseUrl" })}
      >
        <Input placeholder="e.g., https://openrouter.ai/api/v1" />
      </Form.Item>

      <Form.Item
        name={["rag", "reranker_api_key"]}
        label={formatMessage({ id: "settings.rag.rerankerApiKey" })}
      >
        <Input.Password placeholder="Leave blank to keep existing" />
      </Form.Item>

      <div style={twoColGrid}>
        <Form.Item
          name={["rag", "reranker_min_score"]}
          label={formatMessage({ id: "settings.rag.rerankerMinScore" })}
        >
          <InputNumber min={0} max={1} step={0.05} style={{ width: "100%" }} />
        </Form.Item>
        <Form.Item
          name={["rag", "reranker_timeout_seconds"]}
          label={formatMessage({ id: "settings.rag.rerankerTimeout" })}
        >
          <InputNumber min={1} max={300} step={5} style={{ width: "100%" }} />
        </Form.Item>
      </div>

      <div style={twoColGrid}>
        <Form.Item
          name={["rag", "fetch_multiplier"]}
          label={formatMessage({ id: "settings.rag.fetchMultiplier" })}
        >
          <InputNumber min={1} max={10} style={{ width: "100%" }} />
        </Form.Item>
        <Form.Item
          name={["rag", "persist_interval_seconds"]}
          label={formatMessage({ id: "settings.rag.persistInterval" })}
        >
          <InputNumber min={1} max={300} step={5} style={{ width: "100%" }} />
        </Form.Item>
      </div>
    </Card>
  );
}
