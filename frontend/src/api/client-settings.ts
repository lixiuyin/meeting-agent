import { api } from "./client-core";
import type { components } from "./generated";

export type LLMSettings = components["schemas"]["LLMSettings"];
export type EmbeddingSettings = components["schemas"]["EmbeddingSettings"];
export type RAGSettings = components["schemas"]["RAGSettings"];
export type MemorySettings = components["schemas"]["MemorySettings"];
export type SearchSettings = components["schemas"]["SearchSettings"];
export type UploadSettings = components["schemas"]["UploadSettings"];
export type ASRSettings = components["schemas"]["ASRSettings"];
export type OCRSettings = components["schemas"]["OCRSettings"];
export type VisionSettings = components["schemas"]["VisionSettings"];
export type TTSSettings = components["schemas"]["TTSSettings"];
export type ParserSettings = components["schemas"]["ParserSettings"];
export type RetentionSettings = components["schemas"]["RetentionSettings"];
export type ServerInfo = components["schemas"]["ServerInfo"];
export type SettingsResponse = components["schemas"]["SettingsResponse"];
export type SettingsUpdatePayload = Partial<components["schemas"]["SettingsUpdateRequest"]>;

export interface BindingsResponse {
  llm: string[];
  embedding: string[];
  search: string[];
  reranker: string[];
  tts: string[];
  asr: string[];
  ocr: string[];
  vision: string[];
}

export async function getSettings() {
  return api.get<SettingsResponse>("/settings");
}

export async function updateSettings(settings: SettingsUpdatePayload) {
  return api.put<SettingsResponse>("/settings", settings);
}

export async function getAvailableBindings() {
  return api.get<BindingsResponse>("/settings/bindings");
}

export async function rebuildVectors() {
  return api.post<{ message: string }>("/settings/rebuild-vectors");
}

export async function rebuildMultimodal() {
  return api.post<{ message: string }>("/settings/rebuild-multimodal");
}

export async function reloadConfig() {
  return api.post<{ message: string }>("/settings/reload-config");
}
