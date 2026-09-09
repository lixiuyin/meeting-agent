import { deploymentAuthHeaders, expect, test } from "./fixtures";
import { deleteSessionIfPresent } from "./test-data";

test("detached chat recovers in the browser and duplicate keys never save another turn", async ({
  page,
  request,
  baseURL,
}) => {
  test.setTimeout(180_000);
  const key = crypto.randomUUID();
  const body = {
    question: "Explain a meeting action-item checklist in about 150 words.",
    memory_mode: "off",
  };
  const headers = {
    ...deploymentAuthHeaders(),
    "Content-Type": "application/json",
    "Idempotency-Key": key,
    "X-API-Key": process.env.E2E_API_KEY ?? "",
  };
  const response = await fetch(`${baseURL}/api/v1/chat/stream`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  expect(response.ok).toBeTruthy();
  const runId = response.headers.get("X-Run-ID");
  expect(runId).toBeTruthy();
  await response.body?.cancel(); // disconnect is not cancellation of the run
  const state = await request.get(`/api/v1/chat/runs/${runId}`);
  const run = await state.json();
  try {
    await page.goto(`/?sessionId=${run.session_id}`);
    const recover = page.getByRole("button", { name: "Recover response" });
    await expect(recover.or(page.getByText(body.question, { exact: true }))).toBeVisible({
      timeout: 30_000,
    });
    if (await recover.isVisible()) await recover.click();
    await expect
      .poll(async () => (await (await request.get(`/api/v1/chat/runs/${runId}`)).json()).status, {
        timeout: 120_000,
      })
      .toBe("completed");
    const replay = await request.post("/api/v1/chat/stream", { headers, data: body });
    expect(replay.ok()).toBeTruthy();
    expect((await replay.text()).match(/"type": "done"/g)).toHaveLength(1);
    const saved = await (await request.get(`/api/v1/sessions/${run.session_id}/messages`)).json();
    expect(saved.total).toBe(2);
    expect(saved.messages.filter((m: { role: string }) => m.role === "human")).toHaveLength(1);
    await page.reload();
    await expect(page.getByText(body.question, { exact: true })).toBeVisible();
  } finally {
    await request.post(`/api/v1/chat/runs/${runId}/cancel`);
    await deleteSessionIfPresent(request, run.session_id);
  }
});
