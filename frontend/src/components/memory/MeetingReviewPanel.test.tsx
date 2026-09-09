import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { IntlProvider } from "react-intl";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { MemoryItem } from "../../api/client-memory";
import en from "../../i18n/locales/en";
import MeetingReviewPanel from "./MeetingReviewPanel";

const { apiPost, openEvidence } = vi.hoisted(() => ({
  apiPost: vi.fn(),
  openEvidence: vi.fn(),
}));

vi.mock("../../api/client-core", () => ({
  api: { post: apiPost },
  formatApiErrorMessage: (error: unknown) => String(error),
  isRequestCanceled: () => false,
}));

vi.mock("../../api/client-memory", () => ({
  updateMemoryStatus: vi.fn(),
  resolveMemoryConflict: vi.fn(),
}));

vi.mock("../../hooks/useEvidenceViewer", () => ({
  useEvidenceViewer: () => openEvidence,
}));

const fact: MemoryItem = {
  key: "project.release.owner",
  value: "Alice owns the release plan.",
  source: "auto_extracted",
  fact_type: "project_fact",
  assertion_status: "confirmed",
  revision: 1,
  importance: 3,
  salience: 3,
  confidence: 0.9,
  freshness_score: 1,
  usefulness_score: 0,
  usefulness_count: 0,
  access_count: 0,
  updated_at: "2026-09-08T00:00:00Z",
  evidence_excerpt: "Alice owns the release plan.",
  meeting_ids: [7],
  file_ids: [9, 10],
  evidence_refs: [
    { meeting_id: 7, file_id: 9, page_number: 2 },
    { meeting_id: 7, file_id: 10, page_number: 4 },
  ],
};

function renderPanel(item: MemoryItem = fact) {
  apiPost.mockResolvedValue({
    data: {
      items: [item],
      conflicts: {},
      total: 1,
      next_offset: null,
      snapshot: "review-snapshot-1",
      extraction_progress: {},
    },
  });
  return render(
    <IntlProvider locale="en" messages={en}>
      <MeetingReviewPanel />
    </IntlProvider>,
  );
}

describe("MeetingReviewPanel", () => {
  beforeEach(() => {
    apiPost.mockReset();
    openEvidence.mockReset();
  });

  it("labels auto-recorded facts honestly and avoids repeating identical evidence", async () => {
    renderPanel();

    expect(await screen.findByText("Auto-recorded · awaiting human review")).toBeVisible();
    expect(screen.getByText("1 fact awaiting review")).toBeVisible();
    expect(screen.getAllByText("Alice owns the release plan.")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Mark as reviewed" })).toBeVisible();
  });

  it("collapses multiple evidence references into one labelled source menu", async () => {
    renderPanel();

    const sources = await screen.findByRole("button", { name: "Evidence sources (2)" });
    expect(screen.queryByText("Source 1 · File 9")).not.toBeInTheDocument();
    fireEvent.click(sources);
    fireEvent.click(await screen.findByText("Source 2 · File 10"));

    await waitFor(() =>
      expect(openEvidence).toHaveBeenCalledWith(
        fact.evidence_refs?.[1],
        fact.meeting_ids,
        fact.evidence_excerpt,
      ),
    );
  });

  it("keeps a distinct source excerpt visible", async () => {
    renderPanel({
      ...fact,
      value: "Alice owns the release plan.",
      evidence_excerpt: "Alice said: I will own the release plan.",
      evidence_refs: [{ meeting_id: 7, file_id: 9 }],
    });

    expect(await screen.findByText("Source evidence")).toBeVisible();
    expect(screen.getByText("Alice said: I will own the release plan.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Source 1 · File 9" })).toBeVisible();
  });

  it("explains facts held out by reference-only source policy", async () => {
    apiPost.mockResolvedValue({
      data: {
        items: [],
        conflicts: {},
        total: 0,
        next_offset: null,
        snapshot: "review-held",
        extraction_progress: { reference: 2, held_for_source_review: 7 },
      },
    });
    render(
      <IntlProvider locale="en" messages={en}>
        <MeetingReviewPanel />
      </IntlProvider>,
    );

    expect(await screen.findByText("Reference-only: 2 files")).toBeVisible();
    expect(screen.getByText("Held until source classification changes: 7 facts")).toBeVisible();
  });

  it("renders safe markdown tables and math in review content", async () => {
    renderPanel({
      ...fact,
      value: "| Metric | Value |\n| --- | --- |\n| Loss | $x^2$ |",
    });

    expect(await screen.findByRole("table")).toBeVisible();
    expect(document.querySelector(".meeting-review-value .katex")).not.toBeNull();
    expect(document.querySelector(".meeting-review-value img")).toBeNull();
  });

  it("carries the server snapshot when requesting the next review page", async () => {
    apiPost.mockResolvedValue({
      data: {
        items: [fact],
        conflicts: {},
        total: 26,
        next_offset: 25,
        snapshot: "review-snapshot-next",
        extraction_progress: {},
      },
    });
    render(
      <IntlProvider locale="en" messages={en}>
        <MeetingReviewPanel />
      </IntlProvider>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Next" }));

    await waitFor(() =>
      expect(apiPost).toHaveBeenLastCalledWith(
        "/memory/review/query",
        expect.objectContaining({ offset: 25, snapshot: "review-snapshot-next" }),
        expect.any(Object),
      ),
    );
  });
});
