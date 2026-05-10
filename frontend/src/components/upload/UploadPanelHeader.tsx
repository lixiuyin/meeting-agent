import { PlusOutlined } from "@ant-design/icons";
import { Tag } from "antd";
import { useIntl } from "react-intl";

export function UploadPanelHeader() {
  const { formatMessage } = useIntl();

  return (
    <div
      style={{
        fontSize: 15,
        fontWeight: 600,
        color: "var(--color-text-primary)",
        marginBottom: 16,
        display: "flex",
        alignItems: "center",
        gap: 8,
      }}
    >
      <span
        style={{
          width: 28,
          height: 28,
          borderRadius: 8,
          background: "var(--color-primary-alpha)",
          color: "var(--color-primary)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: 14,
        }}
      >
        <PlusOutlined />
      </span>
      {formatMessage({ id: "upload.header.title" })}
      <Tag color="blue" style={{ marginLeft: 8 }}>
        {formatMessage({ id: "upload.header.multipleFiles" })}
      </Tag>
    </div>
  );
}
