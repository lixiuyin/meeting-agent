import type { FileTimelineResponse } from "../../../api/client";

export interface DrawerFileItem {
  id: number;
  file_name: string;
  file_type: string;
  status: string;
  summary?: string | null;
  summary_status?: "pending" | "generating" | "ready" | "failed" | null;
}

export interface TimelineState {
  timelineCache: Record<number, FileTimelineResponse>;
  timelineLoading: Record<number, boolean>;
  timelineError: Record<number, string | null>;
}
