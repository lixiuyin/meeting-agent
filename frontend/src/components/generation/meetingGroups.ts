import type { MeetingInfo } from "../../api/client";
import type { MeetingGroup } from "./types";

export function buildMeetingGroups(meetings: MeetingInfo[]): MeetingGroup[] {
  const groups = meetings.map((meeting) => ({
    key: String(meeting.id),
    title: meeting.title,
    files: [meeting],
    earliestCreatedAt: meeting.created_at,
    ids: [meeting.id],
    number: 0,
  }));
  groups.sort(
    (a, b) => new Date(a.earliestCreatedAt).getTime() - new Date(b.earliestCreatedAt).getTime(),
  );
  return groups.map((group, index) => ({ ...group, number: index + 1 }));
}
