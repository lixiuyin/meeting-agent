import { Segmented, Input, Button } from "antd";
import { useIntl } from "react-intl";
import {
  AppstoreOutlined,
  UnorderedListOutlined,
  SearchOutlined,
  SortAscendingOutlined,
  SortDescendingOutlined,
  PlusOutlined,
  UploadOutlined,
  LoadingOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import VirtualMeetingList from "../VirtualMeetingList";
import SkeletonRow from "../SkeletonRow";
import type { MeetingInfo } from "../../api/client";

interface SearchToolbarProps {
  searchQuery: string;
  onSearchQueryChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  isSearching: boolean;
  viewMode: "grid" | "list";
  onViewModeChange: (mode: "grid" | "list") => void;
  sortField: string;
  sortOrder: "asc" | "desc";
  onToggleSortOrder: () => void;
  loading: boolean;
  onRefresh: () => void;
  onNewMeeting: () => void;
  onAddToExisting: () => void;
}

export function SearchToolbar({
  searchQuery,
  onSearchQueryChange,
  isSearching,
  viewMode,
  onViewModeChange,
  sortField,
  sortOrder,
  onToggleSortOrder,
  loading,
  onRefresh,
  onNewMeeting,
  onAddToExisting,
}: SearchToolbarProps) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        flexWrap: "wrap",
      }}
    >
      <Input
        prefix={<SearchOutlined />}
        placeholder="Search materials..."
        value={searchQuery}
        onChange={onSearchQueryChange}
        style={{ width: 240 }}
        allowClear
        suffix={isSearching ? <LoadingOutlined spin /> : null}
      />

      <Segmented
        value={viewMode}
        onChange={(v) => onViewModeChange(v as "grid" | "list")}
        options={[
          { value: "grid", icon: <AppstoreOutlined /> },
          { value: "list", icon: <UnorderedListOutlined /> },
        ]}
      />

      <Button
        icon={sortOrder === "asc" ? <SortAscendingOutlined /> : <SortDescendingOutlined />}
        onClick={onToggleSortOrder}
      >
        {sortField === "date" ? "Date" : sortField === "name" ? "Name" : sortField}
      </Button>

      <Button icon={<ReloadOutlined />} onClick={onRefresh} loading={loading}>
        Refresh
      </Button>

      <Button type="primary" icon={<PlusOutlined />} onClick={onNewMeeting}>
        New Meeting
      </Button>
      <Button icon={<UploadOutlined />} onClick={onAddToExisting}>
        Add Files
      </Button>
    </div>
  );
}

interface MaterialsContentProps {
  loading: boolean;
  hasMeetings: boolean;
  filteredMeetings: MeetingInfo[];
  viewMode: "grid" | "list";
  selectedIds: Set<number>;
  isSelectionMode: boolean;
  isSearching: boolean;
  searchQuery: string;
  onOpenDetail: (meeting: MeetingInfo) => void;
  onToggleSelect: (id: number) => void;
  onDelete: (id: number) => void;
  onReprocess: (id: number) => void;
}

export function MaterialsContent({
  loading,
  hasMeetings,
  filteredMeetings,
  viewMode,
  selectedIds,
  isSelectionMode,
  isSearching,
  searchQuery,
  onOpenDetail,
  onToggleSelect,
  onDelete,
  onReprocess,
}: MaterialsContentProps) {
  const { formatMessage } = useIntl();

  if (loading && !hasMeetings) {
    return (
      <div style={{ padding: 40, display: "flex", flexDirection: "column", gap: 12 }}>
        {Array.from({ length: 6 }).map((_, i) => (
          <SkeletonRow key={i} />
        ))}
      </div>
    );
  }

  if (filteredMeetings.length === 0) {
    return (
      <div
        style={{
          textAlign: "center",
          padding: "80px 20px",
          color: "var(--color-text-secondary)",
        }}
      >
        <div style={{ fontSize: 18, marginBottom: 8 }}>
          {isSearching
            ? formatMessage({ id: "materials.empty.title.searching" })
            : formatMessage({ id: "materials.empty.title.empty" })}
        </div>
        <div style={{ fontSize: 14, opacity: 0.7 }}>
          {isSearching
            ? formatMessage({ id: "materials.empty.hint.searching" }, { query: searchQuery })
            : formatMessage({ id: "materials.empty.hint.empty" })}
        </div>
      </div>
    );
  }

  return (
    <div style={{ height: "calc(100vh - var(--header-height, 72px) - 208px)" }}>
      <VirtualMeetingList
        meetings={filteredMeetings}
        viewMode={viewMode}
        selectedIds={selectedIds}
        isSelectionMode={isSelectionMode}
        onMeetingClick={onOpenDetail}
        onToggleSelect={onToggleSelect}
        onDelete={onDelete}
        onReprocess={onReprocess}
      />
    </div>
  );
}
