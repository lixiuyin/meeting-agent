import { Badge, Button, Space } from "antd";
import { ReloadOutlined, SaveOutlined, SettingOutlined } from "@ant-design/icons";
import type { IntlShape } from "react-intl";

interface Props {
  hasChanges: boolean;
  saving: boolean;
  formatMessage: IntlShape["formatMessage"];
  onReset: () => void;
  onSave: () => void;
}

export function SettingsHeader({ hasChanges, saving, formatMessage, onReset, onSave }: Props) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        marginBottom: 24,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <div
          style={{
            width: 44,
            height: 44,
            borderRadius: 14,
            background: "var(--gradient-primary)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "#fff",
            fontSize: 20,
            boxShadow: "var(--glow-primary)",
          }}
        >
          <SettingOutlined />
        </div>
        <h1
          style={{ margin: 0, fontSize: 22, fontWeight: 700, color: "var(--color-text-primary)" }}
        >
          {formatMessage({ id: "settings.title" })}
        </h1>
      </div>
      <Space>
        {hasChanges && (
          <Badge dot color="orange">
            <span style={{ color: "var(--color-text-tertiary)", fontSize: 13 }}>
              {formatMessage({ id: "settings.unsaved" })}
            </span>
          </Badge>
        )}
        <Button icon={<ReloadOutlined />} onClick={onReset} disabled={!hasChanges || saving}>
          {formatMessage({ id: "settings.reset" })}
        </Button>
        <Button
          type="primary"
          icon={<SaveOutlined />}
          onClick={onSave}
          loading={saving}
          disabled={!hasChanges || saving}
        >
          {formatMessage({ id: "settings.save" })}
        </Button>
      </Space>
    </div>
  );
}
