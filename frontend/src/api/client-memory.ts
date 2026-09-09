import { api } from "./client-core";
import type { components } from "./generated";

export type MemoryItem = components["schemas"]["MemoryResponse"];
export type MemorySearchItem = components["schemas"]["MemorySearchResultItem"];
export type MemoryFeedbackResponse = components["schemas"]["MemoryFeedbackResponse"];
export type MemoryVersion = components["schemas"]["MemoryVersionResponse"];

export function queryRecordedFacts(
  body: components["schemas"]["FactQueryRequest"],
  options?: { signal?: AbortSignal },
) {
  return api.post<components["schemas"]["FactQueryResponse"]>("/memory/facts/query", body, options);
}

export function compareRecordedFacts(
  body: components["schemas"]["FactChangesRequest"],
  options?: { signal?: AbortSignal },
) {
  return api.post<components["schemas"]["FactChangesResponse"]>(
    "/memory/facts/changes",
    body,
    options,
  );
}

type GeneratedEntityRelation = components["schemas"]["EntityRelationResponse"];

export interface EntityRelation extends GeneratedEntityRelation {
  direction: "incoming" | "outgoing";
}

export type EntityItem = components["schemas"]["EntityResponse"];

export interface EntityWithRelations extends Omit<
  components["schemas"]["EntityWithRelationsResponse"],
  "relations"
> {
  relations: EntityRelation[];
}

export async function listMemories(
  userId = "default",
  options?: {
    category?: string;
    includeExpired?: boolean;
    limit?: number;
    cursor?: string;
    query?: string;
    factType?: string;
    assertionStatus?: string;
    projectId?: string;
    memoryKind?: "all" | "personal" | "reference";
    signal?: AbortSignal;
  },
) {
  return api.get<{
    memories: MemoryItem[];
    items: MemoryItem[];
    total: number;
    next_cursor: string | null;
  }>("/memory", {
    params: {
      user_id: userId,
      category: options?.category,
      include_expired: options?.includeExpired,
      limit: options?.limit,
      cursor: options?.cursor,
      q: options?.query,
      fact_type: options?.factType,
      assertion_status: options?.assertionStatus,
      project_id: options?.projectId,
      memory_kind: options?.memoryKind,
    },
    signal: options?.signal,
  });
}

export async function listAllMemories(
  userId = "default",
  options?: { category?: string; includeExpired?: boolean; signal?: AbortSignal },
): Promise<MemoryItem[]> {
  const memories: MemoryItem[] = [];
  let cursor: string | undefined;
  do {
    const response = await listMemories(userId, { ...options, limit: 100, cursor });
    memories.push(...response.data.items);
    cursor = response.data.next_cursor ?? undefined;
  } while (cursor);
  return memories;
}

export async function deleteMemory(key: string, userId = "default") {
  return api.delete("/memory", { params: { key, user_id: userId } });
}

export async function retryMemoryIndex(key: string) {
  return api.post<MemoryItem>("/memory/retry-index", null, { params: { key } });
}

export async function batchDeleteMemories(keys: string[], userId = "default") {
  return api.post<{ deleted: number; missing: string[] }>("/memory/batch-delete", {
    user_id: userId,
    keys,
  });
}

export async function createMemory(
  key: string,
  value: string,
  userId = "default",
  category?: string,
  importance?: number,
  expiresInDays?: number,
  semantics?: {
    factType?: "fact" | "preference" | "project_fact" | "decision" | "action_item";
    assertionStatus?: "pending" | "confirmed" | "disputed" | "superseded" | "retracted";
    projectId?: string | null;
    validFrom?: string | null;
    validTo?: string | null;
    actionStatus?: "open" | "in_progress" | "blocked" | "done" | "cancelled" | null;
    assignee?: string | null;
    dueAt?: string | null;
  },
) {
  return api.post<MemoryItem>(
    "/memory",
    {
      user_id: userId,
      key,
      value,
      category,
      importance,
      expires_in_days: expiresInDays,
      fact_type: semantics?.factType,
      assertion_status: semantics?.assertionStatus,
      project_id: semantics?.projectId,
      valid_from: semantics?.validFrom,
      valid_to: semantics?.validTo,
      action_status: semantics?.actionStatus,
      assignee: semantics?.assignee,
      due_at: semantics?.dueAt,
    },
    { headers: { "Idempotency-Key": crypto.randomUUID() } },
  );
}

export async function updateMemory(
  key: string,
  value: string,
  expectedRevision: number,
  userId = "default",
  category?: string | null,
  importance?: number,
  semantics?: {
    factType?: "fact" | "preference" | "project_fact" | "decision" | "action_item";
    assertionStatus?: "pending" | "confirmed" | "disputed" | "superseded" | "retracted";
    projectId?: string | null;
    validFrom?: string | null;
    validTo?: string | null;
    actionStatus?: "open" | "in_progress" | "blocked" | "done" | "cancelled" | null;
    assignee?: string | null;
    dueAt?: string | null;
  },
) {
  return api.put<MemoryItem>(
    "/memory",
    {
      user_id: userId,
      key,
      value,
      category,
      importance,
      expected_revision: expectedRevision,
      fact_type: semantics?.factType,
      assertion_status: semantics?.assertionStatus,
      project_id: semantics?.projectId,
      valid_from: semantics?.validFrom,
      valid_to: semantics?.validTo,
      action_status: semantics?.actionStatus,
      assignee: semantics?.assignee,
      due_at: semantics?.dueAt,
    },
    { headers: { "Idempotency-Key": crypto.randomUUID() } },
  );
}

export async function updateMemoryStatus(
  key: string,
  expectedRevision: number,
  assertionStatus: "pending" | "confirmed" | "disputed" | "superseded" | "retracted",
) {
  return api.put<MemoryItem>(
    "/memory",
    {
      key,
      expected_revision: expectedRevision,
      assertion_status: assertionStatus,
    },
    { headers: { "Idempotency-Key": crypto.randomUUID() } },
  );
}

export async function resolveMemoryConflict(
  winnerKey: string,
  expectedRevision: number,
  conflictingKeys: string[],
  expectedConflictRevisions?: Record<string, number>,
) {
  return api.post<{ winner: MemoryItem; superseded_keys: string[] }>("/memory/resolve-conflict", {
    winner_key: winnerKey,
    expected_revision: expectedRevision,
    conflicting_keys: conflictingKeys,
    expected_conflict_revisions: expectedConflictRevisions,
  });
}

export async function listMemoryVersions(key: string, signal?: AbortSignal) {
  return api.get<MemoryVersion[]>("/memory/versions", {
    params: { key, limit: 100 },
    signal,
  });
}

export async function batchImportMemories(
  memories: {
    key: string;
    value: string;
    category?: string;
    importance?: number;
    expires_in_days?: number;
    expires_at?: string;
    fact_type?: "fact" | "preference" | "project_fact" | "decision" | "action_item";
    assertion_status?: "pending" | "confirmed" | "disputed" | "superseded" | "retracted";
    project_id?: string;
    subject?: string;
    predicate?: string;
    object_value?: string;
    action_status?: "open" | "in_progress" | "blocked" | "done" | "cancelled";
    assignee?: string;
    due_at?: string;
    evidence_message_ids?: number[];
    evidence_excerpt?: string;
    conflicts_with?: string[];
    meeting_ids?: number[];
    file_ids?: number[];
  }[],
  userId = "default",
) {
  return api.post<{ imported: number; failed: number; errors: string[] }>(
    "/memory/batch",
    { user_id: userId, memories },
    { headers: { "Idempotency-Key": crypto.randomUUID() } },
  );
}

export async function exportMemories(
  userId = "default",
  options?: { includeExpired?: boolean; limit?: number; cursor?: string; signal?: AbortSignal },
) {
  return api.get<{
    user_id: string;
    total: number;
    memories: MemoryItem[];
    next_cursor: string | null;
  }>("/memory/export", {
    params: {
      user_id: userId,
      include_expired: options?.includeExpired,
      limit: options?.limit,
      cursor: options?.cursor,
    },
    signal: options?.signal,
  });
}

export async function exportAllMemories(
  userId = "default",
  options?: { includeExpired?: boolean; signal?: AbortSignal },
): Promise<MemoryItem[]> {
  const memories: MemoryItem[] = [];
  let cursor: string | undefined;
  do {
    const response = await exportMemories(userId, { ...options, limit: 100, cursor });
    memories.push(...response.data.memories);
    cursor = response.data.next_cursor ?? undefined;
  } while (cursor);
  return memories;
}

export async function searchMemories(
  query: string,
  userId = "default",
  limit = 10,
  minImportance?: number,
  options?: {
    signal?: AbortSignal;
    meetingIds?: number[];
    fileIds?: number[];
    memoryKind?: "all" | "personal" | "reference";
    projectId?: string;
    factType?: string;
    assertionStatus?: string;
  },
) {
  return api.post<{ memories: MemorySearchItem[]; total: number }>(
    "/memory/search",
    {
      query,
      user_id: userId,
      limit,
      min_importance: minImportance,
      meeting_ids: options?.meetingIds,
      file_ids: options?.fileIds,
      memory_kind: options?.memoryKind,
      project_id: options?.projectId,
      fact_type: options?.factType,
      assertion_status: options?.assertionStatus,
    },
    { signal: options?.signal },
  );
}

export async function triggerDecay(userId = "default") {
  return api.post<{ decayed_count: number }>(
    "/memory/decay",
    {},
    {
      params: { user_id: userId },
    },
  );
}

export async function recordMemoryFeedback(key: string, useful: boolean) {
  return api.post<MemoryFeedbackResponse>(
    "/memory/feedback",
    { key, useful },
    { headers: { "Idempotency-Key": crypto.randomUUID() } },
  );
}

export async function listEntities(
  userId = "default",
  options?: { entityType?: string; limit?: number; cursor?: string; signal?: AbortSignal },
) {
  return api.get<{ entities: EntityItem[]; total: number; next_cursor: string | null }>(
    "/memory/entities",
    {
      params: {
        user_id: userId,
        entity_type: options?.entityType,
        limit: options?.limit,
        cursor: options?.cursor,
      },
      signal: options?.signal,
    },
  );
}

export async function listAllEntities(
  userId = "default",
  options?: { entityType?: string; signal?: AbortSignal },
): Promise<EntityItem[]> {
  const entities: EntityItem[] = [];
  let cursor: string | undefined;
  do {
    const response = await listEntities(userId, { ...options, limit: 100, cursor });
    entities.push(...response.data.entities);
    cursor = response.data.next_cursor ?? undefined;
  } while (cursor);
  return entities;
}

export async function getEntity(name: string, userId = "default") {
  return api.get<EntityWithRelations>(`/memory/entities/${encodeURIComponent(name)}`, {
    params: { user_id: userId },
  });
}

export async function deleteEntity(name: string, userId = "default") {
  return api.delete(`/memory/entities/${encodeURIComponent(name)}`, {
    params: { user_id: userId },
  });
}

export async function batchDeleteEntities(names: string[], userId = "default") {
  return api.post<{ deleted: number; missing: string[] }>("/memory/entities/batch-delete", {
    user_id: userId,
    names,
  });
}

export async function mergeEntities(userId: string, sourceNames: string[], targetName: string) {
  return api.post("/memory/entities/merge", {
    user_id: userId,
    source_names: sourceNames,
    target_name: targetName,
  });
}
