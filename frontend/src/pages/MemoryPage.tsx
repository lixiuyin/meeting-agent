import { Card, Tabs, Space } from "antd";
import { BranchesOutlined, HistoryOutlined, UserOutlined } from "@ant-design/icons";
import MemoryList from "../components/memory/MemoryList";
import EntityGraph from "../components/memory/EntityGraph";
import SessionSummariesTab from "../components/memory/SessionSummariesTab";
import { useMemoryActions } from "../hooks/useMemoryActions";

export default function MemoryPage() {
  const userId = "default";
  const memory = useMemoryActions(userId);

  return (
    <div style={{ maxWidth: 900, margin: "0 auto", padding: "12px 16px 24px" }}>
      <Card>
        <Tabs
          defaultActiveKey="memories"
          items={[
            {
              key: "memories",
              label: (
                <Space>
                  <UserOutlined />
                  Memories
                </Space>
              ),
              children: (
                <MemoryList
                  displayMemories={memory.displayMemories}
                  loading={memory.loading}
                  search={memory.search}
                  onSearchChange={memory.setSearch}
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
                  onEdit={(m) => memory.setEditMemory(m)}
                  onDelete={memory.handleDelete}
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
              ),
            },
            {
              key: "entities",
              label: (
                <Space>
                  <BranchesOutlined />
                  Entities
                </Space>
              ),
              children: <EntityGraph userId={userId} />,
            },
            {
              key: "sessions",
              label: (
                <Space>
                  <HistoryOutlined />
                  Past Sessions
                </Space>
              ),
              children: <SessionSummariesTab userId={userId} />,
            },
          ]}
        />
      </Card>
    </div>
  );
}
