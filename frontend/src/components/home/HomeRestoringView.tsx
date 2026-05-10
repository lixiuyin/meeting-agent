import { RobotOutlined } from "@ant-design/icons";
import { motion } from "framer-motion";
import type { IntlShape } from "react-intl";

export default function HomeRestoringView({
  formatMessage,
  sessionTitle,
}: {
  formatMessage: IntlShape["formatMessage"];
  sessionTitle?: string;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        height: "100%",
      }}
    >
      <motion.div
        initial={{ opacity: 0, scale: 0.9 }}
        animate={{ opacity: 1, scale: 1 }}
        style={{ textAlign: "center" }}
      >
        <div
          style={{
            width: 64,
            height: 64,
            borderRadius: 20,
            background: "var(--gradient-primary)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            margin: "0 auto 16px",
            boxShadow: "var(--glow-primary)",
          }}
        >
          <RobotOutlined style={{ fontSize: 28, color: "#fff" }} />
        </div>
        <div style={{ color: "var(--color-text-secondary)" }}>
          {formatMessage({ id: "home.restoring" })}
        </div>
        {sessionTitle && (
          <div
            style={{
              color: "var(--color-text-muted)",
              fontSize: 13,
              marginTop: 6,
              maxWidth: 320,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {sessionTitle}
          </div>
        )}
      </motion.div>
    </div>
  );
}
