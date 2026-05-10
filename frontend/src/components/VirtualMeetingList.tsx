import { memo } from "react";
import { Virtuoso } from "react-virtuoso";
import MeetingCard from "./MeetingCard";
import MeetingListItem from "./MeetingListItem";
import type { MeetingInfo } from "../api/client";

interface VirtualMeetingListProps {
  meetings: MeetingInfo[];
  viewMode: "grid" | "list";
  selectedIds: Set<number>;
  isSelectionMode: boolean;
  onMeetingClick: (meeting: MeetingInfo) => void;
  onToggleSelect: (id: number, e?: React.MouseEvent | React.KeyboardEvent) => void;
  onDelete: (id: number) => void;
  onReprocess: (id: number) => void;
}

// List item renderer for Virtuoso
const ListItem = memo(function ListItem({
  meeting,
  selectedIds,
  isSelectionMode,
  onMeetingClick,
  onToggleSelect,
  onDelete,
  onReprocess,
}: {
  meeting: MeetingInfo;
  selectedIds: Set<number>;
  isSelectionMode: boolean;
  onMeetingClick: (meeting: MeetingInfo) => void;
  onToggleSelect: (id: number, e?: React.MouseEvent | React.KeyboardEvent) => void;
  onDelete: (id: number) => void;
  onReprocess: (id: number) => void;
}) {
  return (
    <div style={{ padding: "4px 8px" }}>
      <MeetingListItem
        meeting={meeting}
        isSelected={selectedIds.has(meeting.id)}
        isSelectionMode={isSelectionMode}
        onClick={onMeetingClick}
        onToggleSelect={onToggleSelect}
        onDelete={onDelete}
        onReprocess={onReprocess}
      />
    </div>
  );
});

function VirtualMeetingList({
  meetings,
  viewMode,
  selectedIds,
  isSelectionMode,
  onMeetingClick,
  onToggleSelect,
  onDelete,
  onReprocess,
}: VirtualMeetingListProps) {
  // List view virtualization
  if (viewMode === "list") {
    return (
      <Virtuoso
        data={meetings}
        itemContent={(_index, meeting) => (
          <ListItem
            meeting={meeting}
            selectedIds={selectedIds}
            isSelectionMode={isSelectionMode}
            onMeetingClick={onMeetingClick}
            onToggleSelect={onToggleSelect}
            onDelete={onDelete}
            onReprocess={onReprocess}
          />
        )}
        style={{ height: "100%" }}
      />
    );
  }

  // Grid mode uses native CSS grid to avoid virtual-list/grid measurement clipping issues.
  return (
    <div style={{ height: "100%", width: "100%", overflowY: "auto", overflowX: "hidden" }}>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))",
          gap: 16,
          padding: "4px 8px 12px",
        }}
      >
        {meetings.map((meeting) => (
          <MeetingCard
            key={meeting.id}
            meeting={meeting}
            isSelected={selectedIds.has(meeting.id)}
            isSelectionMode={isSelectionMode}
            onClick={onMeetingClick}
            onToggleSelect={onToggleSelect}
            onDelete={onDelete}
            onReprocess={onReprocess}
          />
        ))}
      </div>
    </div>
  );
}

export default memo(VirtualMeetingList);
