import { fireEvent, render, screen } from "@testing-library/react";
import { IntlProvider } from "react-intl";
import { describe, expect, it, vi } from "vitest";
import { MeetingFilesSection } from "./MeetingFilesSection";
import en from "../../../i18n/locales/en";

const noop = vi.fn();

function renderFile(
  summaryStatus: "pending" | "generating",
  options: {
    evidenceSyncStatus?: "syncing" | "failed";
    onOpenSemanticHistory?: typeof noop;
  } = {},
) {
  return render(
    <IntlProvider
      locale="en"
      messages={{
        ...en,
        "memory.list.batchDeleteTitle": "Delete {count} memories?",
        "memory.list.batchDeleteDesc": "Delete selected memories.",
      }}
    >
      <MeetingFilesSection
        meetingId={1}
        files={[
          {
            id: 10,
            file_name: "notes.txt",
            file_type: "txt",
            status: "ready",
            summary_status: summaryStatus,
            evidence_sync_status: options.evidenceSyncStatus,
            evidence_sync_error:
              options.evidenceSyncStatus === "failed" ? "index backend unavailable" : undefined,
          },
        ]}
        detailLoading={false}
        expandedSummaryFileId={null}
        onSetExpandedSummaryFileId={noop}
        onViewTimestamps={noop}
        onOpenSpeakers={noop}
        onReprocessFile={noop}
        onDownloadFile={noop}
        onDeleteFile={noop}
        onOpenDocumentViewer={noop}
        onDownloadFileMarkdown={noop}
        onUpdateFileSemantics={noop}
        onOpenSemanticHistory={options.onOpenSemanticHistory ?? noop}
      />
    </IntlProvider>,
  );
}

describe("MeetingFilesSection summary state", () => {
  it("does not label a ready file with a pending optional summary as active work", () => {
    renderFile("pending");

    expect(screen.getByText("Not summarized")).toBeInTheDocument();
    expect(screen.queryByText("Summarizing…")).not.toBeInTheDocument();
  });

  it("shows active summary generation explicitly", () => {
    renderFile("generating");

    expect(screen.getByText("Summarizing…")).toBeInTheDocument();
  });

  it("surfaces evidence index synchronization state", () => {
    renderFile("pending", { evidenceSyncStatus: "syncing" });

    expect(screen.getByText("Updating searchable evidence")).toBeInTheDocument();
  });

  it("opens the immutable evidence review history", () => {
    const onOpenSemanticHistory = vi.fn();
    renderFile("pending", { evidenceSyncStatus: "failed", onOpenSemanticHistory });

    expect(screen.getByText("Evidence update needs attention")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Review evidence history"));
    expect(onOpenSemanticHistory).toHaveBeenCalledWith(expect.objectContaining({ id: 10 }));
  });
});
