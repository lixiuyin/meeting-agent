import { Avatar, Button, Empty, Spin } from "antd";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { DownOutlined, RightOutlined, RobotOutlined, DeleteOutlined } from "@ant-design/icons";
import type { SessionInfo, SessionSearchResult, SessionSummaryItem } from "../../api/client";
import { collectSummarySources } from "./session-detail/sourceHelpers";
import { SummarySection } from "./session-detail/SummarySection";
import { SnippetsSection } from "./session-detail/SnippetsSection";
import { ErrorMessage } from "./session-detail/ErrorMessage";
import { MessagesSection } from "./session-detail/MessagesSection";
import type { SessionMessage } from "./session-detail/types";

interface SessionDetailProps {
  session: SessionInfo;
  isExpanded: boolean;
  isSelected: boolean;
  isSelectionMode: boolean;
  messageCount: number;
  summary: SessionSummaryItem | undefined;
  snippets: SessionSearchResult[];
  messages: SessionMessage[];
  loadingMessages: boolean;
  messagesError: string | null;
  messageRoleFilter: "all" | "human" | "agent";
  summarizing: boolean;
  formatDate: (iso: string) => string;
  onExpand: (id: string) => void;
  onToggleSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onRetryLoad: (id: string) => void;
  onContinue: (id: string) => void;
  onSummarize: (id: string) => void;
  onSetMessageRoleFilter: (filter: "all" | "human" | "agent") => void;
}

function ClockCircleIcon() {
  return <span style={{ fontSize: 13 }}>·</span>;
}

export default function SessionDetail({
  session,
  isExpanded,
  isSelected,
  isSelectionMode,
  messageCount,
  summary,
  snippets,
  messages,
  loadingMessages,
  messagesError,
  messageRoleFilter,
  summarizing,
  formatDate,
  onExpand,
  onToggleSelect,
  onDelete,
  onRetryLoad,
  onContinue,
  onSummarize,
  onSetMessageRoleFilter,
}: SessionDetailProps) {
  const prefersReducedMotion = useReducedMotion();
  const humanMessageCount = messages.filter((msg) => msg.role === "human").length;
  const agentMessageCount = messages.length - humanMessageCount;
  const summarySources = collectSummarySources(messages);

  return (
    <motion.div
      key={session.id}
      layout
      initial={prefersReducedMotion ? { opacity: 1 } : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={prefersReducedMotion ? { opacity: 0 } : { opacity: 0, y: -8 }}
      style={{
        background: isSelected ? "rgba(79, 70, 229, 0.08)" : "var(--color-bg-surface)",
        borderRadius: 16,
        border: `2px solid ${isSelected ? "var(--color-primary)" : "var(--color-border)"}`,
        boxShadow: isSelected ? "var(--glow-primary)" : "var(--shadow-sm)",
        overflow: "hidden",
        transition: "all 0.2s ease",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 16,
          padding: "16px 20px",
          cursor: "pointer",
        }}
        role="button"
        tabIndex={0}
        onClick={() => (isSelectionMode ? onToggleSelect(session.id) : onExpand(session.id))}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            if (isSelectionMode) onToggleSelect(session.id);
            else onExpand(session.id);
          }
        }}
      >
        {isSelectionMode ? (
          <input
            type="checkbox"
            checked={isSelected}
            aria-label={`Select session ${session.title || session.id}`}
            onClick={(e) => {
              e.stopPropagation();
              onToggleSelect(session.id);
            }}
            style={{ width: 18, height: 18, accentColor: "var(--color-primary)" }}
          />
        ) : (
          <Avatar
            size="large"
            icon={<RobotOutlined />}
            style={{
              background: isExpanded ? "var(--gradient-primary)" : "var(--color-bg-muted)",
              color: isExpanded ? "#fff" : "var(--color-primary)",
              border: isExpanded ? "none" : "2px solid var(--color-border)",
              flexShrink: 0,
            }}
          />
        )}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              fontSize: 15,
              fontWeight: 600,
              color: "var(--color-text-primary)",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
            title={session.title || "Untitled Conversation"}
          >
            {session.title || "Untitled Conversation"}
          </div>
          <div
            style={{
              fontSize: 13,
              color: "var(--color-text-muted)",
              marginTop: 4,
              display: "flex",
              alignItems: "center",
              gap: 8,
            }}
          >
            <ClockCircleIcon />
            {formatDate(session.created_at)}
            {messageCount > 0 && (
              <>
                <span style={{ color: "var(--color-border-strong)" }}>·</span>
                <span>{messageCount} messages</span>
              </>
            )}
          </div>
        </div>

        {!isSelectionMode && (
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Button
              type="text"
              size="small"
              icon={isExpanded ? <DownOutlined /> : <RightOutlined />}
              onClick={(e) => {
                e.stopPropagation();
                void onExpand(session.id);
              }}
              aria-label={isExpanded ? "Collapse conversation" : "Expand conversation"}
              title={isExpanded ? "Collapse" : "Expand"}
            />
            <Button
              type="text"
              danger
              size="small"
              icon={<DeleteOutlined />}
              aria-label="Delete conversation"
              onClick={(e) => {
                e.stopPropagation();
                onDelete(session.id);
              }}
            />
          </div>
        )}
      </div>

      <AnimatePresence>
        {isExpanded && !isSelectionMode && (
          <motion.div
            initial={prefersReducedMotion ? { opacity: 0 } : { height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={prefersReducedMotion ? { opacity: 0 } : { height: 0, opacity: 0 }}
            transition={
              prefersReducedMotion
                ? { duration: 0.12, ease: "linear" }
                : { duration: 0.22, ease: "easeInOut" }
            }
            style={{ overflow: "hidden" }}
          >
            <div
              style={{
                borderTop: "1px solid var(--color-border)",
                padding: "20px",
                background: "var(--color-bg-muted)",
              }}
            >
              {loadingMessages ? (
                <div style={{ textAlign: "center", padding: "40px 20px" }}>
                  <Spin size="large" style={{ display: "block", margin: "0 auto 12px" }} />
                  <div style={{ color: "var(--color-text-muted)", fontSize: 13 }}>
                    Loading messages...
                  </div>
                </div>
              ) : (
                <>
                  {summary && <SummarySection summary={summary} sources={summarySources} />}
                  {snippets.length > 0 && <SnippetsSection snippets={snippets} />}
                  {messagesError ? (
                    <ErrorMessage message={messagesError} onRetry={() => onRetryLoad(session.id)} />
                  ) : messages.length === 0 ? (
                    <Empty description="No messages" image={Empty.PRESENTED_IMAGE_SIMPLE} />
                  ) : (
                    <MessagesSection
                      allMessages={messages}
                      humanMessageCount={humanMessageCount}
                      agentMessageCount={agentMessageCount}
                      messageRoleFilter={messageRoleFilter}
                      onSetMessageRoleFilter={onSetMessageRoleFilter}
                      onContinue={() => onContinue(session.id)}
                      onSummarize={() => onSummarize(session.id)}
                      summary={summary}
                      summarizing={summarizing}
                    />
                  )}
                </>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}
