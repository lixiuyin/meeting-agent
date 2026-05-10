import { api } from "./client-core";

export interface LLMSettings {
  binding: string;
  model: string;
  api_key: string;
  base_url: string;
  host: string;
  temperature: number;
  max_tokens: number;
}

export interface EmbeddingSettings {
  binding: string;
  model: string;
  api_key: string;
  base_url: string;
  host: string;
  dimension: number;
}

export interface RAGSettings {
  chunk_size: number;
  chunk_overlap: number;
  top_k: number;
  query_rewrite_enabled: boolean;
  query_rewrite_model: string;
  score_threshold: number;
  distance_metric: "l2" | "cosine" | "ip";
  reranker_binding: string;
  reranker_model: string;
  reranker_api_key: string;
  reranker_base_url: string;
  reranker_top_n: number;
  reranker_min_score: number;
  reranker_timeout_seconds: number;
  fetch_multiplier: number;
  persist_interval_seconds: number;
  parent_child_enabled: boolean;
  child_chunk_size: number;
  child_chunk_overlap: number;
  hybrid_search_enabled: boolean;
  hybrid_alpha: number;
  retriever_provider: "native" | "hybrid" | "multimodal" | "hybrid_multimodal";
  raganything_enabled: boolean;
  raganything_fallback_to_native: boolean;
  raganything_working_dir: string;
  raganything_index_timeout_seconds: number;
  raganything_query_timeout_seconds: number;
  raganything_llm_timeout_seconds: number;
  semantic_chunking_enabled: boolean;
  non_text_chunking_strategy: "native" | "text";
  multi_query_enabled: boolean;
  multi_query_count: number;
  index_tables: boolean;
  index_image_captions: boolean;
  image_ocr_min_length: number;
  content_type_rerank_enabled: boolean;
  sibling_coretrieve_enabled: boolean;
  sibling_coretrieve_per_anchor: number;
  sibling_coretrieve_max_total: number;
  audio_semantic_boundary_enabled: boolean;
  audio_semantic_boundary_threshold: number;
  audio_semantic_min_segments: number;
  audio_semantic_max_segments: number;
  speaker_in_content: boolean;
  split_on_speaker_change: boolean;
}

export interface MemorySettings {
  auto_extract: boolean;
  max_facts_per_turn: number;
  decay_enabled: boolean;
  decay_interval_hours: number;
  ttl_days: number;
  session_max_history: number;
  max_context_items: number;
  session_max_tokens: number;
  session_summary_enabled: boolean;
  session_summary_min_turns: number;
  session_summary_max_items: number;
  session_summary_max_messages: number;
  session_summary_idle_minutes: number;
  session_summary_startup_backfill: boolean;
  consolidation_enabled: boolean;
  consolidation_min_cluster: number;
  semantic_cluster_enabled: boolean;
  knowledge_graph_enabled: boolean;
  profile_enabled: boolean;
  profile_refresh_interval: number;
  extraction_mode: "precise" | "balanced" | "aggressive";
  entity_relations_limit: number;
  global_memory_limit: number;
  skip_threshold: number;
}

export interface SearchSettings {
  binding: string;
  api_key: string;
  region: string;
  max_results: number;
  timeout: number;
  web_search_timeout_s: number;
}

export interface UploadSettings {
  max_size_mb: number;
  auto_summarize_files: boolean;
  multimodal_captioning_enabled: boolean;
  ocr_dedup_enabled: boolean;
  ocr_dedup_timeout_seconds: number;
  video_keyframes_enabled: boolean;
}

export interface ASRSettings {
  provider: string;
  language: string;
  assemblyai_api_key: string;
  speech_model: string;
  speaker_labels: boolean;
  language_detection: boolean;
  poll_interval_seconds: number;
  max_wait_seconds: number;
}

export interface OCRSettings {
  provider: string;
  language: string;
  dpi: number;
  marker_base_url: string;
  marker_api_key: string;
  marker_max_wait_seconds: number;
  mineru_base_url: string;
  mineru_api_key: string;
  mineru_max_wait_seconds: number;
  paddleocr_base_url: string;
  paddleocr_api_key: string;
  http_timeout_seconds: number;
  poll_interval_seconds: number;
}

export interface VisionSettings {
  model: string;
  api_key: string;
  base_url: string;
  retry_max_attempts: number;
  retry_base_delay_seconds: number;
  retry_max_delay_seconds: number;
  caption_min_chars: number;
  ocr_min_chars: number;
}

export interface TTSSettings {
  binding: string;
  model: string;
  api_key: string;
  base_url: string;
  voice: string;
  speed: number;
}

export interface ParserSettings {
  max_parse_pages: number;
  parse_timeout_seconds: number;
  timeout_per_mb_seconds: number;
  timeout_max_seconds: number;
  max_images_per_page: number;
  max_image_bytes: number;
  doc_clean_repetition_min_pages: number;
  doc_clean_repetition_min_ratio: number;
  doc_clean_header_footer_max_lines: number;
  doc_clean_repetition_max_line_length: number;
}

export interface RetentionSettings {
  chat_message_retention_days: number;
  decay_state_retention_days: number;
}

export interface ServerInfo {
  environment: string;
  host: string;
  port: number;
  cors_origins: string;
  trusted_proxies: string;
  trusted_hosts: string;
  security_headers_enabled: boolean;
  security_hsts_max_age: number;
  security_frame_options: string;
  security_referrer_policy: string;
  security_csp: string;
}

export interface SettingsResponse {
  llm: LLMSettings;
  embedding: EmbeddingSettings;
  rag: RAGSettings;
  memory: MemorySettings;
  search: SearchSettings;
  upload: UploadSettings;
  asr: ASRSettings;
  ocr: OCRSettings;
  vision: VisionSettings;
  tts: TTSSettings;
  parser: ParserSettings;
  retention: RetentionSettings;
  server: ServerInfo;
}

export interface SettingsUpdatePayload extends Partial<Omit<SettingsResponse, "server">> {
  confirm_vector_rebuild?: boolean;
}

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
