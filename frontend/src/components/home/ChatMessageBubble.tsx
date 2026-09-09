import { memo, useCallback, useMemo, useState } from "react";
import { Alert, Avatar, Button } from "antd";
import { EditOutlined, RobotOutlined, UserOutlined } from "@ant-design/icons";
import { motion } from "framer-motion";
import { getMeetingAssetUrl, type SourceItem } from "../../api/client";
import type { ChatMessage } from "../../hooks/useChatStream";
import { message } from "antd";
import { useMessageActions } from "../../contexts/MessageActionsContext";
import { CitationMarkdown } from "./chat-bubble/CitationMarkdown";
import { AgentMetaPanels, type CitationSelection } from "./chat-bubble/AgentMetaPanels";
import { CitationModal } from "./chat-bubble/CitationModal";
import {
  sanitizeAgentAnswer,
  isLegacyPartialAnswer,
  isImageDerivedSource,
} from "./chat-bubble/sourceHelpers";
import { sourcePrimaryImageUrl } from "./chat-bubble/sourceLinks";
import { openExternalInNewTab } from "../../utils/url";
import { useIntl } from "react-intl";

interface ChatMessageBubbleProps {
  msg: ChatMessage;
  idx: number;
  isStreaming: boolean;
  isLast: boolean;
}

function ThinkingDots() {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 4, padding: "4px 8px" }}>
      {[0, 1, 2].map((i) => (
        <motion.span
          key={i}
          style={{
            width: 8,
            height: 8,
            borderRadius: "50%",
            background: "var(--color-primary)",
            opacity: 0.4,
          }}
          animate={{
            scale: [1, 1.2, 1],
            opacity: [0.4, 1, 0.4],
          }}
          transition={{
            duration: 1.2,
            repeat: Infinity,
            delay: i * 0.15,
            ease: "easeInOut",
          }}
        />
      ))}
    </div>
  );
}

function ChatMessageBubble({ msg, idx, isStreaming, isLast }: ChatMessageBubbleProps) {
  const { formatMessage } = useIntl();
  const {
    copiedId,
    onCopy,
    onRegenerate,
    onOpenViewer,
    onOpenMeetingSummary,
    onOpenFileSummary,
    onCopySourceSnippet,
    onEditUserMessage,
  } = useMessageActions();
  const [openSourcePopoverKey, setOpenSourcePopoverKey] = useState<string | null>(null);
  const [selectedCitation, setSelectedCitation] = useState<CitationSelection | null>(null);
  const msgKey = msg.id || `${idx}`;
  const displayContent = msg.role === "agent" ? sanitizeAgentAnswer(msg.content) : msg.content;
  const effectiveDisplayContent =
    msg.role === "agent" && !isStreaming && !displayContent
      ? formatMessage({ id: "chat.emptyResponse" })
      : displayContent;
  const allSources = useMemo(() => msg.sources ?? [], [msg.sources]);

  const openSource = useCallback(
    (source: SourceItem) => {
      // Pass source.content as fallback so the summary modal always shows the
      // exact text the LLM was citing, even when the meetings/{id}/summary
      // endpoint is empty or misses this file's per-file entry.
      const fallback = (source.content || "").trim() || undefined;
      // Meeting summary → open SummaryModal
      if (source.source_kind === "meeting_summary" && source.meeting_id != null) {
        onOpenMeetingSummary(source.meeting_id, fallback);
        return;
      }
      // File summary → open file-summary modal view (not raw file viewer)
      if (source.source_kind === "file_summary" && source.meeting_id != null) {
        if (source.file_id == null) {
          message.warning(formatMessage({ id: "chat.fileIdMissing" }));
          return;
        }
        onOpenFileSummary(source.meeting_id, source.file_id, fallback);
        return;
      }
      // Image-derived preview: prefer the inline image URL when we have it;
      // otherwise fall through to the file viewer / preview modal so the
      // click never silently no-ops.
      if (isImageDerivedSource(source)) {
        const imageUrl = sourcePrimaryImageUrl(source);
        if (imageUrl) {
          openExternalInNewTab(imageUrl);
          return;
        }
      }
      // All sources with meeting_id + file_id → use unified MaterialViewer modal
      if (source.meeting_id != null && source.file_id != null) {
        onOpenViewer(source, true);
        return;
      }
      // AV sources with timestamp → TranscriptViewer via seekTo
      if (source.timestamp_start != null) {
        onOpenViewer(source, true);
        return;
      }
      // Fallback: source without file info — cannot open viewer
      message.warning(formatMessage({ id: "chat.sourceUnavailable" }));
    },
    [formatMessage, onOpenFileSummary, onOpenViewer, onOpenMeetingSummary],
  );

  const handleCiteClick = useCallback(
    (citeIdx: number) => {
      // Snapshot length once to avoid race with concurrent sources update
      const srcLen = allSources.length;
      // Guard: sources may not have arrived yet during streaming
      if (isStreaming && isLast && citeIdx > srcLen) {
        message.info(formatMessage({ id: "chat.citationGenerating" }));
        return;
      }
      const src = citeIdx >= 1 && citeIdx <= srcLen ? allSources[citeIdx - 1] : undefined;
      if (!src) {
        message.warning(formatMessage({ id: "chat.sourceNotFound" }, { index: citeIdx }));
        return;
      }
      setOpenSourcePopoverKey(null);
      // Meeting summary → dedicated summary jump
      if (src.source_kind === "meeting_summary" || src.source_kind === "file_summary") {
        openSource(src);
        return;
      }
      // Try to open the viewer directly; fall back to citation modal preview
      if (
        (src.file_id != null && src.meeting_id != null) ||
        src.timestamp_start != null ||
        isImageDerivedSource(src)
      ) {
        openSource(src);
      } else {
        setSelectedCitation({ index: citeIdx, source: src });
      }
    },
    [allSources, formatMessage, openSource, isStreaming, isLast],
  );

  return (
    <motion.div
      key={msgKey}
      initial={{ opacity: 0, y: 10, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.25, ease: "easeOut" }}
      style={{
        display: "flex",
        justifyContent: msg.role === "user" ? "flex-end" : "flex-start",
        padding: "0 8px",
      }}
    >
      <div
        style={{
          display: "flex",
          flexDirection: msg.role === "user" ? "row-reverse" : "row",
          alignItems: "flex-start",
          gap: 12,
          maxWidth: "85%",
        }}
      >
        <Avatar
          size="default"
          icon={msg.role === "user" ? <UserOutlined /> : <RobotOutlined />}
          style={{
            background: msg.role === "user" ? "var(--gradient-primary)" : "var(--color-bg-muted)",
            color: msg.role === "user" ? "#fff" : "var(--color-primary)",
            border: msg.role === "user" ? "none" : "2px solid var(--color-border)",
            flexShrink: 0,
          }}
        />
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 8,
            alignItems: msg.role === "user" ? "flex-end" : "flex-start",
          }}
        >
          {msg.role === "agent" && (msg.degraded || isLegacyPartialAnswer(msg.content)) && (
            <Alert
              type="warning"
              showIcon
              message={formatMessage({ id: "chat.partialResponse" })}
            />
          )}
          <div
            style={{
              padding: "14px 18px",
              borderRadius: msg.role === "user" ? "18px 18px 4px 18px" : "18px 18px 18px 4px",
              background:
                msg.role === "user" ? "var(--gradient-primary)" : "var(--color-bg-surface)",
              color: msg.role === "user" ? "#fff" : "var(--color-text-primary)",
              boxShadow: "var(--shadow-md)",
              border: msg.role === "user" ? "none" : "1px solid var(--color-border)",
              fontSize: 15,
              lineHeight: 1.6,
            }}
          >
            {msg.role === "agent" ? (
              msg.content === "" && isStreaming && isLast ? (
                <div
                  role="status"
                  aria-live="polite"
                  aria-label={formatMessage({ id: "chat.generating" })}
                >
                  <ThinkingDots />
                </div>
              ) : (
                <div>
                  <CitationMarkdown
                    content={effectiveDisplayContent}
                    sourceCount={allSources.length}
                    onCiteClick={handleCiteClick}
                    resolveAssetUrl={getMeetingAssetUrl}
                    streaming={isStreaming && isLast}
                  />
                </div>
              )
            ) : (
              effectiveDisplayContent
            )}
          </div>

          {msg.role === "user" && msg.serverId && (
            <Button
              type="text"
              size="small"
              icon={<EditOutlined />}
              disabled={isStreaming}
              onClick={() => onEditUserMessage(msg)}
              aria-label={formatMessage({ id: "chat.editAndBranch" })}
              style={{ color: "var(--color-text-muted)" }}
            >
              {formatMessage({ id: "chat.edit" })}
            </Button>
          )}

          <AgentMetaPanels
            msg={msg}
            msgKey={msgKey}
            displayContent={effectiveDisplayContent}
            copiedId={copiedId}
            isLast={isLast}
            isStreaming={isStreaming}
            openSourcePopoverKey={openSourcePopoverKey}
            onSetOpenSourcePopoverKey={setOpenSourcePopoverKey}
            onCopy={onCopy}
            onRegenerate={onRegenerate}
            onOpenSource={openSource}
            onCopySourceSnippet={onCopySourceSnippet}
            onSelectCitation={setSelectedCitation}
          />
        </div>
      </div>
      <CitationModal
        citation={selectedCitation}
        onClose={() => setSelectedCitation(null)}
        onOpenSource={openSource}
        onCopySourceSnippet={onCopySourceSnippet}
      />
    </motion.div>
  );
}

// isStreaming is only visually meaningful for the last message (ThinkingDots,
// aria-live, action buttons). Skip re-renders for historical messages when
// only isStreaming changes — they look identical whether streaming or not.
function arePropsEqual(prev: ChatMessageBubbleProps, next: ChatMessageBubbleProps): boolean {
  if (prev.msg !== next.msg) return false;
  if (prev.isLast !== next.isLast) return false;
  if ((prev.isLast || next.isLast) && prev.isStreaming !== next.isStreaming) return false;
  return true;
}

export default memo(ChatMessageBubble, arePropsEqual);
