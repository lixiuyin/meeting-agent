import { api } from "./client-core";

export interface MemoryItem {
  key: string;
  value: string;
  source: string;
  importance: number;
  category: string | null;
  updated_at: string;
  expires_at: string | null;
  access_count?: number;
  superseded_by?: string | null;
  session_id?: string | null;
}

export interface EntityRelation {
  predicate: string;
  other_id: number;
  other_name: string;
  other_type: string;
  direction: "incoming" | "outgoing";
}

export interface EntityItem {
  id: number;
  user_id: string;
  name: string;
  entity_type: string;
  description: string | null;
  mention_count: number;
  created_at: string;
  updated_at: string;
}

export interface EntityWithRelations {
  entity: EntityItem;
  relations: EntityRelation[];
}

export async function listMemories(userId = "default", category?: string) {
  return api.get<{ memories: MemoryItem[] }>("/memory", {
    params: { user_id: userId, ...(category ? { category } : {}) },
  });
}

export async function deleteMemory(key: string, userId = "default") {
  return api.delete("/memory", { params: { key, user_id: userId } });
}

export async function createMemory(
  key: string,
  value: string,
  userId = "default",
  category?: string,
  importance?: number,
  expiresInDays?: number,
) {
  return api.post<MemoryItem>("/memory", {
    user_id: userId,
    key,
    value,
    category,
    importance,
    expires_in_days: expiresInDays,
  });
}

export async function updateMemory(
  key: string,
  value: string,
  userId = "default",
  category?: string,
  importance?: number,
) {
  return api.put<MemoryItem>("/memory", {
    user_id: userId,
    key,
    value,
    category,
    importance,
  });
}

export async function batchImportMemories(
  memories: {
    key: string;
    value: string;
    category?: string;
    importance?: number;
    expires_in_days?: number;
  }[],
  userId = "default",
) {
  return api.post<{ imported: number; failed: number; errors: string[] }>("/memory/batch", {
    user_id: userId,
    memories,
  });
}

export async function exportMemories(userId = "default", includeExpired = false) {
  return api.get<{ user_id: string; total: number; memories: MemoryItem[] }>("/memory/export", {
    params: { user_id: userId, include_expired: includeExpired },
  });
}

export async function searchMemories(
  query: string,
  userId = "default",
  limit = 10,
  minImportance?: number,
) {
  return api.post<{ memories: MemoryItem[]; total: number }>("/memory/search", {
    query,
    user_id: userId,
    limit,
    min_importance: minImportance,
  });
}

export async function triggerDecay(userId = "default") {
  return api.post<{ decayed_count: number }>("/memory/decay", null, {
    params: { user_id: userId },
  });
}

export async function listEntities(userId = "default", entityType?: string) {
  return api.get<{ entities: EntityItem[]; total: number }>("/memory/entities", {
    params: { user_id: userId, ...(entityType ? { entity_type: entityType } : {}) },
  });
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

export async function mergeEntities(userId: string, sourceNames: string[], targetName: string) {
  return api.post("/memory/entities/merge", {
    user_id: userId,
    source_names: sourceNames,
    target_name: targetName,
  });
}
