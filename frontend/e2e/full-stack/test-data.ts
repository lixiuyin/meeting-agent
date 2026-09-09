import { expect, type APIRequestContext } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { join } from "node:path";

interface UploadedFile {
  fileId: number;
  meetingId: number;
}

export async function uploadTextFile(
  request: APIRequestContext,
  options: {
    title?: string;
    meetingId?: number;
    name: string;
    content: string;
  },
): Promise<UploadedFile> {
  const response = await request.post("/api/v1/meetings/upload", {
    multipart: {
      ...(options.meetingId ? { meeting_id: String(options.meetingId) } : {}),
      ...(options.title ? { title: options.title } : {}),
      file: {
        name: options.name,
        mimeType: "text/plain",
        buffer: Buffer.from(options.content),
      },
    },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  const payload = (await response.json()) as { meeting_id: number; file_id: number };
  return { meetingId: payload.meeting_id, fileId: payload.file_id };
}

export async function waitForMeetingReady(
  request: APIRequestContext,
  meetingId: number,
): Promise<void> {
  await expect
    .poll(
      async () => {
        const response = await request.get(`/api/v1/meetings/${meetingId}`);
        if (!response.ok()) return `http-${response.status()}`;
        const payload = (await response.json()) as {
          status?: string;
          error_message?: string | null;
          files?: Array<{ status?: string; error_message?: string | null }>;
        };
        if (payload.status !== "failed") return payload.status;
        const fileError = payload.files?.find((file) => file.error_message)?.error_message;
        return `failed:${fileError ?? payload.error_message ?? "unknown processing error"}`;
      },
      { timeout: 60_000, interval: 500 },
    )
    .toBe("ready");
}

export async function deleteMeetingIfPresent(
  request: APIRequestContext,
  meetingId: number,
): Promise<void> {
  const response = await request.delete(`/api/v1/meetings/${meetingId}`);
  if (![200, 204, 404].includes(response.status())) {
    throw new Error(`cleanup failed for meeting ${meetingId}: HTTP ${response.status()}`);
  }
}

function quoteSql(value: string): string {
  return `'${value.replaceAll("'", "''")}'`;
}

export function seedChatSession(options: {
  title: string;
  marker: string;
  turns?: number;
  agentContent?: (marker: string, turn: number) => string;
  agentSources?: unknown[];
}): string {
  const dataDir = process.env.MEETING_AGENT_DATA_DIR;
  if (!dataDir) throw new Error("MEETING_AGENT_DATA_DIR is required to seed an isolated session");
  const sessionId = `e2e${Date.now()}${Math.random().toString(16).slice(2)}`;
  const turns = options.turns ?? 4;
  const principal =
    process.env.E2E_ENVIRONMENT === "production"
      ? (process.env.E2E_PRINCIPAL_ID ?? "e2e_isolated_principal")
      : "default";
  const statements = [
    "PRAGMA busy_timeout=30000",
    `INSERT INTO chat_sessions (id, user_id, title) VALUES (${quoteSql(sessionId)}, ${quoteSql(principal)}, ${quoteSql(options.title)})`,
  ];
  for (let index = 1; index <= turns; index += 1) {
    const agentContent =
      options.agentContent?.(options.marker, index) ?? `${options.marker} agent answer ${index}`;
    const sourcesJson = JSON.stringify(index === 1 ? (options.agentSources ?? []) : []);
    statements.push(
      `INSERT INTO chat_messages (session_id, role, content) VALUES (${quoteSql(sessionId)}, 'human', ${quoteSql(`${options.marker} user question ${index}`)})`,
      `INSERT INTO chat_messages (session_id, role, content, sources_json) VALUES (${quoteSql(sessionId)}, 'ai', ${quoteSql(agentContent)}, ${quoteSql(sourcesJson)})`,
    );
  }
  execFileSync("sqlite3", [join(dataDir, "meetings.db"), `${statements.join(";\n")};`]);
  return sessionId;
}

export async function deleteSessionIfPresent(
  request: APIRequestContext,
  sessionId: string,
): Promise<void> {
  const response = await request.delete(`/api/v1/sessions/${sessionId}`);
  if (![200, 204, 404].includes(response.status())) {
    throw new Error(`cleanup failed for session ${sessionId}: HTTP ${response.status()}`);
  }
}
