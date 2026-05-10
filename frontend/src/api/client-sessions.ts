import { api } from "./client-core";
import type { SourceItem } from "./client-chat";

export interface SessionInfo {
  id: string;
  user_id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

export interface SessionSummaryItem {
  session_id: string;
  summary: string;
  topics: string[];
  key_entities: string[];
  decisions: string[];
  turn_count: number;
  session_title?: string;
  created_at: string;
}

export interface SessionSearchResult {
  type: string;
  session_id: string;
  summary: string | null;
  topics: string[];
  session_title: string | null;
  role: string | null;
  content: string | null;
  created_at: string | null;
}

export async function listSessions() {
  return api.get<{ total: number; sessions: SessionInfo[] }>("/sessions");
}

export async function getSessionMessages(sessionId: string, options?: { signal?: AbortSignal }) {
  return api.get<{
    session: SessionInfo;
    messages: { role: string; content: string; sources: SourceItem[] }[];
    total: number;
  }>(`/sessions/${sessionId}/messages`, { signal: options?.signal });
}

export async function deleteSession(sessionId: string) {
  return api.delete(`/sessions/${sessionId}`);
}

export async function summarizeSession(sessionId: string) {
  return api.post<{
    summary: string;
    topics: string[];
    key_entities: string[];
    decisions: string[];
  }>(`/sessions/${sessionId}/summarize`);
}

export async function getSessionSummary(sessionId: string) {
  return api.get<SessionSummaryItem>(`/sessions/${sessionId}/summary`);
}

export async function searchSessions(query: string, limit = 10, opts?: { signal?: AbortSignal }) {
  return api.post<{ total: number; results: SessionSearchResult[] }>(
    "/sessions/search",
    { query, limit },
    { signal: opts?.signal },
  );
}

export async function listSessionSummaries(userId = "default", limit = 20) {
  return api.get<{ summaries: SessionSummaryItem[] }>("/sessions/summaries", {
    params: { user_id: userId, limit },
  });
}
