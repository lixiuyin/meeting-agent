import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { IntlProvider } from "react-intl";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter, useLocation } from "react-router-dom";

import type { MemoryItem } from "../../api/client";
import en from "../../i18n/locales/en";
import MemoryList from "./MemoryList";

const { openViewer } = vi.hoisted(() => ({ openViewer: vi.fn() }));
vi.mock("../../contexts/ViewerContext", () => ({ useViewer: () => ({ openViewer }) }));

vi.mock("react-virtuoso", () => ({
  Virtuoso: ({
    data,
    itemContent,
  }: {
    data: MemoryItem[];
    itemContent: (index: number, item: MemoryItem) => ReactNode;
  }) => (
    <div>
      {data.map((item, index) => (
        <div key={item.key}>{itemContent(index, item)}</div>
      ))}
    </div>
  ),
}));

const memory: MemoryItem = {
  revision: 1,
  key: "project_owner",
  value: "Alice owns the launch plan.",
  source: "auto_extracted",
  importance: 4,
  salience: 4,
  confidence: 0.9,
  freshness_score: 1,
  usefulness_score: 0.75,
  usefulness_count: 4,
  category: "project",
  access_count: 2,
  updated_at: "2026-09-04T12:00:00Z",
  evidence_excerpt: "Alice confirmed she owns the launch plan.",
  fact_type: "project_fact",
  assertion_status: "confirmed",
};

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{`${location.pathname}${location.search}`}</output>;
}

function renderList(onFeedback = vi.fn(), item: MemoryItem = memory) {
  render(
    <IntlProvider locale="en" messages={en}>
      <MemoryRouter>
        <MemoryList
          displayMemories={[item]}
          loading={false}
          loadingMore={false}
          hasMore={false}
          total={1}
          onLoadMore={vi.fn()}
          search=""
          onSearchChange={vi.fn()}
          factTypeFilter={undefined}
          onFactTypeFilterChange={vi.fn()}
          statusFilter={undefined}
          onStatusFilterChange={vi.fn()}
          onSemanticSearch={vi.fn()}
          searching={false}
          semanticResults={null}
          onClearSemantic={vi.fn()}
          onRefresh={vi.fn()}
          onCreateOpen={vi.fn()}
          onImportOpen={vi.fn()}
          onExport={vi.fn()}
          onDecay={vi.fn()}
          decaying={false}
          activeAction={null}
          feedbackKey={null}
          onFeedback={onFeedback}
          onStatusChange={vi.fn()}
          onEdit={vi.fn()}
          onDelete={vi.fn()}
          editMemory={null}
          onEditClose={vi.fn()}
          onEditSubmit={vi.fn()}
          createOpen={false}
          onCreateClose={vi.fn()}
          onCreateSubmit={vi.fn()}
          importOpen={false}
          onImportClose={vi.fn()}
          importText=""
          onImportTextChange={vi.fn()}
          onImportSubmit={vi.fn()}
          isSelectionMode={false}
          selectedKeys={new Set()}
          selectedCount={0}
          onToggleSelectionMode={vi.fn()}
          onToggleSelect={vi.fn()}
          onSelectAll={vi.fn()}
          onClearSelection={vi.fn()}
          onBatchDelete={vi.fn()}
        />
        <LocationProbe />
      </MemoryRouter>
    </IntlProvider>,
  );
  return onFeedback;
}

// Query the explicit accessible label and validate the actual visible button.
// This avoids scanning every Ant Design control's computed accessible name in
// jsdom; assertions, click handlers and the default timeout remain unchanged.
function visibleButton(name: string | RegExp) {
  const button = screen.getByLabelText(name, { selector: "button" });
  expect(button).toBeVisible();
  return button;
}

describe("MemoryList feedback", () => {
  it("preserves exact evidence when opening the primary source material button", () => {
    renderList(vi.fn(), {
      ...memory,
      meeting_ids: [99, 7],
      file_ids: [100, 9],
      evidence_refs: [
        { meeting_id: 7, file_id: 9, source_revision: "rev-1", window_start: 10, window_end: 42 },
      ],
    });
    fireEvent.click(visibleButton("Open source material"));
    expect(openViewer).toHaveBeenLastCalledWith(
      expect.objectContaining({
        meetingId: 7,
        fileId: 9,
        sourceRevision: "rev-1",
        windowStart: 10,
        windowEnd: 42,
        evidenceExcerpt: memory.evidence_excerpt,
      }),
    );
    expect(screen.getByTestId("location")).toHaveTextContent("/");
  });

  it("does not invent a meeting/file pair from ambiguous scope arrays", () => {
    renderList(vi.fn(), {
      ...memory,
      meeting_ids: [7, 8],
      file_ids: [9, 10],
      evidence_refs: [{ file_id: 9 }],
    });
    expect(visibleButton("Open source material")).toBeDisabled();
    expect(visibleButton("Open exact evidence source")).toBeDisabled();
  });

  it("offers each source separately when a memory has multiple references", async () => {
    renderList(vi.fn(), {
      ...memory,
      meeting_ids: [7, 8],
      file_ids: [9, 10],
      evidence_refs: [
        { meeting_id: 7, file_id: 9 },
        { meeting_id: 8, file_id: 10 },
      ],
    });
    fireEvent.click(visibleButton("Open source material"));
    fireEvent.click(await screen.findByText("Source 2 · File 10"));
    expect(openViewer).toHaveBeenLastCalledWith(
      expect.objectContaining({ meetingId: 8, fileId: 10 }),
    );
  });

  it("shows usefulness and provenance and records positive feedback", () => {
    const onFeedback = renderList();

    expect(screen.getByText(/75% useful from 4 ratings/)).toBeInTheDocument();
    expect(screen.getByText(/Alice confirmed she owns/)).toBeInTheDocument();
    fireEvent.click(visibleButton("This memory was useful"));

    expect(onFeedback).toHaveBeenCalledWith("project_owner", true);
  });

  it("records negative feedback explicitly", () => {
    const onFeedback = renderList();

    fireEvent.click(visibleButton("This memory was not useful"));

    expect(onFeedback).toHaveBeenCalledWith("project_owner", false);
  });

  it("links scoped evidence to the exact meeting and file", () => {
    renderList(vi.fn(), { ...memory, session_id: null, meeting_ids: [7], file_ids: [9] });

    fireEvent.click(visibleButton(/Open source material/));

    expect(openViewer).toHaveBeenLastCalledWith(
      expect.objectContaining({ meetingId: 7, fileId: 9 }),
    );
  });

  it("links each evidence reference and shows its business-time validity", () => {
    renderList(vi.fn(), {
      ...memory,
      meeting_ids: [7],
      valid_from: "2026-09-01T00:00:00Z",
      valid_to: "2026-09-30T00:00:00Z",
      evidence_refs: [
        {
          meeting_id: 7,
          file_id: 9,
          window_start: 10,
          window_end: 42,
          source_revision: "abc",
          page_number: 3,
          timestamp_start: 12.5,
          timestamp_end: 18,
          chunk_index: 4,
        },
      ],
    });

    expect(screen.getByText(/Effective from/)).toBeInTheDocument();
    fireEvent.click(visibleButton("Open exact evidence source"));
    expect(openViewer).toHaveBeenLastCalledWith(
      expect.objectContaining({
        meetingId: 7,
        fileId: 9,
        sourceRevision: "abc",
        page: 3,
        seekTo: 12.5,
        seekEnd: 18,
        chunkIndex: 4,
        windowStart: 10,
        windowEnd: 42,
      }),
    );
  });
});
