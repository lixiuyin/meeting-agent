import type { ReactNode } from "react";
import { Button, Popover } from "antd";
import { EllipsisOutlined } from "@ant-design/icons";
import { useIntl } from "react-intl";
import { useMediaQuery } from "../../hooks/useMediaQuery";

export default function MemoryActions({ children }: { children: ReactNode }) {
  const narrow = useMediaQuery("(max-width: 768px)");
  const { formatMessage: t } = useIntl();
  return narrow ? (
    <Popover
      trigger="click"
      placement="bottomRight"
      content={<div className="memory-actions-menu">{children}</div>}
    >
      <Button
        className="memory-actions-trigger"
        icon={<EllipsisOutlined />}
        aria-label={t({ id: "memory.moreActions" })}
      />
    </Popover>
  ) : (
    children
  );
}
