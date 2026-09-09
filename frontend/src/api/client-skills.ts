import { api, TIMEOUT_CHAT } from "./client-core";
import type { SourceItem } from "./client-chat";
import type { components } from "./generated";

export interface SkillItem {
  name: string;
  display_name: string;
  description: string;
  examples: string[];
  category: string;
  version: string;
}

type GeneratedSkillInvokeRequest = components["schemas"]["SkillInvokeRequest"];
export type SkillInvokeRequest = Omit<GeneratedSkillInvokeRequest, "user_id"> & {
  user_id?: string;
};

export interface SkillInvokeResponse extends Omit<
  components["schemas"]["SkillInvokeResponse"],
  "sources"
> {
  sources: SourceItem[];
}

export type SkillMatchResponse = components["schemas"]["SkillMatchResponse"];

type GeneratedSkillSection = components["schemas"]["SkillSectionCreateRequest"];
export type SkillSectionCreateRequest = Omit<GeneratedSkillSection, "description" | "required"> & {
  description?: string;
  required?: boolean;
};

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

export async function invokeSkill(request: SkillInvokeRequest, options?: { signal?: AbortSignal }) {
  return api.post<SkillInvokeResponse>("/skills/invoke", request, {
    timeout: TIMEOUT_CHAT,
    signal: options?.signal,
  });
}

export async function matchIntent(query: string) {
  return api.post<SkillMatchResponse>("/skills/match", null, { params: { query } });
}

export async function createSkill(request: SkillCreateRequest) {
  return api.post<SkillCreateResponse>("/skills", request);
}
