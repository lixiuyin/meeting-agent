/**
 * Monitoring utilities — Sentry error tracking and Web Vitals performance.
 *
 * Sentry is only initialized when VITE_SENTRY_DSN is configured.
 * Web Vitals are reported to Sentry automatically when available.
 */

import * as Sentry from "@sentry/react";

const SENTRY_DSN = import.meta.env.VITE_SENTRY_DSN as string | undefined;
const APP_ENV = import.meta.env.MODE;

/** Patterns to scrub from Sentry event data to prevent credential leaks. */
const _SENSITIVE_KEY_PATTERNS =
  /(?:api[_-]?key|token|secret|password|auth|credential|session[_-]?id)/i;
const _SENSITIVE_VALUE_PATTERNS = /^(?:sk-|pk-|key_|ghp_|gho_|glpat-|xox[bposa]-)/;

/** Recursively scrub sensitive values from an object. */
export function scrubSensitiveTelemetry(obj: unknown, depth = 0): unknown {
  if (depth > 5 || obj == null || typeof obj !== "object") return obj;
  if (Array.isArray(obj)) return obj.map((v) => scrubSensitiveTelemetry(v, depth + 1));
  const cleaned: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(obj as Record<string, unknown>)) {
    if (_SENSITIVE_KEY_PATTERNS.test(key)) {
      cleaned[key] = "[Filtered]";
    } else if (typeof value === "string" && _SENSITIVE_VALUE_PATTERNS.test(value)) {
      cleaned[key] = "[Filtered]";
    } else {
      cleaned[key] = scrubSensitiveTelemetry(value, depth + 1);
    }
  }
  return cleaned;
}

export function initMonitoring(): void {
  if (!SENTRY_DSN) {
    return; // Skip if no DSN configured
  }

  Sentry.init({
    dsn: SENTRY_DSN,
    environment: APP_ENV,
    integrations: [Sentry.browserTracingIntegration(), Sentry.replayIntegration()],
    tracesSampleRate: APP_ENV === "production" ? 0.1 : 1.0,
    replaysSessionSampleRate: 0.01,
    replaysOnErrorSampleRate: 1.0,
    beforeSend(event) {
      if (event.extra)
        event.extra = scrubSensitiveTelemetry(event.extra) as Record<string, unknown>;
      if (event.tags) event.tags = scrubSensitiveTelemetry(event.tags) as Record<string, string>;
      if (event.request?.headers) {
        for (const key of Object.keys(event.request.headers)) {
          if (_SENSITIVE_KEY_PATTERNS.test(key)) {
            event.request.headers[key] = "[Filtered]";
          }
        }
      }
      return event;
    },
  });
}

export function reportNonCriticalError(
  context: string,
  error: unknown,
  extra?: Record<string, unknown>,
): void {
  if (import.meta.env.DEV) {
    console.warn(`[non-critical] ${context}`, error, extra);
  }
  if (!SENTRY_DSN) return;
  Sentry.withScope((scope) => {
    scope.setLevel("warning");
    scope.setTag("error.classification", "non-critical");
    scope.setTag("error.context", context);
    if (extra) {
      for (const [key, value] of Object.entries(extra)) {
        scope.setExtra(key, value);
      }
    }
    if (error instanceof Error) {
      Sentry.captureException(error);
      return;
    }
    Sentry.captureMessage(`${context}: ${String(error)}`, "warning");
  });
}

/**
 * Report Web Vitals metrics to Sentry.
 * Call this after monitoring is initialized.
 */
export async function reportWebVitals(): Promise<void> {
  if (!SENTRY_DSN) return;

  try {
    const { onCLS, onLCP, onINP, onFCP, onTTFB } = await import("web-vitals");
    const handlers = [onCLS, onLCP, onINP, onFCP, onTTFB];
    for (const fn of handlers) {
      fn((metric) => {
        Sentry.captureMessage(`WebVital:${metric.name}`, {
          level: "info",
          extra: {
            value: metric.value,
            rating: metric.rating,
            delta: metric.delta,
          },
        });
      });
    }
  } catch (err) {
    reportNonCriticalError("load web-vitals", err);
  }
}
