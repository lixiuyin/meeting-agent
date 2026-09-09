import type { SourceItem } from "../../../api/client";

export interface SessionMessage {
  degraded?: boolean;
  degradation_reason?: string | null;
  id?: number;
  role: string;
  content: string;
  sources: SourceItem[];
}

/**
 * Live chat messages use the UI-facing `agent` role while persisted
 * LangChain messages are returned by the sessions API as `ai`.
 */
export const isAgentRole = (role: string) => role === "agent" || role === "ai";
