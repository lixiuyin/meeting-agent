import { api } from "./client-core";
import type { SourceItem } from "./client-chat";

export interface SkillItem {
  name: string;
  display_name: string;
  description: string;
  examples: string[];
  category: string;
  version: string;
}

export interface SkillInvokeRequest {
  skill_name: string;
  query: string;
  user_id?: string;
  meeting_ids?: number[];
}

export interface SkillInvokeResponse {
  skill_name: string;
  content: string;
  format: string;
  sources: SourceItem[];
  execution_time_ms: number;
}

export interface SkillMatchResponse {
  matched: boolean;
  skill?: {
    name: string;
    display_name: string;
  };
  score?: number;
  details?: Record<string, unknown>;
  ambiguous?: boolean;
  reason?: string;
}

export interface SkillSectionCreateRequest {
  title: string;
  description?: string;
  required?: boolean;
}

export interface SkillCreateRequest {
  name: string;
  display_name: string;
  description: string;
  required_keywords?: string[];
  optional_keywords?: string[];
  examples?: string[];
  threshold?: number;
  sections?: SkillSectionCreateRequest[];
  category?: string;
}

export interface SkillCreateResponse {
  message: string;
  file_path: string;
  skill: SkillItem;
}

export async function listSkills() {
  return api.get<{ skills: SkillItem[]; total: number }>("/skills");
}

export async function invokeSkill(request: SkillInvokeRequest) {
  return api.post<SkillInvokeResponse>("/skills/invoke", request);
}

export async function matchIntent(query: string) {
  return api.post<SkillMatchResponse>("/skills/match", null, { params: { query } });
}

export async function createSkill(request: SkillCreateRequest) {
  return api.post<SkillCreateResponse>("/skills", request);
}
