import type { MeetingInfo } from "../../api/client";

export interface MeetingGroup {
  title: string;
  files: MeetingInfo[];
  earliestCreatedAt: string;
  ids: number[];
  number: number;
}
