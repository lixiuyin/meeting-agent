import { useNavigate } from "react-router-dom";
import { Button, Popconfirm } from "antd";
import {
  DeleteOutlined,
  SearchOutlined,
  CheckSquareOutlined,
  MessageOutlined,
  ArrowRightOutlined,
} from "@ant-design/icons";
import { motion, AnimatePresence } from "framer-motion";
import SessionDetail from "../components/history/SessionDetail";
import SessionSearch from "../components/history/SessionSearch";
import { useSessionMessages } from "../hooks/useSessionMessages";

export default function HistoryPage() {
  const navigate = useNavigate();
  const session = useSessionMessages();

  const handleContinue = (sessionId: string) => {
    navigate(`/?sessionId=${sessionId}`);
  };

  return (
    <div style={{ maxWidth: 1000, margin: "0 auto", padding: "0 16px 24px" }}>
      {/* Header */}
      <motion.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        style={{
          padding: "8px 0 16px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          flexWrap: "wrap",
          gap: 16,
        }}
      >
        <div style={{ fontSize: 13, color: "var(--color-text-muted)" }}>
          {session.displaySessions.length}{" "}
          {session.displaySessions.length === 1 ? "conversation" : "conversations"}
          {session.debouncedSearchQuery.trim()
            ? ` matching "${session.debouncedSearchQuery}"`
            : session.displaySessions.length !== session.sessions.length &&
              ` (of ${session.sessions.length})`}
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <Button
            type={session.isSelectionMode ? "primary" : "default"}
            icon={<CheckSquareOutlined />}
            onClick={() => {
              session.setIsSelectionMode(!session.isSelectionMode);
              if (session.isSelectionMode) {
                session.clearSelection();
              }
            }}
          >
            {session.isSelectionMode ? "Done" : "Select"}
          </Button>

          {session.isSelectionMode && session.selectedIds.size > 0 && (
            <>
              <span style={{ fontSize: 14, color: "var(--color-text-secondary)" }}>
                {session.selectedIds.size} selected
              </span>
              <Button onClick={session.selectAll}>Select All</Button>
              <Button onClick={session.clearSelection}>Clear</Button>
              <Popconfirm
                title={`Delete ${session.selectedIds.size} conversations?`}
                description="This action cannot be undone."
                onConfirm={session.handleBatchDelete}
                okText="Delete"
                okButtonProps={{ danger: true }}
              >
                <Button danger icon={<DeleteOutlined />}>
                  Delete
                </Button>
              </Popconfirm>
            </>
          )}
        </div>
      </motion.div>

      {/* Toolbar */}
      <SessionSearch
        searchQuery={session.searchQuery}
        onSearchChange={session.setSearchQuery}
        searchLoading={session.searchLoading}
        loading={session.loading}
        onRefresh={session.fetchSessions}
      />

      {/* Content */}
      <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.2 }}>
        {session.displaySessions.length === 0 && !session.loading && !session.searchLoading ? (
          <EmptyState searchQuery={session.searchQuery} onStartChat={() => navigate("/")} />
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {(session.loading || session.searchLoading) && session.displaySessions.length === 0 && (
              <LoadingSkeletons />
            )}

            <AnimatePresence>
              {session.displaySessions.map((s) => (
                <SessionDetail
                  key={s.id}
                  session={s}
                  isExpanded={session.expandedId === s.id}
                  isSelected={session.selectedIds.has(s.id)}
                  isSelectionMode={session.isSelectionMode}
                  messageCount={session.getMessageCount(s.id)}
                  summary={session.summaryMap[s.id]}
                  snippets={session.matchingSnippets[s.id] || []}
                  messages={session.messagesMap[s.id] || []}
                  loadingMessages={session.loadingMessagesMap[s.id] || false}
                  messagesError={session.messagesErrorMap[s.id] || null}
                  messageRoleFilter={session.messageRoleFilter}
                  summarizing={session.summarizingId === s.id}
                  formatDate={session.formatDate}
                  onExpand={(id) => void session.handleExpand(id)}
                  onToggleSelect={session.toggleSelection}
                  onDelete={(id) => void session.handleDelete(id)}
                  onRetryLoad={(id) => void session.handleRetryLoad(id)}
                  onContinue={handleContinue}
                  onSummarize={(id) => void session.handleSummarize(id)}
                  onSetMessageRoleFilter={session.setMessageRoleFilter}
                />
              ))}
            </AnimatePresence>
          </div>
        )}
      </motion.div>
    </div>
  );
}

function EmptyState({
  searchQuery,
  onStartChat,
}: {
  searchQuery: string;
  onStartChat: () => void;
}) {
  return (
    <div style={{ textAlign: "center", padding: "80px 20px" }}>
      <div
        style={{
          width: 80,
          height: 80,
          borderRadius: 24,
          background: "var(--gradient-primary)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          margin: "0 auto 20px",
          boxShadow: "var(--glow-primary)",
        }}
      >
        {searchQuery ? (
          <SearchOutlined style={{ fontSize: 32, color: "#fff" }} />
        ) : (
          <MessageOutlined style={{ fontSize: 32, color: "#fff" }} />
        )}
      </div>
      <div
        style={{
          fontSize: 18,
          fontWeight: 600,
          marginBottom: 8,
          color: "var(--color-text-primary)",
        }}
      >
        {searchQuery ? "No matching conversations" : "No chat history yet"}
      </div>
      <div style={{ color: "var(--color-text-muted)", marginBottom: 20 }}>
        {searchQuery ? "Try a different search term" : "Start a conversation to see it here"}
      </div>
      {!searchQuery && (
        <Button
          type="primary"
          icon={<ArrowRightOutlined />}
          size="large"
          style={{
            borderRadius: 12,
            background: "var(--gradient-primary)",
            border: "none",
          }}
          onClick={onStartChat}
        >
          Start Chatting
        </Button>
      )}
    </div>
  );
}

function LoadingSkeletons() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {Array.from({ length: 4 }).map((_, i) => (
        <div
          key={i}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 16,
            padding: "16px 20px",
            borderRadius: 16,
            background: "var(--color-bg-muted)",
          }}
        >
          <div
            className="skeleton-shimmer"
            style={{ width: 44, height: 44, borderRadius: 12, flexShrink: 0 }}
          />
          <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 10 }}>
            <div className="skeleton-shimmer" style={{ width: "40%", height: 16 }} />
            <div className="skeleton-shimmer" style={{ width: "25%", height: 14 }} />
          </div>
        </div>
      ))}
    </div>
  );
}
