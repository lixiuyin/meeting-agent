import { Switch, Tooltip } from "antd";
import type { ReactNode } from "react";
import { useIntl } from "react-intl";

interface SettingSwitchProps {
  label: ReactNode;
  tooltip?: ReactNode;
  size?: "small" | "default";
  style?: React.CSSProperties;
}

export default function SettingSwitch({ label, tooltip, size, style }: SettingSwitchProps) {
  const intl = useIntl();
  return (
    <div style={{ display: "flex", alignItems: "center", ...style }}>
      <Switch
        checkedChildren={intl.formatMessage({ id: "common.enabled" })}
        unCheckedChildren={intl.formatMessage({ id: "common.disabled" })}
        size={size}
      />
      <span
        style={{
          marginLeft: 8,
          color: "var(--color-text-secondary)",
          fontSize: size === "small" ? 13 : undefined,
        }}
      >
        {label}
        {tooltip && (
          <Tooltip title={tooltip}>
            <span style={{ marginLeft: 6, opacity: 0.5, fontSize: 12, cursor: "help" }}>ⓘ</span>
          </Tooltip>
        )}
      </span>
    </div>
  );
}
