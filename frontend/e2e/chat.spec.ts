import { test, expect } from "@playwright/test";

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

  test("should send a message via quick question button", async ({ page }) => {
    // Click a quick question button to send a message
    const quickButton = page.getByRole("button", {
      name: "What decisions were made?",
    });
    await quickButton.click();

    // The welcome state should disappear — messages area should render
    // The user message should appear in the chat
    await expect(page.getByText("What decisions were made?")).toBeVisible({
      timeout: 5_000,
    });
  });

  test("should show streaming indicator during response", async ({ page }) => {
    // Send a question to trigger streaming
    const chatInput = page.getByPlaceholder("Ask anything about your meetings...");
    await chatInput.fill("Test streaming question");
    await chatInput.press("Enter");

    // The streaming indicator (ThinkingDots or loading state on the send button)
    // should appear while waiting for the response.
    // The send button enters loading state during streaming
    // Either the button shows loading or thinking dots appear
    const streamingIndicator = page.locator(".ant-btn-loading, [class*='ant-spin']");
    await expect(streamingIndicator.first()).toBeVisible({ timeout: 5_000 });
  });

  test("should display response after streaming completes", async ({ page }) => {
    // Send a question
    const chatInput = page.getByPlaceholder("Ask anything about your meetings...");
    await chatInput.fill("What is this meeting about?");
    await chatInput.press("Enter");

    // Wait for the user message to appear
    await expect(page.getByText("What is this meeting about?")).toBeVisible({
      timeout: 5_000,
    });

    // Wait for an agent response to appear (markdown body class)
    const agentResponse = page.locator(".markdown-body").first();
    await expect(agentResponse).toBeVisible({ timeout: 30_000 });
  });

  test("should maintain chat history in current session", async ({ page }) => {
    // Send first message
    const chatInput = page.getByPlaceholder("Ask anything about your meetings...");
    await chatInput.fill("First test question");
    await chatInput.press("Enter");

    // Wait for first user message
    await expect(page.getByText("First test question")).toBeVisible({
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
    await expect(page.getByText("First test question")).toBeVisible();
    await expect(page.getByText("Second test question")).toBeVisible({
      timeout: 5_000,
    });

    // Session active tag should appear after first exchange
    await expect(page.getByText("Session Active")).toBeVisible();
  });

  test("should show meeting selector dropdown", async ({ page }) => {
    // The meeting selector should be present in the header area
    const meetingSelect = page.getByPlaceholder(/select meetings to ask about/i);
    await expect(meetingSelect).toBeVisible();
  });

  test("should show parameters button", async ({ page }) => {
    await expect(page.getByRole("button", { name: /parameters/i })).toBeVisible();
  });

  test("should expand parameters panel when clicked", async ({ page }) => {
    await page.getByRole("button", { name: /parameters/i }).click();

    // Parameter controls should become visible
    await expect(page.getByText("Top K retrievals")).toBeVisible();
    await expect(page.getByText("Web search")).toBeVisible();
    await expect(page.getByText("File type filter")).toBeVisible();
    await expect(page.getByText("Date range")).toBeVisible();
  });

  test("should show keyboard shortcut hints", async ({ page }) => {
    await expect(page.getByText("Enter to send")).toBeVisible();
    await expect(page.getByText("Shift+Enter for new line")).toBeVisible();
  });

  test("should send selected file_ids and render citations from selected file", async ({ page }) => {
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
        `data: ${JSON.stringify({ type: "token", content: "Answer from selected file." })}`,
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

    const meetingSelect = page.getByPlaceholder(/select meetings to ask about/i);
    await meetingSelect.click();
    await page.getByText("Scope Meeting", { exact: true }).click();

    const fileSelect = page.getByPlaceholder("Select files (optional)");
    await fileSelect.click();
    await page.getByText("Selected B.pdf", { exact: true }).click();

    const chatInput = page.getByPlaceholder("Ask anything about your meetings...");
    await chatInput.fill("Question scoped to selected file");
    await chatInput.press("Enter");

    await expect(page.getByText("Answer from selected file.")).toBeVisible();
    await expect(page.getByText("Selected B.pdf")).toBeVisible();
    await expect(page.getByText("Unselected A.pdf")).not.toBeVisible();
  });
});
