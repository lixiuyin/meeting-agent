import { Card, Form, Input, InputNumber, Select, Switch, Tooltip } from "antd";
import { QuestionCircleOutlined } from "@ant-design/icons";
import { motion } from "framer-motion";
import { useIntl } from "react-intl";
import { cardVariants, settingsCardStyle, twoColGrid } from "./constants";

export function IngestionTab() {
  const { formatMessage } = useIntl();

  return (
    <motion.div custom={0} variants={cardVariants} initial="hidden" animate="visible">
      {/* Speech Recognition (ASR) */}
      <Card
        title={formatMessage({ id: "settings.ingestion.asr" })}
        style={{ ...settingsCardStyle, marginBottom: 16 }}
      >
        <Form.Item
          name={["asr", "provider"]}
          label={formatMessage({ id: "settings.models.provider" })}
        >
          <Select options={[{ value: "assemblyai", label: "AssemblyAI" }]} />
        </Form.Item>
        <div style={twoColGrid}>
          <Form.Item
            name={["asr", "language"]}
            label={formatMessage({ id: "settings.ingestion.language" })}
          >
            <Input placeholder="e.g., en" />
          </Form.Item>
          <Form.Item
            name={["asr", "speech_model"]}
            label={formatMessage({ id: "settings.ingestion.speechModel" })}
          >
            <Input placeholder="e.g., universal-3-pro" />
          </Form.Item>
        </div>
        <Form.Item
          name={["asr", "assemblyai_api_key"]}
          label={
            <span>
              {formatMessage({ id: "settings.ingestion.assemblyaiApiKey" })}
              <Tooltip title={formatMessage({ id: "settings.ingestion.assemblyaiApiKeyTip" })}>
                <QuestionCircleOutlined style={{ marginLeft: 8, opacity: 0.6 }} />
              </Tooltip>
            </span>
          }
        >
          <Input.Password
            placeholder={formatMessage({ id: "settings.placeholder.environmentOnly" })}
            disabled
          />
        </Form.Item>
        <div style={twoColGrid}>
          <Form.Item
            name={["asr", "poll_interval_seconds"]}
            label={formatMessage({ id: "settings.ingestion.pollInterval" })}
          >
            <InputNumber min={1} max={30} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item
            name={["asr", "max_wait_seconds"]}
            label={formatMessage({ id: "settings.ingestion.maxWait" })}
          >
            <InputNumber min={60} max={7200} step={60} style={{ width: "100%" }} />
          </Form.Item>
        </div>
        <div style={twoColGrid}>
          <Form.Item
            name={["asr", "speaker_labels"]}
            valuePropName="checked"
            style={{ marginBottom: 0 }}
          >
            <Switch
              checkedChildren={formatMessage({ id: "common.enabled" })}
              unCheckedChildren={formatMessage({ id: "common.disabled" })}
              size="small"
            />
          </Form.Item>
          <Form.Item
            name={["asr", "language_detection"]}
            valuePropName="checked"
            style={{ marginBottom: 0 }}
          >
            <Switch
              checkedChildren={formatMessage({ id: "common.enabled" })}
              unCheckedChildren={formatMessage({ id: "common.disabled" })}
              size="small"
            />
          </Form.Item>
        </div>
      </Card>

      {/* Document OCR */}
      <Card
        title={formatMessage({ id: "settings.ingestion.ocrParsing" })}
        style={{ ...settingsCardStyle, marginBottom: 16 }}
      >
        <Form.Item
          name={["ocr", "provider"]}
          label={formatMessage({ id: "settings.ingestion.ocrProvider" })}
        >
          <Select
            options={[
              { value: "marker", label: "Marker (datalab.to)" },
              { value: "mineru", label: "MinerU (mineru.net)" },
              { value: "paddleocr", label: "PaddleOCR (aistudio)" },
            ]}
          />
        </Form.Item>
        <div style={twoColGrid}>
          <Form.Item
            name={["ocr", "language"]}
            label={formatMessage({ id: "settings.ingestion.language" })}
          >
            <Input placeholder="e.g., en, zh" />
          </Form.Item>
          <Form.Item
            name={["ocr", "dpi"]}
            label={formatMessage({ id: "settings.ingestion.scanDpi" })}
          >
            <InputNumber min={72} max={600} step={50} style={{ width: "100%" }} />
          </Form.Item>
        </div>
        <div style={twoColGrid}>
          <Form.Item
            name={["ocr", "http_timeout_seconds"]}
            label={formatMessage({ id: "settings.ingestion.httpTimeout" })}
          >
            <InputNumber min={5} max={600} step={10} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item
            name={["ocr", "poll_interval_seconds"]}
            label={formatMessage({ id: "settings.ingestion.pollInterval" })}
          >
            <InputNumber min={0.5} max={30} step={0.5} style={{ width: "100%" }} />
          </Form.Item>
        </div>

        {/* Per-provider credentials */}
        <div style={twoColGrid}>
          <Form.Item
            name={["ocr", "marker_base_url"]}
            label={formatMessage({ id: "settings.ingestion.markerBaseUrl" })}
          >
            <Input placeholder="https://www.datalab.to/api/v1/marker" />
          </Form.Item>
          <Form.Item
            name={["ocr", "marker_max_wait_seconds"]}
            label={formatMessage({ id: "settings.ingestion.markerMaxWait" })}
          >
            <InputNumber min={10} max={1800} step={30} style={{ width: "100%" }} />
          </Form.Item>
        </div>
        <Form.Item
          name={["ocr", "marker_api_key"]}
          label={formatMessage({ id: "settings.ingestion.markerApiKey" })}
        >
          <Input.Password
            placeholder={formatMessage({ id: "settings.placeholder.keepExisting" })}
          />
        </Form.Item>

        <div style={twoColGrid}>
          <Form.Item
            name={["ocr", "mineru_base_url"]}
            label={formatMessage({ id: "settings.ingestion.mineruBaseUrl" })}
          >
            <Input placeholder="https://mineru.net/api/v4/extract/task" />
          </Form.Item>
          <Form.Item
            name={["ocr", "mineru_max_wait_seconds"]}
            label={formatMessage({ id: "settings.ingestion.mineruMaxWait" })}
          >
            <InputNumber min={10} max={3600} step={60} style={{ width: "100%" }} />
          </Form.Item>
        </div>
        <Form.Item
          name={["ocr", "mineru_api_key"]}
          label={formatMessage({ id: "settings.ingestion.mineruApiKey" })}
        >
          <Input.Password
            placeholder={formatMessage({ id: "settings.placeholder.keepExisting" })}
          />
        </Form.Item>

        <div style={twoColGrid}>
          <Form.Item
            name={["ocr", "paddleocr_base_url"]}
            label={formatMessage({ id: "settings.ingestion.paddleBaseUrl" })}
          >
            <Input placeholder="" />
          </Form.Item>
          <Form.Item
            name={["ocr", "paddleocr_api_key"]}
            label={formatMessage({ id: "settings.ingestion.paddleApiKey" })}
          >
            <Input.Password
              placeholder={formatMessage({ id: "settings.placeholder.keepExisting" })}
            />
          </Form.Item>
        </div>
      </Card>

      {/* Parser Limits */}
      <Card
        title={formatMessage({ id: "settings.ingestion.parserLimits" })}
        style={settingsCardStyle}
      >
        <div style={twoColGrid}>
          <Form.Item
            name={["parser", "max_parse_pages"]}
            label={formatMessage({ id: "settings.ingestion.maxPages" })}
          >
            <InputNumber min={1} max={10000} step={100} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item
            name={["parser", "parse_timeout_seconds"]}
            label={formatMessage({ id: "settings.ingestion.parseTimeout" })}
          >
            <InputNumber min={30} max={3600} step={60} style={{ width: "100%" }} />
          </Form.Item>
        </div>
        <div style={twoColGrid}>
          <Form.Item
            name={["parser", "timeout_per_mb_seconds"]}
            label={formatMessage({ id: "settings.ingestion.timeoutPerMb" })}
          >
            <InputNumber min={1} max={60} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item
            name={["parser", "timeout_max_seconds"]}
            label={formatMessage({ id: "settings.ingestion.maxTimeout" })}
          >
            <InputNumber min={30} max={3600} step={60} style={{ width: "100%" }} />
          </Form.Item>
        </div>

        <div style={twoColGrid}>
          <Form.Item
            name={["parser", "max_images_per_page"]}
            label={formatMessage({ id: "settings.ingestion.maxImagesPerPage" })}
          >
            <InputNumber min={0} max={100} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item
            name={["parser", "max_image_bytes"]}
            label={formatMessage({ id: "settings.ingestion.maxImageBytes" })}
          >
            <InputNumber min={1024} max={67_108_864} step={1_048_576} style={{ width: "100%" }} />
          </Form.Item>
        </div>

        <div style={twoColGrid}>
          <Form.Item
            name={["parser", "doc_clean_repetition_min_pages"]}
            label={formatMessage({ id: "settings.ingestion.repetitionMinPages" })}
          >
            <InputNumber min={1} max={50} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item
            name={["parser", "doc_clean_repetition_min_ratio"]}
            label={formatMessage({ id: "settings.ingestion.repetitionMinRatio" })}
          >
            <InputNumber min={0.1} max={1} step={0.05} style={{ width: "100%" }} />
          </Form.Item>
        </div>
        <div style={twoColGrid}>
          <Form.Item
            name={["parser", "doc_clean_header_footer_max_lines"]}
            label={formatMessage({ id: "settings.ingestion.headerFooterMaxLines" })}
          >
            <InputNumber min={0} max={20} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item
            name={["parser", "doc_clean_repetition_max_line_length"]}
            label={formatMessage({ id: "settings.ingestion.repetitionMaxLineLen" })}
          >
            <InputNumber min={40} max={1000} step={10} style={{ width: "100%" }} />
          </Form.Item>
        </div>
      </Card>
    </motion.div>
  );
}
