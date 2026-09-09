import type { Page, Route } from "@playwright/test";

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

export async function installMemoryApiMock(page: Page) {
  const memories = new Map<string, Record<string, unknown>>();
  let nextId = 1;

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();

    if (path === "/api/v1/meetings/file-token") return json(route, { token: "e2e-file-token" });
    if (path === "/api/v1/health") return json(route, { status: "healthy" });
    if (path === "/api/v1/memory/search") {
      const input = request.postDataJSON() as { query?: unknown };
      const query = String(input.query ?? "").toLowerCase();
      const items = [...memories.values()].filter((item) =>
        `${String(item.key)} ${String(item.value)}`.toLowerCase().includes(query),
      );
      return json(route, { memories: items, total: items.length });
    }
    if (path === "/api/v1/memory/review/query")
      return json(route, {
        items: [],
        conflicts: {},
        total: 0,
        next_offset: null,
        snapshot: "mock-review-snapshot",
      });
    if (path === "/api/v1/memory/entities")
      return json(route, { entities: [], total: 0, next_cursor: null });
    if (path === "/api/v1/sessions/summaries")
      return json(route, { summaries: [], items: [], total: 0, next_cursor: null });
    if (path === "/api/v1/memory/decay") return json(route, { decayed_count: 0 });
    if (path === "/api/v1/memory" && method === "GET") {
      const items = [...memories.values()];
      return json(route, { memories: items, items, total: items.length, next_cursor: null });
    }
    if (path === "/api/v1/memory" && method === "POST") {
      const input = request.postDataJSON() as Record<string, unknown>;
      const now = new Date().toISOString();
      const item = {
        ...input,
        id: nextId++,
        key: input.key,
        value: input.value,
        user_id: input.user_id ?? "default",
        category: input.category ?? null,
        importance: input.importance ?? 3,
        access_count: 0,
        useful_count: 0,
        not_useful_count: 0,
        created_at: now,
        updated_at: now,
        expires_at: null,
        superseded_by: null,
        fact_type: input.fact_type ?? "fact",
        assertion_status: input.assertion_status ?? "confirmed",
        action_status: input.action_status ?? null,
        assignee: input.assignee ?? null,
        due_at: input.due_at ?? null,
        project_id: input.project_id ?? null,
        revision: 1,
      };
      memories.set(String(input.key), item);
      return json(route, item, 201);
    }
    if (path === "/api/v1/memory" && method === "PUT") {
      const input = request.postDataJSON() as Record<string, unknown>;
      const key = String(input.key);
      const item = {
        ...(memories.get(key) ?? {}),
        ...input,
        revision: Number(memories.get(key)?.revision ?? 1) + 1,
        updated_at: new Date().toISOString(),
      };
      memories.set(key, item);
      return json(route, item);
    }
    if (path === "/api/v1/memory" && method === "DELETE") {
      memories.delete(url.searchParams.get("key") ?? "");
      return json(route, { deleted: true });
    }

    return json(route, { detail: `Unhandled memory E2E route: ${method} ${path}` }, 501);
  });
}

export async function installMeetingApiMock(page: Page) {
  const meetings = new Map<number, Record<string, unknown>>();
  let nextId = 10_000;
  let currentMeetingId: number | null = null;

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();

    if (path === "/api/v1/meetings/file-token") return json(route, { token: "e2e-file-token" });
    if (path === "/api/v1/ws/token") return json(route, { token: "e2e-ws-token" });
    if (path === "/api/v1/health") return json(route, { status: "healthy" });
    if (path === "/api/v1/meetings" && method === "GET") {
      const items = [...meetings.values()];
      return json(route, { meetings: items, items, total: items.length, next_cursor: null });
    }
    if (path === "/api/v1/meetings" && method === "POST") {
      const input = request.postDataJSON() as Record<string, unknown>;
      const id = nextId++;
      currentMeetingId = id;
      const now = new Date().toISOString();
      meetings.set(id, {
        id,
        title: input.title,
        description: input.description ?? "",
        meeting_date: null,
        file_name: "test-meeting.txt",
        file_type: "txt",
        file_types: ["txt"],
        status: "uploading",
        error_message: null,
        created_at: now,
        updated_at: now,
      });
      return json(route, { meeting_id: id }, 201);
    }
    if (path === "/api/v1/meetings/upload" && method === "POST") {
      await new Promise((resolve) => setTimeout(resolve, 750));
      if (currentMeetingId !== null) {
        const meeting = meetings.get(currentMeetingId);
        if (meeting) meetings.set(currentMeetingId, { ...meeting, status: "ready" });
      }
      return json(route, { meeting_id: currentMeetingId, status: "ready" });
    }
    const meetingMatch = path.match(/^\/api\/v1\/meetings\/(\d+)$/);
    if (meetingMatch && method === "DELETE") {
      meetings.delete(Number(meetingMatch[1]));
      return json(route, { deleted: true });
    }

    return json(route, { detail: `Unhandled meeting E2E route: ${method} ${path}` }, 501);
  });
}

export const SETTINGS_FIXTURE = {
  llm: { binding: "openai", model: "gpt-4o-mini", temperature: 0.2, max_tokens: 1024 },
  embedding: { binding: "openai", model: "text-embedding-3-small", dimension: 1536 },
  rag: {
    chunk_size: 1200,
    chunk_overlap: 200,
    top_k: 5,
    score_threshold: 0,
    query_rewrite_enabled: true,
    hybrid_search_enabled: false,
    hybrid_alpha: 0.5,
  },
  memory: {
    auto_extract: true,
    max_facts_per_turn: 3,
    session_max_history: 20,
    decay_enabled: true,
    ttl_days: 30,
  },
  search: { binding: "duckduckgo", max_results: 5, timeout_sec: 10 },
  upload: { max_upload_size_mb: 500 },
  asr: {},
  ocr: {},
  vision: {},
  tts: {},
  parser: {},
  retention: {},
  server: {},
};

export async function installSettingsApiMock(page: Page) {
  let settings = structuredClone(SETTINGS_FIXTURE);
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/v1/meetings/file-token") return json(route, { token: "e2e-file-token" });
    if (path === "/api/v1/health") return json(route, { status: "healthy" });
    if (path === "/api/v1/settings/rebuild-status")
      return json(route, { active: false, result: "idle" });
    if (path === "/api/v1/settings/bindings") {
      return json(route, {
        llm: ["openai"],
        embedding: ["openai"],
        search: ["duckduckgo"],
        reranker: [],
        tts: [],
        asr: [],
        ocr: [],
        vision: [],
      });
    }
    if (path === "/api/v1/settings" && request.method() === "GET") return json(route, settings);
    if (path === "/api/v1/settings" && request.method() === "PUT") {
      settings = request.postDataJSON() as typeof settings;
      return json(route, settings);
    }
    return json(
      route,
      { detail: `Unhandled settings E2E route: ${request.method()} ${path}` },
      501,
    );
  });
}

/** Stable, read-only data for accessibility and responsive route sweeps. */
export async function installReadOnlyApiMock(page: Page) {
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/v1/memory/projects" || path === "/api/v1/memory/projects/materials")
      return json(route, []);
    if (path === "/api/v1/health") return json(route, { status: "healthy" });
    if (path === "/api/v1/meetings/file-token") return json(route, { token: "e2e-file-token" });
    if (path === "/api/v1/ws/token") return json(route, {}, 503);
    if (path === "/api/v1/meetings")
      return json(route, { meetings: [], items: [], total: 0, next_cursor: null });
    if (path === "/api/v1/sessions")
      return json(route, { sessions: [], items: [], total: 0, next_cursor: null });
    if (path === "/api/v1/memory")
      return json(route, { memories: [], items: [], total: 0, next_cursor: null });
    if (path === "/api/v1/memory/review/query")
      return json(route, {
        items: [],
        conflicts: {},
        total: 0,
        next_offset: null,
        snapshot: "mock-review-snapshot",
      });
    if (path === "/api/v1/memory/entities")
      return json(route, { entities: [], total: 0, next_cursor: null });
    if (path === "/api/v1/sessions/summaries")
      return json(route, { summaries: [], items: [], total: 0, next_cursor: null });
    if (path === "/api/v1/skills") return json(route, { skills: [], total: 0 });
    if (path === "/api/v1/settings") return json(route, SETTINGS_FIXTURE);
    if (path === "/api/v1/settings/rebuild-status")
      return json(route, { active: false, result: "idle" });
    if (path === "/api/v1/settings/bindings") {
      return json(route, {
        llm: ["openai"],
        embedding: ["openai"],
        search: ["duckduckgo"],
        reranker: [],
        tts: [],
        asr: [],
        ocr: [],
        vision: [],
      });
    }
    return json(
      route,
      { detail: `Unhandled read-only E2E route: ${route.request().method()} ${path}` },
      501,
    );
  });
}
