import { Card, Form, Input, InputNumber, Select, Slider, Tooltip } from "antd";
import { QuestionCircleOutlined } from "@ant-design/icons";
import { motion } from "framer-motion";
import { useIntl } from "react-intl";
import { cardVariants, settingsCardStyle, twoColGrid } from "./constants";
import type { SettingsBindings } from "./types";

interface Props {
  bindings: SettingsBindings;
}

export function AiModelsTab({ bindings }: Props) {
  const { formatMessage } = useIntl();

  return (
    <motion.div custom={0} variants={cardVariants} initial="hidden" animate="visible">
      <Card
        title={formatMessage({ id: "settings.models.llm" })}
        style={{ ...settingsCardStyle, marginBottom: 16 }}
      >
        <Form.Item
          name={["llm", "binding"]}
          label={formatMessage({ id: "settings.models.provider" })}
          rules={[{ required: true }]}
        >
          <Select options={bindings.llm.map((b) => ({ label: b, value: b }))} />
        </Form.Item>
        <Form.Item
          name={["llm", "model"]}
          label={formatMessage({ id: "settings.models.model" })}
          rules={[{ required: true }]}
        >
          <Input placeholder="e.g., gpt-4o-mini, claude-3-haiku" />
        </Form.Item>
        <div style={twoColGrid}>
          <Form.Item
            name={["llm", "temperature"]}
            label={
              <span>
                {formatMessage({ id: "settings.models.temperature" })}
                <Tooltip title={formatMessage({ id: "settings.models.temperatureTip" })}>
                  <QuestionCircleOutlined style={{ marginLeft: 8, opacity: 0.6 }} />
                </Tooltip>
              </span>
            }
            rules={[{ required: true }]}
          >
            <InputNumber min={0} max={2} step={0.1} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item
            name={["llm", "max_tokens"]}
            label={formatMessage({ id: "settings.models.maxTokens" })}
            rules={[{ required: true }]}
          >
            <InputNumber min={1} max={16384} step={128} style={{ width: "100%" }} />
          </Form.Item>
        </div>
        <Form.Item name={["llm", "host"]} label={formatMessage({ id: "settings.models.host" })}>
          <Input placeholder="e.g., http://localhost:11434 for Ollama" />
        </Form.Item>
        <Form.Item
          name={["llm", "base_url"]}
          label={formatMessage({ id: "settings.models.baseUrl" })}
        >
          <Input placeholder="e.g., https://api.openai.com/v1" />
        </Form.Item>
        <Form.Item
          name={["llm", "api_key"]}
          label={formatMessage({ id: "settings.models.apiKey" })}
        >
          <Input.Password placeholder="Leave blank to keep existing" />
        </Form.Item>
      </Card>

      <Card
        title={formatMessage({ id: "settings.models.embedding" })}
        style={{ ...settingsCardStyle, marginBottom: 16 }}
      >
        <Form.Item
          name={["embedding", "binding"]}
          label={
            <span>
              {formatMessage({ id: "settings.models.provider" })}
              <Tooltip title={formatMessage({ id: "settings.models.embeddingChangeWarning" })}>
                <QuestionCircleOutlined style={{ marginLeft: 8, opacity: 0.5 }} />
              </Tooltip>
            </span>
          }
          rules={[{ required: true }]}
        >
          <Select options={bindings.embedding.map((b) => ({ label: b, value: b }))} />
        </Form.Item>
        <Form.Item
          name={["embedding", "model"]}
          label={
            <span>
              {formatMessage({ id: "settings.models.model" })}
              <Tooltip title={formatMessage({ id: "settings.models.embeddingChangeWarning" })}>
                <QuestionCircleOutlined style={{ marginLeft: 8, opacity: 0.5 }} />
              </Tooltip>
            </span>
          }
          rules={[{ required: true }]}
        >
          <Input placeholder="e.g., text-embedding-3-small" />
        </Form.Item>
        <Form.Item
          name={["embedding", "dimension"]}
          label={
            <span>
              {formatMessage({ id: "settings.models.dimension" })}
              <Tooltip title={formatMessage({ id: "settings.models.embeddingChangeWarning" })}>
                <QuestionCircleOutlined style={{ marginLeft: 8, opacity: 0.5 }} />
              </Tooltip>
            </span>
          }
          rules={[{ required: true }]}
        >
          <InputNumber min={128} max={4096} step={128} style={{ width: "100%" }} />
        </Form.Item>
        <Form.Item
          name={["embedding", "host"]}
          label={
            <span>
              {formatMessage({ id: "settings.models.host" })}
              <Tooltip title={formatMessage({ id: "settings.models.embeddingChangeWarning" })}>
                <QuestionCircleOutlined style={{ marginLeft: 8, opacity: 0.5 }} />
              </Tooltip>
            </span>
          }
        >
          <Input placeholder="e.g., http://localhost:11434 for Ollama" />
        </Form.Item>
        <Form.Item
          name={["embedding", "base_url"]}
          label={
            <span>
              {formatMessage({ id: "settings.models.baseUrl" })}
              <Tooltip title={formatMessage({ id: "settings.models.embeddingChangeWarning" })}>
                <QuestionCircleOutlined style={{ marginLeft: 8, opacity: 0.5 }} />
              </Tooltip>
            </span>
          }
        >
          <Input placeholder="e.g., https://api.openai.com/v1" />
        </Form.Item>
        <Form.Item
          name={["embedding", "api_key"]}
          label={formatMessage({ id: "settings.models.apiKey" })}
        >
          <Input.Password placeholder="Leave blank to keep existing" />
        </Form.Item>
      </Card>

      <Card
        title={formatMessage({ id: "settings.models.vision" })}
        style={{ ...settingsCardStyle, marginBottom: 16 }}
      >
        <Form.Item
          name={["vision", "model"]}
          label={formatMessage({ id: "settings.models.model" })}
        >
          <Input placeholder="e.g., gpt-4o-mini (empty = disabled)" />
        </Form.Item>
        <div style={twoColGrid}>
          <Form.Item
            name={["vision", "base_url"]}
            label={formatMessage({ id: "settings.models.baseUrl" })}
          >
            <Input placeholder="e.g., https://api.openai.com/v1" />
          </Form.Item>
          <Form.Item
            name={["vision", "api_key"]}
            label={formatMessage({ id: "settings.models.apiKey" })}
          >
            <Input.Password placeholder="Leave blank to keep existing" />
          </Form.Item>
        </div>
        <div style={twoColGrid}>
          <Form.Item
            name={["vision", "retry_max_attempts"]}
            label={formatMessage({ id: "settings.models.retryMaxAttempts" })}
          >
            <InputNumber min={1} max={10} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item
            name={["vision", "retry_base_delay_seconds"]}
            label={formatMessage({ id: "settings.models.baseDelay" })}
          >
            <InputNumber min={0.1} max={10} step={0.1} style={{ width: "100%" }} />
          </Form.Item>
        </div>
        <Form.Item
          name={["vision", "retry_max_delay_seconds"]}
          label={formatMessage({ id: "settings.models.maxRetryDelay" })}
        >
          <InputNumber min={1} max={60} step={0.5} style={{ width: "100%" }} />
        </Form.Item>
        <div style={twoColGrid}>
          <Form.Item
            name={["vision", "caption_min_chars"]}
            label={formatMessage({ id: "settings.models.captionMinChars" })}
          >
            <InputNumber min={1} max={500} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item
            name={["vision", "ocr_min_chars"]}
            label={formatMessage({ id: "settings.models.ocrMinChars" })}
          >
            <InputNumber min={1} max={200} style={{ width: "100%" }} />
          </Form.Item>
        </div>
      </Card>

      <Card title={formatMessage({ id: "settings.models.tts" })} style={settingsCardStyle}>
        <Form.Item
          name={["tts", "binding"]}
          label={formatMessage({ id: "settings.models.provider" })}
        >
          <Select
            allowClear
            placeholder={formatMessage({ id: "settings.models.disabled" })}
            options={[
              { value: "", label: formatMessage({ id: "settings.models.disabled" }) },
              { value: "openai", label: "OpenAI" },
              { value: "edge", label: "Edge TTS" },
              { value: "cohere", label: "Cohere" },
              { value: "local", label: "Local" },
            ]}
          />
        </Form.Item>
        <Form.Item noStyle shouldUpdate={(prev, curr) => prev.tts?.binding !== curr.tts?.binding}>
          {({ getFieldValue }) =>
            getFieldValue(["tts", "binding"]) ? (
              <>
                <div style={twoColGrid}>
                  <Form.Item
                    name={["tts", "model"]}
                    label={formatMessage({ id: "settings.models.model" })}
                  >
                    <Input placeholder="e.g., tts-1" />
                  </Form.Item>
                  <Form.Item name={["tts", "voice"]} label="Voice">
                    <Input placeholder="e.g., alloy, echo" />
                  </Form.Item>
                </div>
                <Form.Item
                  name={["tts", "base_url"]}
                  label={formatMessage({ id: "settings.models.baseUrl" })}
                >
                  <Input placeholder="e.g., https://api.openai.com/v1" />
                </Form.Item>
                <Form.Item
                  name={["tts", "api_key"]}
                  label={formatMessage({ id: "settings.models.apiKey" })}
                >
                  <Input.Password placeholder="Leave blank to keep existing" />
                </Form.Item>
                <Form.Item name={["tts", "speed"]} label="Speed">
                  <Slider min={0.25} max={4} step={0.1} />
                </Form.Item>
              </>
            ) : (
              <p style={{ color: "var(--color-text-secondary)", margin: 0 }}>
                {formatMessage({ id: "settings.models.ttsSelectProvider" })}
              </p>
            )
          }
        </Form.Item>
      </Card>
    </motion.div>
  );
}
