import type { AxiosResponse } from "axios";
import type { components } from "./generated";
import {
  ApiError,
  api,
  getApiBaseUrl,
  parseApiErrorPayload,
  TIMEOUT_CHAT,
  TIMEOUT_UPLOAD,
} from "./client-core";

export type MeetingFileInfo = components["schemas"]["MeetingFileResponse"];
export type MeetingInfo = components["schemas"]["MeetingResponse"];
export type MeetingDetailInfo = components["schemas"]["MeetingDetailResponse"];

export type EvidenceLocation = components["schemas"]["EvidenceLocationResponse"];

export function locateFileEvidence(
  meetingId: number,
  fileId: number,
  payload: components["schemas"]["EvidenceLocationRequest"],
  options?: { signal?: AbortSignal },
) {
  return api.post<EvidenceLocation>(
    `/meetings/${meetingId}/files/${fileId}/evidence-location`,
    payload,
    options,
  );
}

export interface SummaryStreamTokenEvent {
  type: "token";
  content: string;
}

export interface SummaryStreamStepEvent {
  type: "step";
  step: string;
  status: "start" | "done";
  completed?: number;
  total?: number;
}

export interface SummaryStreamDoneEvent {
  type: "done";
  meeting_id: number;
  tokens_used: number | null;
}

export interface SummaryStreamErrorEvent {
  type: "error";
  message: string;
  code?: string;
  detail?: string;
  exception_type?: string;
}

export type SummaryStreamEvent =
  | SummaryStreamTokenEvent
  | SummaryStreamStepEvent
  | SummaryStreamDoneEvent
  | SummaryStreamErrorEvent;

export interface TranscriptSegment {
  start: number;
  end: number;
  text: string;
  speaker?: string | null;
}

export interface SpeakerInfo {
  speaker_code: string;
  speaker_name: string | null;
  utterance_count: number;
  sample: TranscriptSegment | null;
}

export interface SpeakersResponse {
  file_id: number;
  speakers: SpeakerInfo[];
}

export interface SpeakerNameMapping {
  speaker_code: string;
  speaker_name: string;
}

export interface UpdateSpeakersResponse {
  file_id: number;
  mappings: SpeakerInfo[];
  message: string;
}

export async function createWebSocketToken(): Promise<string> {
  const { data } = await api.post<{ token: string }>("/ws/token");
  if (!data.token) throw new ApiError(500, "WebSocket token response was empty");
  return data.token;
}

export type FileTimelineResponse =
  | {
      kind: "segments";
      file_id: number;
      file_name: string;
      segments: { start: number; end: number; text: string; speaker?: string | null }[];
      total_duration: number | null;
      speaker_count: number | null;
    }
  | {
      kind: "pages";
      file_id: number;
      file_name: string;
      pages: {
        page_num: number;
        text: string;
        heading: string | null;
        image_assets?: {
          storage_path: string;
          thumbnail_path: string | null;
          caption: string | null;
          ocr_text: string | null;
        }[];
      }[];
      page_count: number;
    }
  | {
      kind: "captions";
      file_id: number;
      file_name: string;
      captions: { caption: string | null; ocr_text: string | null }[];
    }
  | { kind: "text"; file_id: number; file_name: string; text: string; word_count: number };

let _fileTokenCache: { token: string; expiresAt: number } | null = null;
let _tokenRefreshTimer: ReturnType<typeof setTimeout> | null = null;
let _fileTokenGeneration = 0;
const _signedFileUrlCache = new Map<string, { url: string; expiresAt: number }>();
const _signedFileUrlPromises = new Map<string, Promise<string>>();

interface SignedFileUrlResponse {
  url: string;
  token: string;
  expires_at: number;
}

const _fileCacheKey = (meetingId: number, fileId: number) => `${meetingId}:${fileId}`;

async function _fetchSignedFileUrl(meetingId: number, fileId: number): Promise<string> {
  const cacheKey = _fileCacheKey(meetingId, fileId);
  const pending = _signedFileUrlPromises.get(cacheKey);
  if (pending) return pending;
  const request = api
    .post<SignedFileUrlResponse>(`/meetings/${meetingId}/files/${fileId}/signed-url`)
    .then(({ data }) => {
      const absoluteUrl = new URL(data.url, window.location.origin).toString();
      _signedFileUrlCache.set(cacheKey, {
        url: absoluteUrl,
        expiresAt: data.expires_at * 1000,
      });
      return absoluteUrl;
    })
    .finally(() => _signedFileUrlPromises.delete(cacheKey));
  _signedFileUrlPromises.set(cacheKey, request);
  return request;
}

export async function prefetchMeetingFileUrl(meetingId: number, fileId: number): Promise<void> {
  const cacheKey = _fileCacheKey(meetingId, fileId);
  const cached = _signedFileUrlCache.get(cacheKey);
  if (cached && cached.expiresAt > Date.now() + 15_000) return;
  try {
    await _fetchSignedFileUrl(meetingId, fileId);
  } catch {
    // Non-fatal: call sites can fall back to unauthenticated URL in dev mode
  }
}

export async function resolveMeetingFileUrl(meetingId: number, fileId: number): Promise<string> {
  const cacheKey = _fileCacheKey(meetingId, fileId);
  const cached = _signedFileUrlCache.get(cacheKey);
  if (cached && cached.expiresAt > Date.now() + 15_000) return cached.url;
  try {
    return await _fetchSignedFileUrl(meetingId, fileId);
  } catch {
    return new URL(
      `${getApiBaseUrl()}/meetings/${meetingId}/files/${fileId}`,
      window.location.origin,
    ).toString();
  }
}

export async function prefetchMeetingFileUrls(meetingId: number, fileIds: number[]): Promise<void> {
  await Promise.all(fileIds.map((fileId) => prefetchMeetingFileUrl(meetingId, fileId)));
}

async function _refreshFileToken(generation: number): Promise<number> {
  try {
    const { data } = await api.post<{ token: string }>("/meetings/file-token", undefined, {
      timeout: 3_000,
    });
    if (generation !== _fileTokenGeneration) return 240_000;
    _fileTokenCache = {
      token: data.token,
      expiresAt: Date.now() + 280_000,
    };
    return 240_000;
  } catch (error) {
    // Non-fatal: file downloads may fail until token is obtained
    // Respect the endpoint's one-minute rate-limit window; retry other
    // transient failures sooner so an offline startup can recover promptly.
    return error instanceof ApiError && error.status === 429 ? 60_000 : 10_000;
  }
}

let _fileTokenPromise: Promise<void> | null = null;

export async function initFileToken(): Promise<void> {
  if (_fileTokenPromise) return _fileTokenPromise;
  const generation = _fileTokenGeneration;
  const promise = (async () => {
    try {
      const retryDelay = await _refreshFileToken(generation);
      if (generation === _fileTokenGeneration && !_tokenRefreshTimer) {
        _tokenRefreshTimer = setTimeout(() => {
          _tokenRefreshTimer = null;
          void initFileToken();
        }, retryDelay);
      }
    } finally {
      if (generation === _fileTokenGeneration) _fileTokenPromise = null;
    }
  })();
  _fileTokenPromise = promise;
  return promise;
}

export function cleanupFileToken(): void {
  _fileTokenGeneration += 1;
  _fileTokenPromise = null;
  if (_tokenRefreshTimer) {
    clearTimeout(_tokenRefreshTimer);
    _tokenRefreshTimer = null;
  }
  _fileTokenCache = null;
  _signedFileUrlCache.clear();
  _signedFileUrlPromises.clear();
}

export function getMeetingFileUrl(meetingId: number, fileId: number): string {
  const cacheKey = _fileCacheKey(meetingId, fileId);
  const cached = _signedFileUrlCache.get(cacheKey);
  if (cached && cached.expiresAt > Date.now()) {
    return cached.url;
  }
  const url = new URL(
    `${getApiBaseUrl()}/meetings/${meetingId}/files/${fileId}`,
    window.location.origin,
  ).toString();
  // Best-effort async refresh; caller may render fallback URL while this resolves.
  void prefetchMeetingFileUrl(meetingId, fileId);
  return url;
}

export function getMeetingAssetUrl(assetPath: string): string {
  const url = `${getApiBaseUrl()}/meetings/assets?path=${encodeURIComponent(assetPath)}`;
  if (_fileTokenCache?.token) {
    return `${url}&token=${encodeURIComponent(_fileTokenCache.token)}`;
  }
  return url;
}

export async function createMeeting(
  title: string,
  description?: string,
  meetingDate?: string,
): Promise<AxiosResponse<{ meeting_id: number; message: string }>> {
  return api.post<{ meeting_id: number; message: string }>("/meetings", {
    title,
    description,
    meeting_date: meetingDate,
  });
}

export async function uploadMeeting(
  file: File,
  options: {
    title?: string;
    description?: string;
    meetingId?: number;
    businessDomain?: "unspecified" | "meeting" | "course" | "research";
    materialRole?: "transcript" | "minutes" | "agenda" | "decision_log" | "attachment";
    signal?: AbortSignal;
  },
): Promise<
  AxiosResponse<{
    meeting_id: number;
    file_id: number | null;
    message: string;
    is_existing: boolean;
  }>
> {
  const form = new FormData();
  form.append("file", file);
  if (options.title) form.append("title", options.title);
  if (options.description) form.append("description", options.description);
  if (options.meetingId) form.append("meeting_id", options.meetingId.toString());
  if (options.businessDomain) form.append("business_domain", options.businessDomain);
  if (options.materialRole) form.append("material_role", options.materialRole);
  return api.post<{
    meeting_id: number;
    file_id: number | null;
    message: string;
    is_existing: boolean;
  }>("/meetings/upload", form, { signal: options.signal, timeout: TIMEOUT_UPLOAD });
}

export async function listMeetings(params?: {
  status?: string;
  limit?: number;
  offset?: number;
  signal?: AbortSignal;
}): Promise<AxiosResponse<{ total: number; meetings: MeetingInfo[] }>> {
  const { signal, ...query } = params ?? {};
  return api.get<{ total: number; meetings: MeetingInfo[] }>("/meetings", {
    params: query,
    signal,
  });
}

export async function listAllMeetings(options?: {
  status?: string;
  signal?: AbortSignal;
}): Promise<MeetingInfo[]> {
  const pageSize = 100;
  const byId = new Map<number, MeetingInfo>();
  let offset = 0;
  for (;;) {
    const response = await listMeetings({
      status: options?.status,
      limit: pageSize,
      offset,
      signal: options?.signal,
    });
    for (const meeting of response.data.meetings) byId.set(meeting.id, meeting);
    offset += response.data.meetings.length;
    if (response.data.meetings.length < pageSize || offset >= response.data.total) break;
  }
  return Array.from(byId.values());
}

export async function getMeeting(
  id: number,
  opts?: { signal?: AbortSignal },
): Promise<AxiosResponse<MeetingDetailInfo>> {
  const response = await api.get<MeetingDetailInfo>(`/meetings/${id}`, { signal: opts?.signal });
  if (response.data?.files?.length) {
    const readyIds = response.data.files.filter((f) => f.status === "ready").map((f) => f.id);
    void prefetchMeetingFileUrls(id, readyIds);
  }
  return response;
}

export async function deleteMeeting(id: number): Promise<AxiosResponse> {
  return api.delete(`/meetings/${id}`);
}

export async function getMeetingFiles(
  meetingId: number,
): Promise<AxiosResponse<MeetingFileInfo[]>> {
  const response = await api.get<MeetingFileInfo[]>(`/meetings/${meetingId}/files`);
  if (response.data?.length) {
    const readyIds = response.data.filter((f) => f.status === "ready").map((f) => f.id);
    void prefetchMeetingFileUrls(meetingId, readyIds);
  }
  return response;
}

export async function fetchMeetingFile(
  meetingId: number,
  fileId: number,
  options?: { signal?: AbortSignal },
): Promise<AxiosResponse<Blob>> {
  return api.get<Blob>(`/meetings/${meetingId}/files/${fileId}`, {
    responseType: "blob",
    signal: options?.signal,
  });
}

export async function deleteMeetingFile(meetingId: number, fileId: number): Promise<AxiosResponse> {
  return api.delete(`/meetings/${meetingId}/files/${fileId}`);
}

export async function reprocessMeeting(meetingId: number): Promise<AxiosResponse> {
  return api.post(`/meetings/${meetingId}/reprocess`);
}

export async function reprocessMeetingFile(
  meetingId: number,
  fileId: number,
): Promise<AxiosResponse> {
  return api.post(`/meetings/${meetingId}/files/${fileId}/reprocess`);
}

export type MaterialRole = "transcript" | "minutes" | "agenda" | "decision_log" | "attachment";
export type BusinessDomain = "unspecified" | "meeting" | "course" | "research";
export type ApprovalStatus = "unreviewed" | "draft" | "reviewed" | "approved" | "rejected";
export type MeetingFileSemanticEvent = components["schemas"]["MeetingFileSemanticEventResponse"];

export async function updateMeetingFileSemantics(
  meetingId: number,
  fileId: number,
  data: {
    material_role?: MaterialRole;
    business_domain?: BusinessDomain;
    approval_status?: ApprovalStatus;
    approval_reason?: string | null;
  },
): Promise<AxiosResponse<MeetingFileInfo>> {
  return api.patch<MeetingFileInfo>(`/meetings/${meetingId}/files/${fileId}/semantics`, data);
}

export async function getMeetingFileSemanticHistory(
  meetingId: number,
  fileId: number,
): Promise<AxiosResponse<MeetingFileSemanticEvent[]>> {
  return api.get<MeetingFileSemanticEvent[]>(
    `/meetings/${meetingId}/files/${fileId}/semantics/history`,
  );
}

export interface SummaryData {
  meeting_id: number;
  summary: string;
  tokens_used: number | null;
  per_file_summaries?: {
    file_id: number;
    file_name: string;
    file_type: string;
    summary: string;
    key_points: string[];
  }[];
}

export async function updateMeeting(
  meetingId: number,
  data: { title?: string; description?: string; meeting_date?: string },
): Promise<AxiosResponse<MeetingInfo>> {
  return api.put<MeetingInfo>(`/meetings/${meetingId}`, data);
}

export async function getSummary(meetingId: number): Promise<AxiosResponse<SummaryData>> {
  return api.get<SummaryData>(`/meetings/${meetingId}/summary`);
}

export async function generateSummary(meetingId: number): Promise<AxiosResponse<SummaryData>> {
  return api.post<SummaryData>(`/meetings/${meetingId}/summary`);
}

export async function* sendMeetingSummaryStream(
  meetingId: number,
  options?: { signal?: AbortSignal },
): AsyncGenerator<SummaryStreamEvent> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
  };

  const timeoutController = new AbortController();
  const timeoutId = setTimeout(() => timeoutController.abort(), TIMEOUT_CHAT);
  const combinedController = new AbortController();
  const onAbort = () => combinedController.abort();
  timeoutController.signal.addEventListener("abort", onAbort, { once: true });
  options?.signal?.addEventListener("abort", onAbort, { once: true });
  if (timeoutController.signal.aborted || options?.signal?.aborted) {
    combinedController.abort();
  }

  const response = await fetch(`${getApiBaseUrl()}/meetings/${meetingId}/summary/stream`, {
    method: "POST",
    headers,
    body: "{}",
    signal: combinedController.signal,
  });

  if (!response.ok) {
    const raw = await response.text().catch(() => response.statusText);
    let payload: unknown = raw;
    if (typeof raw === "string") {
      try {
        payload = JSON.parse(raw);
      } catch {
        // Keep raw payload.
      }
    }
    const parsed = parseApiErrorPayload(payload, response.statusText || "Request failed");
    throw new ApiError(response.status, parsed.message, {
      code: parsed.code,
      requestId: parsed.requestId,
      details: parsed.details,
    });
  }

  const reader = response.body?.getReader();
  if (!reader) throw new Error("No response body");

  const decoder = new TextDecoder();
  let buffer = "";

  let parseErrors = 0;
  const MAX_PARSE_ERRORS = 5;

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed.startsWith("data: ")) continue;
        const jsonStr = trimmed.slice(6);
        if (!jsonStr) continue;
        try {
          const event = JSON.parse(jsonStr) as SummaryStreamEvent;
          parseErrors = 0;
          yield event;
        } catch {
          parseErrors++;
          if (parseErrors >= MAX_PARSE_ERRORS) {
            throw new ApiError(502, "Summary stream parsing failed — response may be incomplete.");
          }
        }
      }
    }
  } finally {
    clearTimeout(timeoutId);
    timeoutController.signal.removeEventListener("abort", onAbort);
    options?.signal?.removeEventListener("abort", onAbort);
    await reader.cancel().catch(() => {});
  }
}

export async function getTranscript(
  meetingId: number,
  options?: { signal?: AbortSignal },
): Promise<AxiosResponse<{ transcript: string; files?: { file_id: number; content: string }[] }>> {
  return api.get(`/meetings/${meetingId}/transcript`, { signal: options?.signal });
}

export async function getTimestamps(
  meetingId: number,
): Promise<AxiosResponse<{ segments: { start: number; end: number; text: string }[] }>> {
  return api.get<{
    segments: { start: number; end: number; text: string }[];
  }>(`/meetings/${meetingId}/transcript/timestamps`);
}

export async function getFileTimeline(
  meetingId: number,
  fileId: number,
  opts?: { signal?: AbortSignal },
): Promise<AxiosResponse<FileTimelineResponse>> {
  return api.get<FileTimelineResponse>(`/meetings/${meetingId}/files/${fileId}/timeline`, {
    signal: opts?.signal,
  });
}

export async function exportMeeting(
  meetingId: number,
  format: "json" | "markdown" | "txt" = "json",
): Promise<AxiosResponse<{ content: string; filename: string }>> {
  return api.get<{ content: string; filename: string }>(`/meetings/${meetingId}/export`, {
    params: { format },
  });
}

export async function getFileSpeakers(
  meetingId: number,
  fileId: number,
): Promise<AxiosResponse<SpeakersResponse>> {
  return api.get<SpeakersResponse>(`/meetings/${meetingId}/files/${fileId}/speakers`);
}

export async function updateSpeakerNames(
  meetingId: number,
  fileId: number,
  mappings: SpeakerNameMapping[],
): Promise<AxiosResponse<UpdateSpeakersResponse>> {
  return api.put<UpdateSpeakersResponse>(`/meetings/${meetingId}/files/${fileId}/speakers`, {
    mappings,
  });
}

export async function getSpeakerAudioUrl(
  meetingId: number,
  fileId: number,
  speakerCode: string,
): Promise<string> {
  const url = `${getApiBaseUrl()}/meetings/${meetingId}/files/${fileId}/speakers/${encodeURIComponent(speakerCode)}/audio`;
  const signedFileUrl = new URL(await resolveMeetingFileUrl(meetingId, fileId));
  const token = signedFileUrl.searchParams.get("token");
  return token ? `${url}?token=${encodeURIComponent(token)}` : url;
}
