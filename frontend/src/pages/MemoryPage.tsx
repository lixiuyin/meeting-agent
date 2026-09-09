import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Card, Select, Space, Tabs } from "antd";
import { BranchesOutlined, HistoryOutlined, UserOutlined } from "@ant-design/icons";
import { useIntl } from "react-intl";
import MemoryList from "../components/memory/MemoryList";
import RecordedFactsPanel from "../components/memory/RecordedFactsPanel";
import FactChangesPanel from "../components/memory/FactChangesPanel";
import ProjectWorkspace from "../components/memory/ProjectWorkspace";
import MeetingReviewPanel from "../components/memory/MeetingReviewPanel";
import EntityGraph from "../components/memory/EntityGraph";
import SessionSummariesTab from "../components/memory/SessionSummariesTab";
import { useMemoryActions } from "../hooks/useMemoryActions";
import { useMediaQuery } from "../hooks/useMediaQuery";

export default function MemoryPage() {
  const { formatMessage } = useIntl();
  const userId = "default";
  const [memoryKind, setMemoryKind] = useState<"all" | "personal" | "reference">("personal");
  const memory = useMemoryActions(userId, memoryKind);
  const isNarrow = useMediaQuery("(max-width: 768px)");
  const [search, setSearch] = useSearchParams();
  const activeTab = search.get("memoryTab") || (search.has("project") ? "projects" : "memories");
  const setActiveTab = (tab: string) =>
    setSearch((previous) => {
      const next = new URLSearchParams(previous);
      next.set("memoryTab", tab);
      return next;
    });
  const items = [
    {
      key: "projects",
      label: formatMessage({ id: "memory.projects" }),
      textLabel: formatMessage({ id: "memory.projects" }),
      children: <ProjectWorkspace />,
    },
    {
      key: "memories",
      label: (
        <Space>
          <UserOutlined />
          {formatMessage({ id: "memory.tabs.memories" })}
        </Space>
      ),
      textLabel: formatMessage({ id: "memory.tabs.memories" }),
      children: (
        <div className="memory-memories-panel">
          <Select
            className="memory-library-select"
            aria-label={formatMessage({ id: "memory.library" })}
            value={memoryKind}
            onChange={setMemoryKind}
            style={{ width: 240 }}
            options={["personal", "reference", "all"].map((value) => ({
              value,
              label: formatMessage({ id: `memory.library.${value}` }),
            }))}
          />
          <MemoryList
            displayMemories={memory.displayMemories}
            loading={memory.loading}
            loadingMore={memory.loadingMore}
            hasMore={memory.hasMore}
            total={memory.total}
            onLoadMore={memory.loadMore}
            search={memory.search}
            onSearchChange={memory.setSearch}
            factTypeFilter={memory.factTypeFilter}
            onFactTypeFilterChange={memory.setFactTypeFilter}
            statusFilter={memory.statusFilter}
            onStatusFilterChange={memory.setStatusFilter}
            projectFilter={memory.projectFilter}
            projectOptions={memory.projectOptions}
            onProjectFilterChange={memory.setProjectFilter}
            onSemanticSearch={memory.handleSemanticSearch}
            searching={memory.searching}
            semanticResults={memory.semanticResults}
            onClearSemantic={memory.clearSemantic}
            onRefresh={memory.load}
            onCreateOpen={() => memory.setCreateOpen(true)}
            onImportOpen={() => memory.setImportOpen(true)}
            onExport={memory.handleExport}
            onDecay={memory.handleDecay}
            decaying={memory.decaying}
            activeAction={memory.activeAction}
            feedbackKey={memory.feedbackKey}
            onFeedback={memory.handleFeedback}
            onStatusChange={memory.handleStatusChange}
            onEdit={(m) => memory.setEditMemory(m)}
            onDelete={memory.handleDelete}
            onRetryIndex={memory.handleRetryIndex}
            editMemory={memory.editMemory}
            onEditClose={() => memory.setEditMemory(null)}
            onEditSubmit={memory.handleEdit}
            createOpen={memory.createOpen}
            onCreateClose={() => memory.setCreateOpen(false)}
            onCreateSubmit={memory.handleCreate}
            importOpen={memory.importOpen}
            onImportClose={() => {
              memory.setImportOpen(false);
              memory.setImportText("");
            }}
            importText={memory.importText}
            onImportTextChange={memory.setImportText}
            onImportSubmit={() => {
              if (memory.importText.trim()) memory.handleImport(memory.importText.trim());
            }}
            isSelectionMode={memory.isSelectionMode}
            selectedKeys={memory.selectedKeys}
            selectedCount={memory.selectedCount}
            onToggleSelectionMode={memory.toggleSelectionMode}
            onToggleSelect={memory.toggleSelection}
            onSelectAll={memory.selectAll}
            onClearSelection={memory.clearSelection}
            onBatchDelete={memory.handleBatchDelete}
          />
        </div>
      ),
    },
    {
      key: "facts",
      label: formatMessage({ id: "memory.facts.tab" }),
      textLabel: formatMessage({ id: "memory.facts.tab" }),
      children: <RecordedFactsPanel />,
    },
    {
      key: "changes",
      label: formatMessage({ id: "memory.changes.tab" }),
      textLabel: formatMessage({ id: "memory.changes.tab" }),
      children: <FactChangesPanel />,
    },
    {
      key: "review",
      label: formatMessage({ id: "workflow.review" }),
      textLabel: formatMessage({ id: "workflow.review" }),
      children: <MeetingReviewPanel />,
    },
    {
      key: "entities",
      label: (
        <Space>
          <BranchesOutlined />
          {formatMessage({ id: "memory.tabs.entities" })}
        </Space>
      ),
      textLabel: formatMessage({ id: "memory.tabs.entities" }),
      children: <EntityGraph userId={userId} />,
    },
    {
      key: "sessions",
      label: (
        <Space>
          <HistoryOutlined />
          {formatMessage({ id: "memory.tabs.sessions" })}
        </Space>
      ),
      textLabel: formatMessage({ id: "memory.tabs.sessions" }),
      children: <SessionSummariesTab userId={userId} />,
    },
  ];

  return (
    <div className="memory-page-shell">
      <Card className="memory-page-card">
        {isNarrow && (
          <Select
            aria-label={formatMessage({ id: "nav.memory" })}
            value={activeTab}
            onChange={setActiveTab}
            options={items.map(({ key, textLabel }) => ({ value: key, label: textLabel }))}
            style={{ width: "100%", marginBottom: 16 }}
          />
        )}
        <Tabs
          className="memory-page-tabs"
          activeKey={activeTab}
          onChange={setActiveTab}
          tabBarStyle={isNarrow ? { display: "none" } : undefined}
          items={items.map(({ key, label, children }) => ({ key, label, children }))}
        />
      </Card>
    </div>
  );
}
