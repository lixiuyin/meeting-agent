import { Card, Form, Input, InputNumber, Select, Switch } from "antd";
import { motion } from "framer-motion";
import { useIntl } from "react-intl";
import { cardVariants, settingsCardStyle, twoColGrid } from "./constants";
import type { SettingsBindings } from "./types";

interface Props {
  bindings: SettingsBindings;
}

export function SearchUploadTab({ bindings }: Props) {
  const { formatMessage } = useIntl();
  const disabledLabel = formatMessage({ id: "settings.models.disabled" });

  return (
    <motion.div custom={0} variants={cardVariants} initial="hidden" animate="visible">
      {/* Web Search */}
      <Card
        title={formatMessage({ id: "settings.search.webSearch" })}
        style={{ ...settingsCardStyle, marginBottom: 16 }}
      >
        <Form.Item
          name={["search", "binding"]}
          label={formatMessage({ id: "settings.models.provider" })}
        >
          <Select
            allowClear
            placeholder={disabledLabel}
            options={bindings.search.map((b) => ({ label: b || disabledLabel, value: b }))}
          />
        </Form.Item>
        <Form.Item
          name={["search", "region"]}
          label={formatMessage({ id: "settings.search.region" })}
          rules={[{ required: true }]}
        >
          <Input placeholder="e.g., wt-wt, us-en" />
        </Form.Item>
        <div style={twoColGrid}>
          <Form.Item
            name={["search", "max_results"]}
            label={formatMessage({ id: "settings.search.maxResults" })}
            rules={[{ required: true }]}
          >
            <InputNumber min={1} max={20} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item
            name={["search", "timeout"]}
            label={formatMessage({ id: "settings.search.timeout" })}
            rules={[{ required: true }]}
          >
            <InputNumber min={1} max={60} style={{ width: "100%" }} />
          </Form.Item>
        </div>
        <Form.Item
          name={["search", "web_search_timeout_s"]}
          label={formatMessage({ id: "settings.search.webSearchTimeout" })}
        >
          <InputNumber min={1} max={60} step={1} style={{ width: "100%" }} />
        </Form.Item>
        <Form.Item
          name={["search", "api_key"]}
          label={formatMessage({ id: "settings.models.apiKey" })}
        >
          <Input.Password placeholder="Leave blank to keep existing" />
        </Form.Item>
      </Card>

      {/* File Upload Options */}
      <Card title={formatMessage({ id: "settings.search.fileUpload" })} style={settingsCardStyle}>
        <Form.Item
          name={["upload", "max_size_mb"]}
          label={formatMessage({ id: "settings.search.maxSizeMb" })}
          rules={[{ required: true }]}
        >
          <InputNumber min={10} max={2000} step={10} style={{ width: "100%" }} />
        </Form.Item>

        <Form.Item
          name={["upload", "auto_summarize_files"]}
          valuePropName="checked"
          style={{ marginBottom: 8 }}
        >
          <div style={{ display: "flex", alignItems: "center" }}>
            <Switch
              checkedChildren={formatMessage({ id: "common.enabled" })}
              unCheckedChildren={formatMessage({ id: "common.disabled" })}
            />
            <span style={{ marginLeft: 8, color: "var(--color-text-secondary)" }}>
              {formatMessage({ id: "settings.search.autoSummarize" })}
            </span>
          </div>
        </Form.Item>

        <Form.Item
          name={["upload", "multimodal_captioning_enabled"]}
          valuePropName="checked"
          style={{ marginBottom: 8 }}
        >
          <div style={{ display: "flex", alignItems: "center" }}>
            <Switch
              checkedChildren={formatMessage({ id: "common.enabled" })}
              unCheckedChildren={formatMessage({ id: "common.disabled" })}
            />
            <span style={{ marginLeft: 8, color: "var(--color-text-secondary)" }}>
              {formatMessage({ id: "settings.search.imageCaptioning" })}
            </span>
          </div>
        </Form.Item>

        <Form.Item
          name={["upload", "ocr_dedup_enabled"]}
          valuePropName="checked"
          style={{ marginBottom: 8 }}
        >
          <div style={{ display: "flex", alignItems: "center" }}>
            <Switch
              checkedChildren={formatMessage({ id: "common.enabled" })}
              unCheckedChildren={formatMessage({ id: "common.disabled" })}
            />
            <span style={{ marginLeft: 8, color: "var(--color-text-secondary)" }}>
              {formatMessage({ id: "settings.search.ocrDedup" })}
            </span>
          </div>
        </Form.Item>

        <div style={twoColGrid}>
          <Form.Item
            name={["upload", "ocr_dedup_timeout_seconds"]}
            label={formatMessage({ id: "settings.search.ocrDedupTimeout" })}
          >
            <InputNumber min={1} max={60} step={1} style={{ width: "100%" }} />
          </Form.Item>
        </div>

        <Form.Item
          name={["upload", "video_keyframes_enabled"]}
          valuePropName="checked"
          style={{ marginBottom: 0 }}
        >
          <div style={{ display: "flex", alignItems: "center" }}>
            <Switch
              checkedChildren={formatMessage({ id: "common.enabled" })}
              unCheckedChildren={formatMessage({ id: "common.disabled" })}
            />
            <span style={{ marginLeft: 8, color: "var(--color-text-secondary)" }}>
              {formatMessage({ id: "settings.search.videoKeyframes" })}
            </span>
          </div>
        </Form.Item>
      </Card>
    </motion.div>
  );
}
