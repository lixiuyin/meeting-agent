import { test, expect, type Page } from "@playwright/test";

async function mockSuccessfulChatStream(page: Page, delayMs = 0) {
  await page.route("**/api/v1/chat/stream", async (route) => {
    const payload = route.request().postDataJSON() as { question?: string };
    const answer = `Mock answer for ${payload.question ?? "question"}`;
    const body = [
      `data: ${JSON.stringify({ type: "token", content: answer })}`,
      `data: ${JSON.stringify({ type: "done", session_id: "sess-e2e-chat", message_ids: [101, 102] })}`,
      "",
    ].join("\n");
    if (delayMs > 0) await new Promise((resolve) => setTimeout(resolve, delayMs));
    await route.fulfill({
      status: 200,
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Run-ID": "run-e2e-chat",
      },
      body,
    });
  });
}

test.describe("Chat and streaming", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
    // Wait for the chat page to fully render
    await expect(page.getByLabel("Send message")).toBeVisible();
  });

  test("should display chat input on home page", async ({ page }) => {
    const chatInput = page.getByPlaceholder("Ask anything about your meetings...");
    await expect(chatInput).toBeVisible();
    await expect(chatInput).toBeEditable();
  });

  test("should display welcome state when no messages exist", async ({ page }) => {
    // Empty state should show the welcome message
    await expect(page.getByText("How can I help you today?")).toBeVisible();
    await expect(
      page.getByText(/Ask me anything about your meetings. I can summarize discussions/i),
    ).toBeVisible();
  });

  test("should display quick question buttons", async ({ page }) => {
    // Quick questions should be visible in the empty state
    await expect(page.getByRole("button", { name: "Summarize this meeting" })).toBeVisible();
    await expect(page.getByRole("button", { name: "List key action items" })).toBeVisible();
    await expect(page.getByRole("button", { name: "What decisions were made?" })).toBeVisible();
  });

  test("should handle empty message submission gracefully", async ({ page }) => {
    // The send button should be disabled when input is empty
    const sendButton = page.getByLabel("Send message");

    // With empty input, the send button should be disabled
    const chatInput = page.getByPlaceholder("Ask anything about your meetings...");
    await chatInput.fill("");
    await expect(sendButton).toBeDisabled();
  });

  test("should reject a source-only stream as an empty model response", async ({ page }) => {
    await page.route("**/api/v1/chat/stream", async (route) => {
      const source = {
        meeting_id: 1,
        file_id: 1,
        file_name: "candidate.pdf",
        chunk_index: 0,
        content: "retrieved candidate without an answer",
        source_kind: "page",
      };
      const body = [
        `data: ${JSON.stringify({ type: "sources", items: [source] })}`,
        `data: ${JSON.stringify({ type: "done", session_id: "must-not-be-accepted" })}`,
        "",
      ].join("\n");
      await route.fulfill({
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
        body,
      });
    });

    const input = page.getByPlaceholder("Ask anything about your meetings...");
    await input.fill("What was discussed?");
    await input.press("Enter");

    await expect(
      page.getByText("The model returned no usable answer. Please retry.", { exact: true }),
    ).toBeVisible();
    await expect(page.getByText(/Cited sources/)).toHaveCount(0);
  });

  test("should render degraded generation as a warning separate from answer text", async ({
    page,
  }) => {
    await page.route("**/api/v1/chat/stream", async (route) => {
      const body = [
        `data: ${JSON.stringify({ type: "status", status: "degraded", reason: "fast_path_timeout" })}`,
        `data: ${JSON.stringify({ type: "token", content: "Relevant source excerpts (partial result):\n\n- The launch is planned for November." })}`,
        `data: ${JSON.stringify({ type: "done", session_id: "sess-degraded" })}`,
        "",
      ].join("\n");
      await route.fulfill({
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
        body,
      });
    });

    const input = page.getByPlaceholder("Ask anything about your meetings...");
    await input.fill("What is the launch date?");
    await input.press("Enter");

    await expect(
      page.getByText("This answer is incomplete. Retry to generate a complete answer.", {
        exact: true,
      }),
    ).toBeVisible();
    await expect(page.getByText("Relevant source excerpts (partial result):")).toBeVisible();
  });

  test("should enable send button when message is typed", async ({ page }) => {
    const chatInput = page.getByPlaceholder("Ask anything about your meetings...");
    const sendButton = page.getByLabel("Send message");

    await chatInput.fill("Hello, test message");
    await expect(sendButton).toBeEnabled();
  });

  test("should display New Chat button", async ({ page }) => {
    await expect(page.getByRole("button", { name: /new chat/i })).toBeVisible();
  });

  test("should clear chat when New Chat is clicked", async ({ page }) => {
    // Type a message
    const chatInput = page.getByPlaceholder("Ask anything about your meetings...");
    await chatInput.fill("Some test message");

    // Click new chat
    await page.getByRole("button", { name: /new chat/i }).click();

    // Input should be cleared and we should be back to welcome state
    await expect(chatInput).toHaveValue("");
    await expect(page.getByText("How can I help you today?")).toBeVisible();
  });

  test("new chat clears restored request-scoped filters before sending", async ({ page }) => {
    await page.route("**/api/v1/sessions/session-filter-test/messages*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          session: { id: "session-filter-test" },
          messages: [{ id: 11, role: "human", content: "Scoped old question", sources: [] }],
          total: 1,
          next_before_id: null,
          session_config: {
            schema_version: 1,
            meeting_ids: [7],
            file_ids: [9],
            file_types: ["pdf"],
            date_from: "2024-01-01",
            date_to: "2024-12-31",
            valid_at: "2024-06-01T00:00:00Z",
            known_at: "2024-07-01T00:00:00Z",
            use_web_search: true,
            rag_mode: "hybrid",
            continuation_mode: "saved_scope",
          },
        }),
      });
    });
    await mockSuccessfulChatStream(page);
    await page.goto("/?sessionId=session-filter-test");
    await expect(page.getByText("Scoped old question", { exact: true })).toBeVisible();

    const restoredRequestPromise = page.waitForRequest(
      (request) =>
        request.method() === "POST" && new URL(request.url()).pathname === "/api/v1/chat/stream",
    );
    const input = page.getByPlaceholder("Ask anything about your meetings...");
    await input.fill("Continue with restored scope");
    await input.press("Enter");
    const restoredPayload = (await restoredRequestPromise).postDataJSON() as Record<
      string,
      unknown
    >;
    expect(restoredPayload).toMatchObject({
      session_id: "session-filter-test",
      meeting_ids: [7],
      file_types: ["pdf"],
      date_from: "2024-01-01",
      date_to: "2024-12-31",
      valid_at: "2024-06-01T00:00:00.000Z",
      known_at: "2024-07-01T00:00:00.000Z",
      use_web_search: true,
      rag_mode: "hybrid",
      continuation_mode: "saved_scope",
    });
    await expect(page.getByText("Mock answer for Continue with restored scope")).toBeVisible();

    await page.getByRole("button", { name: /new chat/i }).click();
    const requestPromise = page.waitForRequest(
      (request) =>
        request.method() === "POST" && new URL(request.url()).pathname === "/api/v1/chat/stream",
    );
    await input.fill("Fresh unscoped question");
    await input.press("Enter");
    const payload = (await requestPromise).postDataJSON() as Record<string, unknown>;

    expect(payload).not.toHaveProperty("session_id");
    expect(payload).not.toHaveProperty("meeting_ids");
    expect(payload).not.toHaveProperty("file_ids");
    expect(payload).not.toHaveProperty("file_types");
    expect(payload).not.toHaveProperty("date_from");
    expect(payload).not.toHaveProperty("date_to");
    expect(payload).not.toHaveProperty("valid_at");
    expect(payload).not.toHaveProperty("known_at");
    expect(payload).toMatchObject({
      use_web_search: false,
      rag_mode: "auto",
      continuation_mode: "latest",
    });
  });

  test("should send a message via quick question button", async ({ page }) => {
    await mockSuccessfulChatStream(page);
    // Click a quick question button to send a message
    const quickButton = page.getByRole("button", {
      name: "What decisions were made?",
    });
    await quickButton.click();

    // The welcome state should disappear — messages area should render
    // The user message should appear in the chat
    await expect(page.getByText("What decisions were made?", { exact: true })).toBeVisible({
      timeout: 5_000,
    });
  });

  test("should expose stop generation and cancel a pre-header send", async ({ page }) => {
    await mockSuccessfulChatStream(page, 750);
    // Send a question to trigger streaming
    const chatInput = page.getByPlaceholder("Ask anything about your meetings...");
    await chatInput.fill("Test streaming question");
    await chatInput.press("Enter");

    const stop = page.getByRole("button", { name: "Stop generating" });
    await expect(stop).toBeVisible({ timeout: 5_000 });
    await stop.click();
    await expect(page.getByLabel("Send message")).toBeVisible();
    await expect(page.getByText("Test streaming question", { exact: true })).toHaveCount(0);
  });

  test("should enter explicit edit-and-branch mode for a saved user message", async ({ page }) => {
    await mockSuccessfulChatStream(page);
    const input = page.getByPlaceholder("Ask anything about your meetings...");
    await input.fill("Original branch question");
    await input.press("Enter");
    await expect(page.getByText("Mock answer for Original branch question")).toBeVisible();

    await page.getByRole("button", { name: "Edit and create a branch" }).click();

    await expect(page.getByText("Editing an earlier message", { exact: true })).toBeVisible();
    await expect(input).toHaveValue("Original branch question");
    await expect(
      page.getByText(
        "Sending creates a new conversation branch. The original history remains unchanged.",
      ),
    ).toBeVisible();
  });

  test("should display response after streaming completes", async ({ page }) => {
    await mockSuccessfulChatStream(page);
    // Send a question
    const chatInput = page.getByPlaceholder("Ask anything about your meetings...");
    await chatInput.fill("What is this meeting about?");
    await chatInput.press("Enter");

    // Wait for the user message to appear
    await expect(page.getByText("What is this meeting about?", { exact: true })).toBeVisible({
      timeout: 5_000,
    });

    // Wait for an agent response to appear (markdown body class)
    const agentResponse = page.locator(".markdown-body").first();
    await expect(agentResponse).toBeVisible({ timeout: 30_000 });
  });

  test("should maintain chat history in current session", async ({ page }) => {
    await mockSuccessfulChatStream(page);
    // Send first message
    const chatInput = page.getByPlaceholder("Ask anything about your meetings...");
    await chatInput.fill("First test question");
    await chatInput.press("Enter");

    // Wait for first user message
    await expect(page.getByText("First test question", { exact: true })).toBeVisible({
      timeout: 5_000,
    });

    // Wait for agent response
    await expect(page.locator(".markdown-body").first()).toBeVisible({
      timeout: 30_000,
    });

    // Send second message
    await chatInput.fill("Second test question");
    await chatInput.press("Enter");

    // Both user messages should be visible
    await expect(page.getByText("First test question", { exact: true })).toBeVisible();
    await expect(page.getByText("Second test question", { exact: true })).toBeVisible({
      timeout: 5_000,
    });

    // Session active tag should appear after first exchange
    await expect(page.getByText("Session Active")).toBeVisible();
  });

  test("should show meeting selector dropdown", async ({ page }) => {
    // The meeting selector should be present in the header area
    const meetingSelect = page.getByRole("combobox", {
      name: /select meetings to ask about/i,
    });
    await expect(meetingSelect).toBeVisible();
  });

  test("should show modes and filters button", async ({ page }) => {
    await expect(page.getByRole("button", { name: /modes & filters/i })).toBeVisible();
  });

  test("should expand modes and filters panel when clicked", async ({ page }) => {
    await page.getByRole("button", { name: /modes & filters/i }).click();

    // Conversation-level controls should become visible.
    await expect(page.getByText("Retrieval engine")).toBeVisible();
    await expect(page.getByText("RAG mode")).toBeVisible();
    await expect(page.getByText("Memory mode")).toBeVisible();
    await expect(page.getByText("Web search")).toBeVisible();
    await expect(page.getByText("File type filter")).toBeVisible();
    await expect(page.getByText("Date range")).toBeVisible();
  });

  test("should show keyboard shortcut hints", async ({ page }) => {
    await expect(page.getByText("Enter to send")).toBeVisible();
    await expect(page.getByText("Shift+Enter for new line")).toBeVisible();
  });

  test("should send selected file_ids and render citations from selected file", async ({
    page,
  }) => {
    await page.route("**/api/v1/meetings**", async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === "/api/v1/meetings") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            total: 1,
            meetings: [
              {
                id: 1,
                title: "Scope Meeting",
                description: null,
                file_type: "pdf",
                file_name: null,
                status: "ready",
                meeting_date: "2026-04-01",
                created_at: "2026-04-01T00:00:00Z",
                transcript_preview: null,
                file_url: null,
                error_message: null,
              },
            ],
          }),
        });
        return;
      }

      if (url.pathname === "/api/v1/meetings/1/files") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify([
            {
              id: 201,
              file_type: "pdf",
              file_name: "Unselected A.pdf",
              status: "ready",
              created_at: "2026-04-01T00:00:00Z",
              transcript_preview: null,
              error_message: null,
            },
            {
              id: 202,
              file_type: "pdf",
              file_name: "Selected B.pdf",
              status: "ready",
              created_at: "2026-04-01T00:00:00Z",
              transcript_preview: null,
              error_message: null,
            },
          ]),
        });
        return;
      }

      await route.continue();
    });

    await page.route("**/api/v1/chat/stream", async (route) => {
      const payload = route.request().postDataJSON() as {
        meeting_ids?: number[];
        file_ids?: number[];
      };
      expect(payload.meeting_ids).toEqual([1]);
      expect(payload.file_ids).toEqual([202]);

      const source = {
        meeting_id: 1,
        meeting_title: "Scope Meeting",
        content: "Selected file answer snippet",
        score: 0.98,
        file_id: 202,
        file_name: "Selected B.pdf",
        file_type: "pdf",
        chunk_index: 1,
        page_number: 2,
        timestamp_start: null,
        timestamp_end: null,
        speaker: null,
        source_kind: "page",
      };
      const sseBody = [
        `data: ${JSON.stringify({ type: "token", content: "Answer from selected file. [1]" })}`,
        `data: ${JSON.stringify({ type: "sources", items: [source] })}`,
        `data: ${JSON.stringify({ type: "done", session_id: "sess-scope" })}`,
        "",
      ].join("\n");
      await route.fulfill({
        status: 200,
        headers: {
          "Content-Type": "text/event-stream",
          "Cache-Control": "no-cache",
          Connection: "keep-alive",
        },
        body: sseBody,
      });
    });

    await page.reload();
    await expect(page.getByLabel("Send message")).toBeVisible();

    const meetingSelect = page.getByRole("combobox", {
      name: /select meetings to ask about/i,
    });
    await meetingSelect.click();
    await page.getByText("Scope Meeting", { exact: true }).click();

    const fileSelect = page.getByRole("combobox", { name: "Select files (optional)" });
    await fileSelect.click();
    await page.getByText("Selected B.pdf", { exact: true }).click();
    await page.keyboard.press("Escape");

    const chatInput = page.getByPlaceholder("Ask anything about your meetings...");
    await chatInput.fill("Question scoped to selected file");
    await chatInput.press("Enter");

    await expect(page.getByText("Answer from selected file.")).toBeVisible();
    await expect(page.getByLabel("Open source 1: Selected B.pdf")).toBeVisible();
    await expect(page.getByLabel(/Open source \d+: Unselected A\.pdf/)).toHaveCount(0);
  });
});
