import { expect, test } from "./fixtures";

test("saved message IDs support immutable edit branches", async ({ request }) => {
  const turnKey = `branch-e2e-${Date.now()}`;
  const chat = await request.post("/api/v1/chat/stream", {
    headers: { "Idempotency-Key": turnKey, Accept: "text/event-stream" },
    data: { question: "hi", memory_mode: "off" },
  });
  expect(chat.ok()).toBeTruthy();
  const events = (await chat.text())
    .split(/\r?\n/)
    .filter((line) => line.startsWith("data: "))
    .map((line) => JSON.parse(line.slice(6)) as Record<string, unknown>);
  const done = events.find((event) => event.type === "done");
  expect(done).toBeTruthy();
  const sessionId = String(done?.session_id);
  const messageIds = done?.message_ids as number[];
  expect(messageIds).toHaveLength(2);

  let branchId: string | undefined;
  try {
    const branch = await request.post(`/api/v1/sessions/${sessionId}/branches`, {
      data: { from_message_id: messageIds[0], reason: "edit" },
    });
    expect(branch.ok()).toBeTruthy();
    const payload = await branch.json();
    branchId = payload.session.id as string;
    expect(payload.session.parent_session_id).toBe(sessionId);
    expect(payload.session.branched_from_message_id).toBe(messageIds[0]);
    expect(payload.session.branch_reason).toBe("edit");
    expect(payload.messages).toEqual([]);

    const source = await request.get(`/api/v1/sessions/${sessionId}/messages`);
    expect(source.ok()).toBeTruthy();
    expect((await source.json()).messages).toHaveLength(2);
  } finally {
    if (branchId) await request.delete(`/api/v1/sessions/${branchId}`);
    await request.delete(`/api/v1/sessions/${sessionId}`);
  }
});
