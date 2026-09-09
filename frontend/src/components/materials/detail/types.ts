import type { FileTimelineResponse } from "../../../api/client";

export interface DrawerFileItem {
  id: number;
  file_name: string;
  file_type: string;
  status: string;
  summary?: string | null;
  summary_status?: "pending" | "generating" | "ready" | "failed" | null;
  material_role?: "transcript" | "minutes" | "agenda" | "decision_log" | "attachment";
  business_domain?: "unspecified" | "meeting" | "course" | "research";
  approval_status?: "unreviewed" | "draft" | "reviewed" | "approved" | "rejected";
  approval_reason?: string | null;
  source_revision?: number;
  semantic_updated_at?: string | null;
  evidence_sync_status?: "pending" | "syncing" | "ready" | "failed";
  evidence_sync_error?: string | null;
}

export interface TimelineState {
  timelineCache: Record<number, FileTimelineResponse>;
  timelineLoading: Record<number, boolean>;
  timelineError: Record<number, string | null>;
}
