import { Alert, Avatar } from "antd";
import { RobotOutlined, UserOutlined } from "@ant-design/icons";
import { useIntl } from "react-intl";
import { motion } from "framer-motion";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { SourceItem } from "../../../api/client";
import { CitationMarkdown } from "./CitationMarkdown";
import { SourceChips } from "./SourceChips";
import { sourceKeyFor } from "./sourceHelpers";
import { sanitizeAgentAnswer, isLegacyPartialAnswer } from "../../home/chat-bubble/sourceHelpers";
import { SourceDetailModal } from "./SourceDetailModal";
import { isAgentRole, type SessionMessage } from "./types";

export function MessageBubble({
  msg,
  reducedMotion,
}: {
  msg: SessionMessage;
  reducedMotion: boolean;
}) {
  const { formatMessage } = useIntl();
  const [openSourcePopoverKey, setOpenSourcePopoverKey] = useState<string | null>(null);
  const [flashSourceKey, setFlashSourceKey] = useState<string | null>(null);
  const flashTimerRef = useRef<number | null>(null);
  const [selectedCitation, setSelectedCitation] = useState<{
    index: number;
    source: SourceItem;
  } | null>(null);
  const citationSources = useMemo(() => msg.sources ?? [], [msg.sources]);
  const isAgent = isAgentRole(msg.role);
  const displayContent = isAgent ? sanitizeAgentAnswer(msg.content) : msg.content;

  useEffect(
    () => () => {
      if (flashTimerRef.current != null) window.clearTimeout(flashTimerRef.current);
    },
    [],
  );

  const handleCiteClick = useCallback(
    (citeIdx: number) => {
      const src = citationSources[citeIdx - 1];
      if (!src) return;
      const targetKey = sourceKeyFor(src, citeIdx);
      setSelectedCitation({ index: citeIdx, source: src });
      setOpenSourcePopoverKey(targetKey);
      setFlashSourceKey(targetKey);
      if (flashTimerRef.current != null) window.clearTimeout(flashTimerRef.current);
      flashTimerRef.current = window.setTimeout(() => {
        setFlashSourceKey((prev) => (prev === targetKey ? null : prev));
      }, 1000);
    },
    [citationSources],
  );

  return (
    <motion.div
      layout="position"
      initial={reducedMotion ? { opacity: 1 } : { opacity: 0, y: 3 }}
      animate={{ opacity: 1, y: 0 }}
      exit={reducedMotion ? { opacity: 0 } : { opacity: 0, y: -2 }}
      transition={reducedMotion ? { duration: 0.1 } : { duration: 0.14, ease: "easeOut" }}
      style={{
        display: "flex",
        justifyContent: msg.role === "human" ? "flex-end" : "flex-start",
        marginBottom: 12,
      }}
    >
      <div
        style={{
          display: "flex",
          flexDirection: msg.role === "human" ? "row-reverse" : "row",
          alignItems: "flex-start",
          gap: 10,
          maxWidth: "90%",
        }}
      >
        <Avatar
          size="small"
          icon={msg.role === "human" ? <UserOutlined /> : <RobotOutlined />}
          style={{
            background:
              msg.role === "human" ? "var(--gradient-primary)" : "var(--color-bg-surface)",
            color: msg.role === "human" ? "#fff" : "var(--color-primary)",
            border: msg.role === "human" ? "none" : "2px solid var(--color-border)",
            flexShrink: 0,
          }}
        />
        <div
          style={{
            padding: "10px 14px",
            borderRadius: msg.role === "human" ? "14px 14px 4px 14px" : "14px 14px 14px 4px",
            background:
              msg.role === "human" ? "var(--gradient-primary)" : "var(--color-bg-surface)",
            color: msg.role === "human" ? "#fff" : "var(--color-text-primary)",
            boxShadow: "var(--shadow-sm)",
            border: msg.role === "human" ? "none" : "1px solid var(--color-border)",
            fontSize: 13,
            lineHeight: 1.5,
            textAlign: "left",
          }}
        >
          {isAgent && (msg.degraded || isLegacyPartialAnswer(msg.content)) && (
            <Alert
              type="warning"
              showIcon
              message={formatMessage({ id: "chat.partialResponse" })}
            />
          )}
          {isAgent ? (
            <div className="markdown-body">
              <CitationMarkdown
                content={displayContent}
                sourceCount={(msg.sources ?? []).length}
                onCiteClick={handleCiteClick}
              />
            </div>
          ) : (
            msg.content
          )}
          {isAgent && msg.sources && msg.sources.length > 0 && (
            <SourceChips
              sources={msg.sources}
              openSourcePopoverKey={openSourcePopoverKey}
              onOpenSourcePopoverChange={setOpenSourcePopoverKey}
              flashSourceKey={flashSourceKey}
            />
          )}
        </div>
      </div>
      <SourceDetailModal
        selectedCitation={selectedCitation}
        onClose={() => setSelectedCitation(null)}
      />
    </motion.div>
  );
}
