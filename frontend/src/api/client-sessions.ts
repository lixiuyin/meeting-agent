import { api } from "./client-core";
import type { SourceItem } from "./client-chat";
import type { components } from "./generated";

export type SessionInfo = components["schemas"]["SessionResponse"];

export function getContinuationPreview(sessionId: string, options?: { signal?: AbortSignal }) {
  return api.get<components["schemas"]["ContinuationPreviewResponse"]>(
    `/sessions/${encodeURIComponent(sessionId)}/continuation-preview`,
    options,
  );
}

type GeneratedSessionSummary = components["schemas"]["SessionSummaryResponse"];
export type SessionSummaryItem = Omit<
  GeneratedSessionSummary,
  "topics" | "key_entities" | "decisions" | "created_at"
> & {
  topics: string[];
  key_entities: string[];
  decisions: string[];
  created_at: string;
};

export type SessionSearchResult = components["schemas"]["SessionSearchResult"];

function normalizeSummary(summary: GeneratedSessionSummary): SessionSummaryItem {
  return {
    ...summary,
    topics: summary.topics ?? [],
    key_entities: summary.key_entities ?? [],
    decisions: summary.decisions ?? [],
    created_at: summary.created_at ?? "",
  };
}

export async function listSessions(options?: {
  limit?: number;
  cursor?: string;
  signal?: AbortSignal;
}) {
  return api.get<{
    total: number;
    sessions: SessionInfo[];
    items: SessionInfo[];
    next_cursor: string | null;
  }>("/sessions", {
    params: { limit: options?.limit, cursor: options?.cursor },
    signal: options?.signal,
  });
}

export async function listAllSessions(options?: { signal?: AbortSignal }): Promise<SessionInfo[]> {
  const sessions: SessionInfo[] = [];
  let cursor: string | undefined;
  do {
    const response = await listSessions({ limit: 100, cursor, signal: options?.signal });
    sessions.push(...response.data.items);
    cursor = response.data.next_cursor ?? undefined;
  } while (cursor);
  return sessions;
}

export async function getSessionMessages(
  sessionId: string,
  options?: { signal?: AbortSignal; limit?: number; beforeId?: number },
) {
  return api.get<{
    session: SessionInfo;
    messages: {
      id?: number;
      role: string;
      content: string;
      sources: SourceItem[];
      degraded?: boolean;
      degradation_reason?: string | null;
    }[];
    total: number;
    next_before_id: number | null;
    pending_run?: { id: string; question: string; status: string } | null;
    session_config?: {
      schema_version: number;
      meeting_ids?: number[] | null;
      file_ids?: number[] | null;
      file_types?: string[] | null;
      date_from?: string | null;
      date_to?: string | null;
      valid_at?: string | null;
      known_at?: string | null;
      use_web_search?: boolean | null;
      rag_mode?: "vector" | "native" | "hybrid" | "multimodal" | "hybrid_multimodal" | "auto";
      retrieval_profile?: "fast" | "balanced" | "thorough";
      memory_mode?: "off" | "focused" | "balanced" | "deep";
      continuation_mode?: "latest" | "saved_scope" | "saved_snapshot";
    } | null;
    task_state?: {
      schema_version: number;
      objective: string;
      last_query?: string;
      resolved_query: string;
      intent: "factual" | "summary" | "comparison" | "exhaustive";
      meeting_ids: number[];
      file_ids: number[];
      active_scope?: {
        meeting_ids: number[];
        file_ids: number[];
        date_from?: string | null;
        date_to?: string | null;
      };
      turn_count?: number;
      open_questions?: string[];
      retrieved_source_ids: string[];
      recalled_memory_keys: string[];
    } | null;
  }>(`/sessions/${sessionId}/messages`, {
    params: { limit: options?.limit, before_id: options?.beforeId },
    signal: options?.signal,
  });
}

export async function branchSession(
  sessionId: string,
  fromMessageId: number,
  reason: "edit" | "regenerate",
) {
  return api.post<{
    session: SessionInfo;
    messages: {
      id?: number;
      role: string;
      content: string;
      sources: SourceItem[];
      degraded?: boolean;
      degradation_reason?: string | null;
    }[];
    total: number;
    next_before_id: number | null;
  }>(`/sessions/${encodeURIComponent(sessionId)}/branches`, {
    from_message_id: fromMessageId,
    reason,
  });
}

export async function deleteSession(sessionId: string, retractDerivedMemories = false) {
  return api.delete(`/sessions/${sessionId}`, {
    params: { retract_derived_memories: retractDerivedMemories },
  });
}

export async function batchDeleteSessions(sessionIds: string[]) {
  return api.post<{ deleted: number; missing: string[] }>("/sessions/batch-delete", {
    session_ids: sessionIds,
  });
}

export async function summarizeSession(sessionId: string) {
  return api.post<{
    summary: string;
    topics: string[];
    key_entities: string[];
    decisions: string[];
  }>(`/sessions/${sessionId}/summarize`);
}

export async function getSessionSummary(sessionId: string, options?: { signal?: AbortSignal }) {
  const response = await api.get<GeneratedSessionSummary>(`/sessions/${sessionId}/summary`, {
    signal: options?.signal,
  });
  return { ...response, data: normalizeSummary(response.data) };
}

export async function searchSessions(query: string, limit = 10, opts?: { signal?: AbortSignal }) {
  return api.post<{ total: number; results: SessionSearchResult[] }>(
    "/sessions/search",
    { query, limit },
    { signal: opts?.signal },
  );
}

export async function listSessionSummaries(
  userId = "default",
  options?: { limit?: number; cursor?: string; signal?: AbortSignal },
) {
  const response = await api.get<components["schemas"]["SessionSummaryListResponse"]>(
    "/sessions/summaries",
    {
      params: { user_id: userId, limit: options?.limit, cursor: options?.cursor },
      signal: options?.signal,
    },
  );
  const summaries = (response.data.summaries ?? response.data.items).map(normalizeSummary);
  return { ...response, data: { ...response.data, summaries } };
}

export async function listAllSessionSummaries(
  userId = "default",
  options?: { signal?: AbortSignal },
): Promise<SessionSummaryItem[]> {
  const summaries: SessionSummaryItem[] = [];
  let cursor: string | undefined;
  do {
    const response = await listSessionSummaries(userId, {
      limit: 100,
      cursor,
      signal: options?.signal,
    });
    summaries.push(...response.data.summaries);
    cursor = response.data.next_cursor ?? undefined;
  } while (cursor);
  return summaries;
}
