import { Button, Empty, Space, Tooltip } from "antd";
import { ArrowRightOutlined, FileTextOutlined } from "@ant-design/icons";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { useCallback, useMemo } from "react";
import { useIntl } from "react-intl";
import type { SessionSummaryItem } from "../../../api/client";
import { messageKeyFor } from "./sourceHelpers";
import { MessageBubble } from "./MessageBubble";
import { isAgentRole, type SessionMessage } from "./types";

interface Props {
  allMessages: SessionMessage[];
  humanMessageCount: number;
  agentMessageCount: number;
  messageRoleFilter: "all" | "human" | "agent";
  onSetMessageRoleFilter: (filter: "all" | "human" | "agent") => void;
  onContinue: () => void;
  onSummarize: () => void;
  summary: SessionSummaryItem | undefined;
  summarizing: boolean;
  hasOlderMessages: boolean;
  loadingOlder: boolean;
  onLoadOlder: () => void;
}

export function MessagesSection({
  allMessages,
  humanMessageCount,
  agentMessageCount,
  messageRoleFilter,
  onSetMessageRoleFilter,
  onContinue,
  onSummarize,
  summary,
  summarizing,
  hasOlderMessages,
  loadingOlder,
  onLoadOlder,
}: Props) {
  const { formatMessage } = useIntl();
  const isVisibleByFilter = useCallback(
    (msg: SessionMessage) => {
      if (messageRoleFilter === "all") return true;
      return messageRoleFilter === "human" ? msg.role === "human" : isAgentRole(msg.role);
    },
    [messageRoleFilter],
  );

  const visibleMessages = useMemo(
    () =>
      allMessages
        .map((msg, originalIndex) => ({ msg, originalIndex }))
        .filter(({ msg }) => isVisibleByFilter(msg)),
    [allMessages, isVisibleByFilter],
  );
  const prefersReducedMotion = useReducedMotion();

  return (
    <>
      {hasOlderMessages && (
        <div style={{ textAlign: "center", marginBottom: 12 }}>
          <Button size="small" loading={loadingOlder} onClick={onLoadOlder}>
            {formatMessage({ id: "history.loadOlder" })}
          </Button>
        </div>
      )}
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
        <span style={{ fontSize: 12, color: "var(--color-text-muted)" }}>
          {formatMessage({ id: "history.filter" })}
        </span>
        <Button
          size="small"
          type={messageRoleFilter === "all" ? "primary" : "default"}
          onClick={() => onSetMessageRoleFilter("all")}
        >
          {formatMessage({ id: "history.all" }, { count: allMessages.length })}
        </Button>
        <Button
          size="small"
          type={messageRoleFilter === "human" ? "primary" : "default"}
          onClick={() => onSetMessageRoleFilter("human")}
        >
          {formatMessage({ id: "history.user" }, { count: humanMessageCount })}
        </Button>
        <Button
          size="small"
          type={messageRoleFilter === "agent" ? "primary" : "default"}
          onClick={() => onSetMessageRoleFilter("agent")}
        >
          {formatMessage({ id: "history.agent" }, { count: agentMessageCount })}
        </Button>
      </div>
      <div style={{ maxHeight: 400, overflowY: "auto", marginBottom: 20, padding: 4 }}>
        <AnimatePresence initial={false} mode="sync">
          {visibleMessages.map(({ msg, originalIndex }) => (
            <MessageBubble
              key={messageKeyFor(msg, originalIndex)}
              msg={msg}
              reducedMotion={Boolean(prefersReducedMotion)}
            />
          ))}
        </AnimatePresence>
        <AnimatePresence initial={false}>
          {visibleMessages.length === 0 && (
            <motion.div
              key="empty-message-filter"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={
                prefersReducedMotion
                  ? { duration: 0.1, ease: "linear" }
                  : { duration: 0.16, ease: "easeOut" }
              }
            >
              <Empty
                description={formatMessage({ id: "history.noFilteredMessages" })}
                image={Empty.PRESENTED_IMAGE_SIMPLE}
              />
            </motion.div>
          )}
        </AnimatePresence>
      </div>
      <Space>
        <Button
          type="primary"
          icon={<ArrowRightOutlined />}
          onClick={onContinue}
          style={{ background: "var(--gradient-primary)", border: "none" }}
        >
          {formatMessage({ id: "history.continue" })}
        </Button>
        <Tooltip title={formatMessage({ id: "history.summaryTooltip" })}>
          <Button icon={<FileTextOutlined />} onClick={onSummarize} loading={summarizing}>
            {summary
              ? formatMessage({ id: "history.regenerateSummary" })
              : formatMessage({ id: "history.summarize" })}
          </Button>
        </Tooltip>
      </Space>
    </>
  );
}
