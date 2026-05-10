import axios from "axios";

export function getApiBaseUrl(): string {
  return (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "/api/v1";
}

export class ApiError extends Error {
  public code?: string;
  public requestId?: string;
  public details?: Record<string, unknown> | null;

  constructor(
    public status: number,
    message: string,
    opts?: {
      code?: string;
      requestId?: string;
      details?: Record<string, unknown> | null;
    },
  ) {
    super(message);
    this.name = "ApiError";
    this.code = opts?.code;
    this.requestId = opts?.requestId;
    this.details = opts?.details;
  }
}

interface ErrorEnvelope {
  code?: string;
  message?: string;
  request_id?: string;
  details?: Record<string, unknown> | null;
  detail?: string;
}

export function parseApiErrorPayload(
  payload: unknown,
  fallbackMessage = "Request failed",
): {
  message: string;
  code?: string;
  requestId?: string;
  details?: Record<string, unknown> | null;
} {
  if (typeof payload === "string" && payload.trim()) {
    return { message: payload };
  }

  if (payload && typeof payload === "object") {
    const data = payload as ErrorEnvelope;
    if (typeof data.message === "string" && data.message.trim()) {
      return {
        message: data.message,
        code: data.code,
        requestId: data.request_id,
        details: data.details ?? null,
      };
    }
    if (typeof data.detail === "string" && data.detail.trim()) {
      return { message: data.detail };
    }
  }

  return { message: fallbackMessage };
}

export function formatApiErrorMessage(error: unknown, fallbackMessage = "Request failed"): string {
  if (error instanceof ApiError) {
    if (error.requestId) {
      return `${error.message} (Request ID: ${error.requestId})`;
    }
    return error.message;
  }
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return fallbackMessage;
}

// H-10: Vite's `import.meta.env.DEV` is tree-shaken to `false` in production
// builds, so VITE_API_KEY is NEVER embedded in production bundles.  This
// dev-mode convenience exists solely for local development.  Production
// deployments MUST use a reverse-proxy to inject the API key or switch to
// session-token / OAuth authentication.
const API_KEY = import.meta.env.DEV
  ? (import.meta.env.VITE_API_KEY as string | undefined)?.trim()
  : undefined;

export function buildAuthHeaders(extra: Record<string, string> = {}): Record<string, string> {
  return API_KEY ? { ...extra, "X-API-Key": API_KEY } : extra;
}

// L-4: Per-endpoint timeout tiers instead of a single 120s default.
// Read endpoints are fast (DB lookups) — 30s is generous.  Chat requests
// may stream responses and deserve 120s.  Upload limits are enforced by
// the server's body-size cap; 600s covers slow connections.
// Each tier can be overridden via VITE_TIMEOUT_*_MS env var.
export const TIMEOUT_READ = Number(import.meta.env.VITE_TIMEOUT_READ_MS) || 30_000;
export const TIMEOUT_CHAT = Number(import.meta.env.VITE_TIMEOUT_CHAT_MS) || 120_000;
export const TIMEOUT_UPLOAD = Number(import.meta.env.VITE_TIMEOUT_UPLOAD_MS) || 600_000;
const DEFAULT_TIMEOUT = TIMEOUT_READ;

export const api = axios.create({
  baseURL: getApiBaseUrl(),
  timeout: DEFAULT_TIMEOUT,
});

// Track in-flight requests so they can be mass-cancelled on route changes.
const _activeSignals = new Set<AbortController>();

export function cancelAllInFlightRequests(reason?: string): void {
  const err = new DOMException(reason ?? "Route change", "AbortError");
  for (const ctrl of _activeSignals) {
    ctrl.abort(err);
    _activeSignals.delete(ctrl);
  }
}

api.interceptors.request.use((config) => {
  // Auto-inject an AbortController when the caller doesn't provide a signal,
  // so the request is always cancellable (e.g. on component unmount).
  // Cleanup is bound to the signal's abort event instead of per-request
  // response interceptors to avoid array bloat under concurrent loads.
  if (!config.signal) {
    const ctrl = new AbortController();
    config.signal = ctrl.signal;
    _activeSignals.add(ctrl);
    ctrl.signal.addEventListener(
      "abort",
      () => {
        _activeSignals.delete(ctrl);
      },
      { once: true },
    );
    // Also clean up on settlement (resolve or reject) via a one-shot
    // response interceptor — this handles the normal case where the request
    // completes without being aborted.
    const interceptorId = api.interceptors.response.use(
      (response) => {
        _activeSignals.delete(ctrl);
        api.interceptors.response.eject(interceptorId);
        return response;
      },
      (error) => {
        _activeSignals.delete(ctrl);
        api.interceptors.response.eject(interceptorId);
        return Promise.reject(error);
      },
    );
  }
  return config;
});

if (API_KEY) {
  api.interceptors.request.use((config) => {
    config.headers.set("X-API-Key", API_KEY);
    return config;
  });
}

api.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error.response?.status ?? 0;
    const parsed = parseApiErrorPayload(error.response?.data, error.message ?? "Request failed");
    return Promise.reject(
      new ApiError(status, parsed.message, {
        code: parsed.code,
        requestId: parsed.requestId,
        details: parsed.details,
      }),
    );
  },
);
