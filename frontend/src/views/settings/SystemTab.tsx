import {
  Alert,
  Button,
  Card,
  Descriptions,
  Divider,
  Form,
  Input,
  InputNumber,
  Tag,
  Typography,
} from "antd";
import { InfoCircleOutlined, ReloadOutlined, ThunderboltOutlined } from "@ant-design/icons";
import { motion } from "framer-motion";
import { useIntl } from "react-intl";
import { cardVariants, settingsCardStyle, twoColGrid } from "./constants";

const { Text } = Typography;

interface Props {
  rebuilding: boolean;
  rebuildingMultimodal: boolean;
  reloading: boolean;
  onRebuildVectors: () => void;
  onRebuildMultimodal: () => void;
  onReloadConfig: () => void;
}

function ReadOnlyValue({ name, placeholder }: { name: string[]; placeholder?: string }) {
  return (
    <Form.Item noStyle shouldUpdate>
      {({ getFieldValue }) => {
        const val = getFieldValue(name);
        const str = val === undefined || val === null || val === "" ? null : String(val);
        return (
          <span style={{ color: str ? undefined : "var(--color-text-tertiary)" }}>
            {str ?? placeholder ?? "—"}
          </span>
        );
      }}
    </Form.Item>
  );
}

function ReadOnlyTag({ name }: { name: string[] }) {
  return (
    <Form.Item noStyle shouldUpdate>
      {({ getFieldValue }) => <Tag color="blue">{String(getFieldValue(name) ?? "")}</Tag>}
    </Form.Item>
  );
}

export function SystemTab({
  rebuilding,
  rebuildingMultimodal,
  reloading,
  onRebuildVectors,
  onRebuildMultimodal,
  onReloadConfig,
}: Props) {
  const { formatMessage } = useIntl();

  return (
    <motion.div custom={0} variants={cardVariants} initial="hidden" animate="visible">
      <Card
        title={formatMessage({ id: "settings.system.retention" })}
        style={{ ...settingsCardStyle, marginBottom: 16 }}
      >
        <div style={twoColGrid}>
          <Form.Item
            name={["retention", "chat_message_retention_days"]}
            label={formatMessage({ id: "settings.system.chatRetention" })}
          >
            <InputNumber min={1} max={3650} step={30} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item
            name={["retention", "decay_state_retention_days"]}
            label={formatMessage({ id: "settings.system.decayRetention" })}
          >
            <InputNumber min={1} max={3650} step={30} style={{ width: "100%" }} />
          </Form.Item>
        </div>
      </Card>

      <Card
        title={formatMessage({ id: "settings.system.serverInfo" })}
        style={{ ...settingsCardStyle, marginBottom: 16 }}
      >
        <Alert
          type="warning"
          showIcon
          icon={<InfoCircleOutlined />}
          style={{ marginBottom: 12, borderRadius: 8 }}
          title={formatMessage({ id: "settings.system.readonlyNote" })}
        />
        <Descriptions column={1} bordered size="small" labelStyle={{ width: 220 }}>
          <Descriptions.Item label={formatMessage({ id: "settings.system.environment" })}>
            <ReadOnlyTag name={["server", "environment"]} />
          </Descriptions.Item>
          <Descriptions.Item label={formatMessage({ id: "settings.system.host" })}>
            <ReadOnlyValue name={["server", "host"]} />
          </Descriptions.Item>
          <Descriptions.Item label={formatMessage({ id: "settings.system.port" })}>
            <ReadOnlyValue name={["server", "port"]} />
          </Descriptions.Item>
          <Descriptions.Item label={formatMessage({ id: "settings.system.corsOrigins" })}>
            <ReadOnlyValue
              name={["server", "cors_origins"]}
              placeholder={formatMessage({ id: "settings.system.notConfigured" })}
            />
          </Descriptions.Item>
        </Descriptions>
      </Card>

      <Card
        title={
          <span>
            {formatMessage({ id: "settings.system.securityHeaders" })}
            <Tag color="default" style={{ marginLeft: 8, fontSize: 11 }}>
              {formatMessage({ id: "settings.system.readOnlyTag" })}
            </Tag>
          </span>
        }
        style={{ ...settingsCardStyle, marginBottom: 16 }}
      >
        <Descriptions column={1} bordered size="small" labelStyle={{ width: 260 }}>
          <Descriptions.Item
            label={formatMessage({ id: "settings.system.securityHeadersEnabled" })}
          >
            <ReadOnlyTag name={["server", "security_headers_enabled"]} />
          </Descriptions.Item>
          <Descriptions.Item label={formatMessage({ id: "settings.system.hstsMaxAge" })}>
            <ReadOnlyValue name={["server", "security_hsts_max_age"]} />
          </Descriptions.Item>
          <Descriptions.Item label={formatMessage({ id: "settings.system.frameOptions" })}>
            <ReadOnlyValue name={["server", "security_frame_options"]} />
          </Descriptions.Item>
          <Descriptions.Item label={formatMessage({ id: "settings.system.referrerPolicy" })}>
            <ReadOnlyValue name={["server", "security_referrer_policy"]} />
          </Descriptions.Item>
          <Descriptions.Item label={formatMessage({ id: "settings.system.csp" })}>
            <ReadOnlyValue name={["server", "security_csp"]} />
          </Descriptions.Item>
        </Descriptions>
      </Card>

      <Card
        title={
          <span>
            {formatMessage({ id: "settings.system.trustedProxies" })}
            <Tag color="default" style={{ marginLeft: 8, fontSize: 11 }}>
              {formatMessage({ id: "settings.system.readOnlyTag" })}
            </Tag>
          </span>
        }
        style={{ ...settingsCardStyle, marginBottom: 16 }}
      >
        <Descriptions column={1} bordered size="small" labelStyle={{ width: 260 }}>
          <Descriptions.Item label={formatMessage({ id: "settings.system.trustedProxiesLabel" })}>
            <ReadOnlyValue
              name={["server", "trusted_proxies"]}
              placeholder={formatMessage({ id: "settings.system.notConfigured" })}
            />
          </Descriptions.Item>
          <Descriptions.Item label={formatMessage({ id: "settings.system.trustedHostsLabel" })}>
            <ReadOnlyValue
              name={["server", "trusted_hosts"]}
              placeholder={formatMessage({ id: "settings.system.notConfigured" })}
            />
          </Descriptions.Item>
        </Descriptions>
      </Card>

      <Card
        title={formatMessage({ id: "settings.system.secrets" })}
        style={{ ...settingsCardStyle, marginBottom: 16 }}
      >
        <Form.Item
          name={["asr", "assemblyai_api_key"]}
          label={formatMessage({ id: "settings.ingestion.assemblyaiApiKey" })}
        >
          <Input
            disabled
            placeholder={formatMessage(
              { id: "settings.system.setViaEnv" },
              { envVar: "ASSEMBLYAI_API_KEY" },
            )}
          />
        </Form.Item>
      </Card>

      <Card title={formatMessage({ id: "settings.system.operations" })} style={settingsCardStyle}>
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              flexWrap: "wrap",
              gap: 12,
            }}
          >
            <div>
              <div style={{ fontWeight: 600 }}>
                {formatMessage({ id: "settings.system.rebuildVectors" })}
              </div>
              <div style={{ fontSize: 12, color: "var(--color-text-muted)", marginTop: 2 }}>
                {formatMessage({ id: "settings.system.rebuildVectorsDesc" })}
              </div>
            </div>
            <Button
              icon={<ThunderboltOutlined />}
              loading={rebuilding}
              onClick={onRebuildVectors}
              style={{ borderRadius: 8 }}
            >
              {formatMessage({ id: "settings.system.rebuildVectorsBtn" })}
            </Button>
          </div>

          <Divider style={{ margin: "4px 0" }} />

          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              flexWrap: "wrap",
              gap: 12,
            }}
          >
            <div>
              <div style={{ fontWeight: 600 }}>
                {formatMessage({ id: "settings.system.rebuildMultimodal" })}
              </div>
              <div style={{ fontSize: 12, color: "var(--color-text-muted)", marginTop: 2 }}>
                {formatMessage({ id: "settings.system.rebuildMultimodalDesc" })}
              </div>
            </div>
            <Button
              icon={<ThunderboltOutlined />}
              loading={rebuildingMultimodal}
              onClick={onRebuildMultimodal}
              style={{ borderRadius: 8 }}
            >
              {formatMessage({ id: "settings.system.rebuildMultimodalBtn" })}
            </Button>
          </div>

          <Divider style={{ margin: "4px 0" }} />

          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              flexWrap: "wrap",
              gap: 12,
            }}
          >
            <div>
              <div style={{ fontWeight: 600 }}>
                {formatMessage({ id: "settings.system.reloadConfig" })}
              </div>
              <div style={{ fontSize: 12, color: "var(--color-text-muted)", marginTop: 2 }}>
                {formatMessage({ id: "settings.system.reloadConfigDesc" })}
              </div>
              <Text type="secondary" style={{ fontSize: 11, display: "block", marginTop: 2 }}>
                {formatMessage({ id: "settings.memoryOnly.desc" })}
              </Text>
            </div>
            <Button
              icon={<ReloadOutlined />}
              loading={reloading}
              onClick={onReloadConfig}
              style={{ borderRadius: 8 }}
            >
              {formatMessage({ id: "settings.system.reloadConfigBtn" })}
            </Button>
          </div>
        </div>
      </Card>
    </motion.div>
  );
}
