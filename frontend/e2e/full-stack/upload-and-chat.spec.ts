import { expect, test } from "./fixtures";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";

test.setTimeout(180_000);
const CHAT_SLO_TARGET_MS = 3_000;

interface StreamObservation {
  answer: string;
  done: boolean;
  errors: string[];
  sourceItems: Array<{
    file_id?: number | null;
    file_name?: string | null;
    meeting_id?: number;
  }>;
  sessionId: string | null;
  spanCount: number;
  spans: Array<{
    duration_ms?: number | null;
    end_offset_ms?: number | null;
    label?: string;
    phase?: string;
    skipped?: boolean;
    start_offset_ms?: number | null;
    status?: string;
  }>;
  traceId: string | null;
  traceTotalMs: number | null;
  totalMs: number | null;
  ttftMs: number | null;
}

interface ProcessSpan {
  duration_ms?: number | null;
  end_offset_ms?: number | null;
  error_message?: string;
  error_type?: string;
  label: string;
  phase: string;
  sequence?: number;
  skipped?: boolean;
  start_offset_ms?: number | null;
  status: string;
}

interface IngestTrace {
  error_message?: string;
  error_span?: string;
  error_type?: string;
  file_id: number;
  meeting_id: number | null;
  process: "ingest";
  ready_status: string;
  schema_version: 1;
  spans: ProcessSpan[];
  terminal_status: string;
  timestamp: string;
  total_ms: number;
  trace_id: string;
}

interface SmokeMetrics {
  schema_version: 1;
  command: "e2e-smoke";
  captured_at: string;
  source_revision: string | null;
  dataset_fingerprint_sha256: string | null;
  harness_fingerprint_sha256: string | null;
  implementation_fingerprint_sha256: string | null;
  stats: {
    upload_to_ready: number;
    chat_ttft: number;
    chat_total: number;
  };
  assertions: {
    answer_has_citation: boolean;
    answer_has_expected_fact: boolean;
    chat_latency_slo_ok: boolean;
    dead_letter_jobs: number;
    ingest_required_spans_ok: boolean;
    ingest_terminal_success: boolean;
    ingest_trace_id: string;
    ingest_trace_span_count: number;
    readiness_checks_ok: boolean;
    source_count: number;
    source_identity_ok: boolean;
    terminal_done: boolean;
    trace_id: string;
    trace_span_count: number;
  };
  diagnostics: {
    chat_trace: {
      answer: string;
      session_id: string;
      sources: StreamObservation["sourceItems"];
      spans: StreamObservation["spans"];
      terminal_status: "success";
      trace_id: string;
    };
    ingest_trace: IngestTrace;
    trace_total_ms: number | null;
    spans: StreamObservation["spans"];
  };
}

async function readIngestTrace(fileId: number): Promise<IngestTrace | null> {
  const dataDir = process.env.MEETING_AGENT_DATA_DIR;
  if (!dataDir) throw new Error("MEETING_AGENT_DATA_DIR is required for the full-stack smoke");
  try {
    const content = await readFile(join(dataDir, "logs", "ingest.jsonl"), "utf8");
    const matches = content
      .split(/\r?\n/)
      .filter(Boolean)
      .map((line) => JSON.parse(line) as IngestTrace)
      .filter((trace) => trace.process === "ingest" && trace.file_id === fileId);
    return matches.at(-1) ?? null;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw error;
  }
}

declare global {
  interface Window {
    __meetingAgentStreamObservation?: StreamObservation;
  }
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    const originalFetch = window.fetch.bind(window);
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      if (!url.includes("/api/v1/chat/stream")) return originalFetch(input, init);

      const started = performance.now();
      const response = await originalFetch(input, init);
      if (!response.body) return response;

      const [applicationBody, observationBody] = response.body.tee();
      const observation: StreamObservation = {
        answer: "",
        done: false,
        errors: [],
        sourceItems: [],
        sessionId: null,
        spanCount: 0,
        spans: [],
        traceId: null,
        traceTotalMs: null,
        totalMs: null,
        ttftMs: null,
      };
      window.__meetingAgentStreamObservation = observation;

      void (async () => {
        const reader = observationBody.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        const consumeFrame = (frame: string) => {
          for (const line of frame.split(/\r?\n/)) {
            if (!line.startsWith("data:")) continue;
            try {
              const event = JSON.parse(line.slice(5).trim()) as {
                type?: string;
                content?: string;
                items?: StreamObservation["sourceItems"];
                message?: string;
                session_id?: string;
                trace?: {
                  trace_id?: string;
                  total_ms?: number;
                  spans?: StreamObservation["spans"];
                };
              };
              if (event.type === "token" && event.content) {
                observation.ttftMs ??= performance.now() - started;
                observation.answer += event.content;
              } else if (event.type === "sources") {
                observation.sourceItems = event.items ?? [];
              } else if (event.type === "trace") {
                observation.traceId = event.trace?.trace_id ?? null;
                observation.spanCount = event.trace?.spans?.length ?? 0;
                observation.spans = (event.trace?.spans ?? []).map((span) => ({
                  duration_ms: span.duration_ms,
                  end_offset_ms: span.end_offset_ms,
                  label: span.label,
                  phase: span.phase,
                  skipped: span.skipped,
                  start_offset_ms: span.start_offset_ms,
                  status: span.status,
                }));
                observation.traceTotalMs = event.trace?.total_ms ?? null;
              } else if (event.type === "error") {
                observation.errors.push(event.message ?? "unknown stream error");
              } else if (event.type === "done") {
                observation.done = true;
                observation.sessionId = event.session_id ?? null;
                observation.totalMs = performance.now() - started;
              }
            } catch (error) {
              observation.errors.push(`invalid SSE JSON: ${String(error)}`);
            }
          }
        };

        try {
          while (true) {
            const { done, value } = await reader.read();
            buffer += decoder.decode(value, { stream: !done });
            const frames = buffer.split(/\r?\n\r?\n/);
            buffer = frames.pop() ?? "";
            frames.forEach(consumeFrame);
            if (done) break;
          }
          if (buffer.trim()) consumeFrame(buffer);
        } catch (error) {
          observation.errors.push(`stream observation failed: ${String(error)}`);
        }
      })();

      return new Response(applicationBody, {
        headers: response.headers,
        status: response.status,
        statusText: response.statusText,
      });
    };
  });
});

test("upload -> ready -> cited chat -> clean queue", async ({ page, request }, testInfo) => {
  const meetingTitle = `E2E Sample Meeting ${Date.now()}`;
  const fixture = await readFile(
    new URL("../../../backend/tests/fixtures/benchmark/e2e-smoke.txt", import.meta.url),
  );
  const uploadStarted = performance.now();
  const uploadResponse = await request.post("/api/v1/meetings/upload", {
    multipart: {
      title: meetingTitle,
      file: {
        name: "sample.txt",
        mimeType: "text/plain",
        buffer: fixture,
      },
    },
  });
  expect(uploadResponse.ok()).toBeTruthy();
  const uploadPayload = await uploadResponse.json();
  const meetingId = uploadPayload.meeting_id as number;
  const fileId = uploadPayload.file_id as number;

  try {
    await expect
      .poll(
        async () => {
          const detail = await request.get(`/api/v1/meetings/${meetingId}`);
          if (!detail.ok()) return "missing";
          const payload = await detail.json();
          return payload.status;
        },
        { timeout: 60_000, interval: 2_000 },
      )
      .toBe("ready");
    const uploadToReadyMs = performance.now() - uploadStarted;

    await expect
      .poll(() => readIngestTrace(fileId), { timeout: 15_000, interval: 100 })
      .not.toBeNull();
    const ingestTrace = await readIngestTrace(fileId);
    if (!ingestTrace) throw new Error(`Missing ingest trace for file ${fileId}`);
    const ingestLabels = new Set(ingestTrace.spans.map((span) => span.label));
    const ingestRequiredSpansOk =
      ingestLabels.has("fetch_metadata") &&
      (ingestLabels.has("parse") || ingestLabels.has("transcribe")) &&
      ingestLabels.has("index_meeting") &&
      ingestLabels.has("db_persist");
    const ingestTerminalSuccess =
      ingestTrace.terminal_status === "success" &&
      ingestTrace.ready_status === "ready" &&
      ingestTrace.spans.every((span) => span.status !== "running");
    expect(ingestRequiredSpansOk).toBeTruthy();
    expect(ingestTerminalSuccess).toBeTruthy();

    await expect
      .poll(
        async () => {
          const list = await request.get("/api/v1/meetings?status=ready&limit=100");
          if (!list.ok()) return false;
          const payload = await list.json();
          return (payload.meetings ?? []).some((m: { id: number }) => m.id === meetingId);
        },
        { timeout: 30_000, interval: 2_000 },
      )
      .toBeTruthy();

    await page.goto("/");
    await expect(page.getByPlaceholder(/ask anything about your meetings/i)).toBeVisible();

    const meetingSelect = page.getByRole("combobox").first();
    await expect(meetingSelect).toBeVisible();
    await meetingSelect.click();
    await meetingSelect.fill(meetingTitle);
    const meetingOption = page
      .locator(".ant-select-dropdown:visible .ant-select-item-option")
      .filter({ hasText: meetingTitle })
      .first();
    await expect(meetingOption).toBeVisible({ timeout: 15_000 });
    await meetingOption.click();
    // Antd multi-mode keeps the dropdown open after selection — close it so
    // subsequent assertions don't collide with the still-visible option row.
    await page.keyboard.press("Escape");
    await expect(page.locator(".ant-tag").filter({ hasText: meetingTitle })).toBeVisible();

    await page
      .getByPlaceholder(/ask anything about your meetings/i)
      .fill("What are the release codename, owner, and target date? Cite the source.");
    const streamReq = page.waitForResponse(
      (resp) => resp.url().includes("/api/v1/chat/stream") && resp.status() === 200,
      { timeout: 60_000 },
    );
    await page.keyboard.press("Enter");
    await streamReq;
    await expect(page.getByPlaceholder(/ask anything about your meetings/i)).toHaveValue("");
    await expect(
      page.getByRole("heading", { name: /how can i help you today/i }),
    ).not.toBeVisible();

    await expect
      .poll(() => page.evaluate(() => window.__meetingAgentStreamObservation?.done ?? false), {
        timeout: 120_000,
        interval: 250,
      })
      .toBeTruthy();

    const observation = await page.evaluate(() => window.__meetingAgentStreamObservation);
    expect(observation).toBeTruthy();
    expect(observation?.errors).toEqual([]);
    expect(observation?.ttftMs).toBeGreaterThan(0);
    expect(observation?.totalMs).toBeGreaterThanOrEqual(observation?.ttftMs ?? Infinity);
    expect(observation?.ttftMs).toBeLessThanOrEqual(CHAT_SLO_TARGET_MS);
    expect(observation?.totalMs).toBeLessThanOrEqual(CHAT_SLO_TARGET_MS);
    expect(observation?.answer).toMatch(/ORBIT-742/i);
    expect(observation?.answer).toMatch(/\[\d+\]/);
    expect(observation?.traceId).toBeTruthy();
    expect(observation?.sessionId).toBeTruthy();
    expect(observation?.spanCount).toBeGreaterThan(0);
    expect(observation?.sourceItems.length).toBeGreaterThan(0);
    const sourceIdentityOk =
      observation?.sourceItems.some(
        (source) =>
          source.meeting_id === meetingId &&
          source.file_id === fileId &&
          source.file_name === "sample.txt",
      ) ?? false;
    expect(sourceIdentityOk).toBeTruthy();

    const readyResponse = await request.get("/api/v1/health/ready");
    expect(readyResponse.ok()).toBeTruthy();
    const readyPayload = (await readyResponse.json()) as {
      checks?: Record<string, string>;
    };
    const requiredChecks = ["startup", "database", "fts5", "job_queue", "storage"];
    const readinessChecksOk = requiredChecks.every((name) => readyPayload.checks?.[name] === "ok");
    expect(readinessChecksOk).toBeTruthy();

    const jobsResponse = await request.get("/api/v1/health/jobs");
    expect(jobsResponse.ok()).toBeTruthy();
    const jobsPayload = (await jobsResponse.json()) as { counts?: Record<string, number> };
    const deadLetterJobs = jobsPayload.counts?.dead_letter ?? 0;
    expect(deadLetterJobs).toBe(0);

    const metrics: SmokeMetrics = {
      schema_version: 1,
      command: "e2e-smoke",
      captured_at: new Date().toISOString(),
      source_revision: process.env.E2E_SOURCE_REVISION ?? null,
      dataset_fingerprint_sha256: process.env.E2E_DATASET_FINGERPRINT ?? null,
      harness_fingerprint_sha256: process.env.E2E_HARNESS_FINGERPRINT ?? null,
      implementation_fingerprint_sha256: process.env.E2E_IMPLEMENTATION_FINGERPRINT ?? null,
      stats: {
        upload_to_ready: uploadToReadyMs,
        chat_ttft: observation?.ttftMs ?? 0,
        chat_total: observation?.totalMs ?? 0,
      },
      assertions: {
        answer_has_citation: /\[\d+\]/.test(observation?.answer ?? ""),
        answer_has_expected_fact: /ORBIT-742/i.test(observation?.answer ?? ""),
        chat_latency_slo_ok:
          (observation?.ttftMs ?? Infinity) <= CHAT_SLO_TARGET_MS &&
          (observation?.totalMs ?? Infinity) <= CHAT_SLO_TARGET_MS,
        dead_letter_jobs: deadLetterJobs,
        ingest_required_spans_ok: ingestRequiredSpansOk,
        ingest_terminal_success: ingestTerminalSuccess,
        ingest_trace_id: ingestTrace.trace_id,
        ingest_trace_span_count: ingestTrace.spans.length,
        readiness_checks_ok: readinessChecksOk,
        source_count: observation?.sourceItems.length ?? 0,
        source_identity_ok: sourceIdentityOk,
        terminal_done: observation?.done ?? false,
        trace_id: observation?.traceId ?? "",
        trace_span_count: observation?.spanCount ?? 0,
      },
      diagnostics: {
        chat_trace: {
          answer: observation?.answer ?? "",
          session_id: observation?.sessionId ?? "",
          sources: observation?.sourceItems ?? [],
          spans: observation?.spans ?? [],
          terminal_status: "success",
          trace_id: observation?.traceId ?? "",
        },
        ingest_trace: ingestTrace,
        trace_total_ms: observation?.traceTotalMs ?? null,
        spans: observation?.spans ?? [],
      },
    };

    const metricsJson = JSON.stringify(metrics, null, 2);
    await testInfo.attach("e2e-smoke-metrics", {
      body: metricsJson,
      contentType: "application/json",
    });
    console.log(`E2E_SMOKE_METRICS=${JSON.stringify(metrics)}`);
    const outputPath = process.env.E2E_METRICS_OUTPUT;
    if (outputPath) {
      await mkdir(dirname(outputPath), { recursive: true });
      await writeFile(outputPath, `${metricsJson}\n`, "utf8");
    }
  } finally {
    await request.delete(`/api/v1/meetings/${meetingId}`);
  }
});
