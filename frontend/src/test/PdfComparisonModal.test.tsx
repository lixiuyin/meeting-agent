import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { useEffect, type ReactNode } from "react";
import PdfComparisonModal from "../components/materials/file-views/PdfComparisonModal";
import type { PageItem } from "../components/materials/file-views/types";

vi.mock("../hooks/useMeetingFileUrl", () => ({
  useMeetingFileUrl: vi.fn(() => "/api/v1/meetings/1/files/1"),
}));

vi.mock("react-virtualized-auto-sizer", () => ({
  AutoSizer: ({
    renderProp,
  }: {
    renderProp: (size: { width?: number; height?: number }) => ReactNode;
  }) => <>{renderProp({ width: 900, height: 600 })}</>,
}));

vi.mock("react-pdf", () => {
  const Document = ({
    children,
    onLoadSuccess,
  }: {
    children: ReactNode;
    onLoadSuccess?: (doc: { numPages: number }) => void;
  }) => {
    useEffect(() => {
      onLoadSuccess?.({ numPages: 3 });
    }, [onLoadSuccess]);
    return <div data-testid="pdf-document">{children}</div>;
  };

  const Page = ({ pageNumber }: { pageNumber: number }) => (
    <div data-testid={`pdf-page-${pageNumber}`}>PDF Page {pageNumber}</div>
  );

  return {
    Document,
    Page,
    pdfjs: { GlobalWorkerOptions: { workerSrc: "" } },
  };
});

function pane(source: "pdf" | "parsed") {
  return document.querySelector<HTMLDivElement>(`[data-pdf-pane="${source}"]`)!;
}

const pages: PageItem[] = [
  { page_num: 1, heading: "## Intro", text: "first page", image_assets: [] },
  { page_num: 2, heading: "## Details", text: "second page", image_assets: [] },
];

describe("PdfComparisonModal", () => {
  beforeEach(() => {
    vi.spyOn(Element.prototype, "clientWidth", "get").mockReturnValue(900);
    vi.spyOn(Element.prototype, "clientHeight", "get").mockImplementation(function (
      this: HTMLElement,
    ) {
      return this.dataset.pdfPane ? 600 : 0;
    });
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (
      this: HTMLElement,
    ) {
      const container = this.closest<HTMLElement>("[data-pdf-pane]");
      const height = container?.dataset.pdfPane === "pdf" ? 1000 : 2000;
      const top = this.dataset.pageNum
        ? (Number(this.dataset.pageNum) - 1) * height - (container?.scrollTop ?? 0)
        : 0;
      return new DOMRect(0, top, 800, this.dataset.pageNum ? height : 600);
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("renders modal with parsed pages", async () => {
    render(
      <PdfComparisonModal
        open
        meetingId={1}
        fileId={1}
        fileName="slides.pdf"
        pages={pages}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText("PDF Comparison — slides.pdf")).toBeInTheDocument();
    expect(screen.getByText("Parsed Content (2 pages)")).toBeInTheDocument();
    expect(screen.getByText("second page")).toBeInTheDocument();
    await screen.findByTestId("pdf-page-1");
    expect(pane("pdf").querySelector('[data-page-num="3"]')).toBeInTheDocument();
    expect(screen.queryByTestId("pdf-page-3")).not.toBeInTheDocument();
  });

  it("aligns both panes only when the explicit page button is clicked", async () => {
    render(
      <PdfComparisonModal
        open
        meetingId={1}
        fileId={1}
        fileName="slides.pdf"
        pages={pages}
        onClose={vi.fn()}
      />,
    );

    await screen.findByTestId("pdf-page-1");
    fireEvent.click(screen.getByRole("button", { name: "Go to page 2" }));
    expect(pane("pdf").scrollTop).toBe(1000);
    expect(pane("parsed").scrollTop).toBe(2000);
    fireEvent.click(screen.getByText("first page"));
    expect(pane("pdf").scrollTop).toBe(1000);
    expect(pane("parsed").scrollTop).toBe(2000);
  });

  it("uses the top reading anchor even when a parsed page is taller than the viewport", async () => {
    render(
      <PdfComparisonModal
        open
        meetingId={1}
        fileId={1}
        fileName="slides.pdf"
        pages={pages}
        onClose={vi.fn()}
      />,
    );

    await screen.findByTestId("pdf-page-1");
    act(() => {
      fireEvent.wheel(pane("parsed"));
      pane("parsed").scrollTop = 2500;
      fireEvent.scroll(pane("parsed"));
    });

    await waitFor(() => {
      expect(screen.getByText("Page 2 / 3")).toBeInTheDocument();
      expect(pane("pdf").scrollTop).toBe(1250);
      expect(pane("parsed").scrollTop).toBe(2500);
    });
  });

  it("ignores follower echoes and immediately permits the other pane to take control", async () => {
    render(
      <PdfComparisonModal
        open
        meetingId={1}
        fileId={1}
        fileName="slides.pdf"
        pages={pages}
        onClose={vi.fn()}
      />,
    );

    await screen.findByTestId("pdf-page-1");
    fireEvent.click(screen.getByRole("button", { name: "Go to page 2" }));
    act(() => {
      fireEvent.scroll(pane("pdf"));
      fireEvent.scroll(pane("parsed"));
    });
    expect(screen.getByText("Page 2 / 3")).toBeInTheDocument();
    act(() => {
      fireEvent.wheel(pane("pdf"));
      pane("pdf").scrollTop = 1600;
      fireEvent.scroll(pane("pdf"));
    });
    await waitFor(() => expect(pane("parsed").scrollTop).toBe(3200));
    fireEvent.scroll(pane("parsed"));
    expect(pane("pdf").scrollTop).toBe(1600);
  });

  it("calls onClose when modal close button is clicked", async () => {
    const onClose = vi.fn();
    render(
      <PdfComparisonModal
        open
        meetingId={1}
        fileId={1}
        fileName="slides.pdf"
        pages={pages}
        onClose={onClose}
      />,
    );

    await screen.findByTestId("pdf-page-1");
    fireEvent.click(screen.getAllByLabelText("Close")[0]);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
