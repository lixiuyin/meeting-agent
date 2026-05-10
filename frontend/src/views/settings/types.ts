import type { BindingsResponse, SettingsResponse } from "../../api/client";

export interface FormValues {
  llm: SettingsResponse["llm"];
  embedding: SettingsResponse["embedding"];
  rag: SettingsResponse["rag"];
  memory: SettingsResponse["memory"];
  search: SettingsResponse["search"];
  upload: SettingsResponse["upload"];
  asr: SettingsResponse["asr"];
  ocr: SettingsResponse["ocr"];
  vision: SettingsResponse["vision"];
  tts: SettingsResponse["tts"];
  parser: SettingsResponse["parser"];
  retention: SettingsResponse["retention"];
  server: SettingsResponse["server"];
}

export type SettingsBindings = BindingsResponse;
