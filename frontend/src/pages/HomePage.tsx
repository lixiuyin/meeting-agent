import { useCallback, useEffect, useMemo } from "react";
import { Input, Button, Alert, Tooltip, message } from "antd";
import { SendOutlined, CopyOutlined, StopOutlined } from "@ant-design/icons";
import { motion, AnimatePresence } from "framer-motion";
import { useIntl } from "react-intl";
import { sourceToViewerRequest } from "../utils/sourceLocation";
import { useViewer } from "../contexts/ViewerContext";
import WelcomeScreen from "../components/home/WelcomeScreen";
import ChatMessageBubble from "../components/home/ChatMessageBubble";
import ChatParameters from "../components/home/ChatParameters";
import HomeContextHeader from "../components/home/HomeContextHeader";
import HomeRestoringView from "../components/home/HomeRestoringView";
import { useChatSession } from "../contexts/ChatContext";
import { MessageActionsProvider } from "../contexts/MessageActionsContext";
import type { SourceItem } from "../api/client";
import { useChatSummaryModal } from "../hooks/useChatSummaryModal";
import { useHomeShortcuts } from "../hooks/useHomeShortcuts";
import SummaryModal from "../components/materials/SummaryModal";

export default function HomePage() {
  const { openViewer } = useViewer();
  const {
    // State
    input,
    setInput,
    restoring,
    hasOlderMessages,
    loadingOlder,
    loadOlderMessages,
    pendingRun,
    resumePendingRun,
    restoreError,
    setRestoreError,
    retryRestore,
    inputFocused,
    setInputFocused,
    copiedId,
    editingMessage,
    // Chat options
    paramsExpanded,
    setParamsExpanded,
    useWebSearch,
    setUseWebSearch,
    selectedTypeFilters,
    setSelectedTypeFilters,
    dateFrom,
    setDateFrom,
    dateTo,
    setDateTo,
    validAt,
    setValidAt,
    knownAt,
    setKnownAt,
    continuationMode,
    setContinuationMode,
    ragMode,
    setRagMode,
    retrievalProfile,
    setRetrievalProfile,
    memoryMode,
    setMemoryMode,
    activeParamCount,
    // Session selection
    selectedMeetingIds,
    setSelectedMeetingIds,
    selectedFileIds,
    setSelectedFileIds,
    loadingMeetings,
    loadingFiles,
    removeSelectedMeeting,
    removeSelectedFile,
    meetingOptions,
    selectedMeetings,
    fileOptions,
    selectedFiles,
    // Chat stream
    messages,
    isStreaming,
    streamSessionId,
    streamError,
    streamErrorCode,
    streamErrorDetail,
    streamRequestId,
    streamNotice,
    setStreamError,
    setStreamNotice,
    // Refs (DOM + callback)
    scrollContainerRef,
    bottomRef,
    textareaRef,
    handleSendRef,
    // Handlers
    handleSend,
    handleRegenerate,
    handleEditUserMessage,
    cancelEditing,
    handleStop,
    handleWithdrawCurrent,
    handleCopy,
    handleCopyRequestId,
    handleCopySourceSnippet,
    handleNewSession,
  } = useChatSession();
  const { formatMessage } = useIntl();
  const summaryModal = useChatSummaryModal();

  // Warn before leaving the page when the chat input contains unsent text
  useEffect(() => {
    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      if (input.trim()) {
        e.preventDefault();
      }
    };
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [input]);

  // Keyboard shortcuts
  useHomeShortcuts({
    inputFocused,
    textareaRef,
    handleSendRef,
  });

  const openSourceViewer = useCallback(
    (source: SourceItem) => {
      if (source.file_id == null) {
        // Avoid silent no-op clicks: chunks should always carry file_id, but
        // if metadata was lost upstream the user still gets a clear signal
        // rather than a dead button.
        message.warning(formatMessage({ id: "chat.sourceFileInfoMissing" }));
        return;
      }
      const request = sourceToViewerRequest(source);
      if (request) openViewer(request);
    },
    [formatMessage, openViewer],
  );

  const openSummaryFileViewer = useCallback(
    (fileId: number) => {
      const f = summaryModal.files.find((item) => item.id === fileId);
      if (!f || summaryModal.meetingId == null) return;
      openViewer({
        meetingId: summaryModal.meetingId,
        fileId: f.id,
        fileName: f.file_name,
        fileType: f.file_type,
      });
    },
    [openViewer, summaryModal.files, summaryModal.meetingId],
  );

  const messageActions = useMemo(
    () => ({
      copiedId,
      onCopy: handleCopy,
      onRegenerate: handleRegenerate,
      onEditUserMessage: handleEditUserMessage,
      onOpenViewer: openSourceViewer,
      onOpenMeetingSummary: (meetingId: number, fallbackContent?: string) =>
        summaryModal.openSummary(meetingId, undefined, true, fallbackContent),
      onOpenFileSummary: (meetingId: number, fileId?: number, fallbackContent?: string) =>
        summaryModal.openSummary(meetingId, fileId, true, fallbackContent),
      onCopySourceSnippet: handleCopySourceSnippet,
    }),
    [
      openSourceViewer,
      summaryModal,
      copiedId,
      handleCopy,
      handleCopySourceSnippet,
      handleRegenerate,
      handleEditUserMessage,
    ],
  );

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  if (restoring) {
    return <HomeRestoringView formatMessage={formatMessage} />;
  }

  return (
    <div
      style={{
        height: "100%",
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
        maxWidth: 1200,
        margin: "0 auto",
        padding: "0 16px",
      }}
    >
      <HomeContextHeader
        formatMessage={formatMessage}
        selectedMeetingIds={selectedMeetingIds}
        setSelectedMeetingIds={setSelectedMeetingIds}
        meetingOptions={meetingOptions}
        loadingMeetings={loadingMeetings}
        selectedFileIds={selectedFileIds}
        setSelectedFileIds={setSelectedFileIds}
        fileOptions={fileOptions}
        loadingFiles={loadingFiles}
        selectedMeetings={selectedMeetings}
        selectedFiles={selectedFiles}
        removeSelectedMeeting={removeSelectedMeeting}
        removeSelectedFile={removeSelectedFile}
        streamSessionId={streamSessionId}
        onNewSession={handleNewSession}
      />

      {/* Parameters panel */}
      <ChatParameters
        expanded={paramsExpanded}
        onToggle={() => setParamsExpanded((v) => !v)}
        useWebSearch={useWebSearch}
        onUseWebSearchChange={setUseWebSearch}
        selectedTypeFilters={selectedTypeFilters}
        onTypeFiltersChange={setSelectedTypeFilters}
        dateFrom={dateFrom}
        onDateFromChange={(v) => setDateFrom(v)}
        dateTo={dateTo}
        onDateToChange={(v) => setDateTo(v)}
        validAt={validAt}
        onValidAtChange={setValidAt}
        knownAt={knownAt}
        onKnownAtChange={setKnownAt}
        continuationMode={continuationMode}
        onContinuationModeChange={setContinuationMode}
        ragMode={ragMode}
        onRagModeChange={setRagMode}
        retrievalProfile={retrievalProfile}
        onRetrievalProfileChange={setRetrievalProfile}
        memoryMode={memoryMode}
        onMemoryModeChange={setMemoryMode}
        activeParamCount={activeParamCount}
      />

      {/* Messages */}
      <div
        ref={scrollContainerRef}
        style={{
          flex: 1,
          minHeight: 0,
          overflowY: "auto",
          padding: "8px 0 16px",
        }}
      >
        {(restoreError || streamError) && (
          <motion.div
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            style={{ padding: "0 8px 12px" }}
          >
            <Alert
              type="error"
              showIcon
              title={
                streamError
                  ? streamErrorCode
                    ? `${streamError} [${streamErrorCode}]`
                    : streamError
                  : restoreError
              }
              description={
                <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 4 }}>
                  {streamErrorDetail && (
                    <div
                      style={{
                        fontSize: 12,
                        fontFamily: "monospace",
                        background: "var(--color-bg-muted)",
                        padding: "4px 8px",
                        borderRadius: 4,
                        color: "var(--color-text-secondary)",
                        wordBreak: "break-all",
                      }}
                    >
                      {streamErrorDetail}
                    </div>
                  )}
                  {streamError && streamRequestId && (
                    <div
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 8,
                        marginTop: 2,
                        fontSize: 12,
                      }}
                    >
                      <span style={{ color: "var(--color-text-muted)" }}>
                        {formatMessage({ id: "chat.requestId" })}: {streamRequestId}
                      </span>
                      <Button
                        type="text"
                        size="small"
                        icon={<CopyOutlined />}
                        onClick={() => handleCopyRequestId(streamRequestId!)}
                        style={{ paddingInline: 6, height: 24 }}
                      >
                        {formatMessage({ id: "chat.copy" })}
                      </Button>
                    </div>
                  )}
                </div>
              }
              closable
              action={
                restoreError ? (
                  <Button size="small" onClick={retryRestore}>
                    {formatMessage({ id: "home.restoreRetry" })}
                  </Button>
                ) : undefined
              }
              onClose={() => {
                setRestoreError(null);
                setStreamError(null);
              }}
            />
          </motion.div>
        )}
        {!restoreError && !streamError && streamNotice && (
          <motion.div
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            style={{ padding: "0 8px 12px" }}
          >
            <Alert
              type="info"
              showIcon
              title={streamNotice}
              closable
              onClose={() => setStreamNotice(null)}
            />
          </motion.div>
        )}
        {pendingRun && !isStreaming && (
          <Alert
            type="info"
            showIcon
            title="An earlier response can be recovered without sending the question again."
            action={<Button onClick={() => void resumePendingRun()}>Recover response</Button>}
          />
        )}
        {messages.length === 0 ? (
          <WelcomeScreen onQuickQuestion={(q) => handleSend(q)} />
        ) : (
          <MessageActionsProvider value={messageActions}>
            <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
              {hasOlderMessages && (
                <Button loading={loadingOlder} onClick={() => void loadOlderMessages()}>
                  {formatMessage({ id: "home.loadOlder", defaultMessage: "Load older messages" })}
                </Button>
              )}
              <AnimatePresence initial={false}>
                {messages.map((msg, idx) => (
                  <ChatMessageBubble
                    key={msg.id || `${idx}`}
                    msg={msg}
                    idx={idx}
                    isStreaming={isStreaming}
                    isLast={idx === messages.length - 1}
                  />
                ))}
              </AnimatePresence>
            </div>
          </MessageActionsProvider>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        style={{
          padding: "16px 0 24px",
          borderTop: "1px solid var(--color-border)",
        }}
      >
        {editingMessage && (
          <Alert
            type="info"
            showIcon
            title={formatMessage({ id: "chat.editingBranch" })}
            description={formatMessage({ id: "chat.editingBranchDescription" })}
            action={
              <Button size="small" onClick={cancelEditing}>
                {formatMessage({ id: "common.cancel" })}
              </Button>
            }
            style={{ marginBottom: 8 }}
          />
        )}
        {isStreaming && pendingRun?.id && (
          <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 8 }}>
            <Button danger size="small" onClick={() => void handleWithdrawCurrent()}>
              {formatMessage({ id: "chat.withdrawCurrent" })}
            </Button>
          </div>
        )}
        <div
          style={{
            display: "flex",
            alignItems: "flex-end",
            gap: 12,
            padding: "12px 16px",
            background: inputFocused ? "var(--color-bg-surface)" : "var(--color-bg-muted)",
            borderRadius: 20,
            border: `2px solid ${inputFocused ? "var(--color-primary)" : "transparent"}`,
            boxShadow: inputFocused ? "var(--shadow-glow)" : "var(--shadow-sm)",
            transition: "all 0.2s ease",
          }}
        >
          <Input.TextArea
            ref={textareaRef}
            variant="borderless"
            placeholder={formatMessage({ id: "home.inputPlaceholder" })}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            onFocus={() => setInputFocused(true)}
            onBlur={() => setInputFocused(false)}
            disabled={isStreaming}
            autoSize={{ minRows: 1, maxRows: 5 }}
            maxLength={10000}
            style={{
              flex: 1,
              background: "transparent",
              padding: 0,
              resize: "none",
              fontSize: 15,
              lineHeight: 1.5,
            }}
          />
          <Tooltip title={formatMessage({ id: isStreaming ? "chat.stopGenerating" : "chat.send" })}>
            <Button
              type="primary"
              shape="circle"
              size="large"
              icon={isStreaming ? <StopOutlined /> : <SendOutlined />}
              onClick={isStreaming ? () => void handleStop() : () => handleSend()}
              disabled={!isStreaming && !input.trim()}
              aria-label={formatMessage({ id: isStreaming ? "chat.stopGenerating" : "chat.send" })}
              style={{
                background: "var(--gradient-primary)",
                border: "none",
                boxShadow: "var(--glow-primary)",
                flexShrink: 0,
              }}
            />
          </Tooltip>
        </div>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            padding: "8px 16px 0",
            fontSize: 12,
            color: "var(--color-text-muted)",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <span>{formatMessage({ id: "home.enterSend" })}</span>
            <span style={{ opacity: 0.6 }}>|</span>
            <span>{formatMessage({ id: "home.shiftEnter" })}</span>
            <span style={{ opacity: 0.6 }}>|</span>
            <span>
              <kbd
                style={{
                  padding: "2px 6px",
                  background: "var(--color-bg-muted)",
                  borderRadius: 4,
                  fontSize: 11,
                  border: "1px solid var(--color-border)",
                }}
              >
                /
              </kbd>{" "}
              {formatMessage({ id: "home.slashFocus" })}
            </span>
          </div>
          {input.length > 0 && (
            <span>{formatMessage({ id: "home.characters" }, { count: input.length })}</span>
          )}
        </div>
      </motion.div>

      {/* Meeting Summary Modal (opened from citation clicks) */}
      <SummaryModal
        open={summaryModal.open}
        loading={summaryModal.loading}
        data={summaryModal.data}
        streaming={summaryModal.streaming}
        files={summaryModal.files}
        title={formatMessage({
          id: summaryModal.targetFileId != null ? "chat.fileSummary" : "chat.meetingSummary",
        })}
        focusFileId={summaryModal.targetFileId}
        onCopy={summaryModal.copy}
        onDownload={summaryModal.download}
        onRegenerate={() => summaryModal.regenerate()}
        onClose={summaryModal.close}
        onNavigateToFile={openSummaryFileViewer}
      />
    </div>
  );
}
