import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { useEffect, type ReactNode } from "react";
import PdfComparisonModal from "../components/materials/file-views/PdfComparisonModal";
import type { PageItem } from "../components/materials/file-views/types";

vi.mock("../api/client", () => ({
  getMeetingFileUrl: vi.fn(() => "/api/v1/meetings/1/files/1"),
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

type ObserverCallback = (
  entries: IntersectionObserverEntry[],
  observer: IntersectionObserver,
) => void;

class MockIntersectionObserver {
  static instances: MockIntersectionObserver[] = [];

  callback: ObserverCallback;
  root: Element | null;
  observed = new Set<Element>();
  disconnect = vi.fn();
  unobserve = vi.fn((target: Element) => this.observed.delete(target));
  takeRecords = vi.fn(() => []);

  constructor(callback: ObserverCallback, options?: IntersectionObserverInit) {
    this.callback = callback;
    this.root = (options?.root as Element | null) ?? null;
    MockIntersectionObserver.instances.push(this);
  }

  observe = vi.fn((target: Element) => {
    this.observed.add(target);
  });
}

function triggerIntersection(target: Element, ratio: number) {
  const instance = MockIntersectionObserver.instances.find((item) => item.observed.has(target));
  if (!instance) {
    throw new Error("No observer found for target");
  }
  const entry = {
    target,
    intersectionRatio: ratio,
    isIntersecting: ratio > 0,
    boundingClientRect: target.getBoundingClientRect(),
    intersectionRect: target.getBoundingClientRect(),
    rootBounds: null,
    time: Date.now(),
  } as IntersectionObserverEntry;
  instance.callback([entry], instance as unknown as IntersectionObserver);
}

const pages: PageItem[] = [
  { page_num: 1, heading: "## Intro", text: "first page", image_assets: [] },
  { page_num: 2, heading: "## Details", text: "second page", image_assets: [] },
];

describe("PdfComparisonModal", () => {
  beforeEach(() => {
    MockIntersectionObserver.instances = [];
    window.IntersectionObserver =
      MockIntersectionObserver as unknown as typeof IntersectionObserver;
  });

  afterEach(() => {
    vi.clearAllMocks();
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
    await screen.findByTestId("pdf-page-3");
  });

  it("scrolls PDF pane when clicking parsed page card", async () => {
    const scrollSpy = vi.spyOn(Element.prototype, "scrollIntoView");
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
    fireEvent.click(screen.getAllByText("Page 2")[0]);

    expect(scrollSpy).toHaveBeenCalled();
  });

  it("updates current page from parsed-pane intersection", async () => {
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
    const parsedPageTwo = Array.from(document.querySelectorAll("[data-page-num='2']")).find((el) =>
      el.textContent?.includes("second page"),
    );
    expect(parsedPageTwo).toBeTruthy();
    act(() => {
      triggerIntersection(parsedPageTwo!, 0.9);
    });

    await waitFor(() => {
      expect(screen.getByText("Page 2 / 3")).toBeInTheDocument();
    });
  });

  it("prevents feedback loop while sync guard is active", async () => {
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

    await screen.findByTestId("pdf-page-3");
    fireEvent.click(screen.getAllByText("Page 2")[0]);

    const pdfPageThree = Array.from(document.querySelectorAll("[data-page-num='3']")).find((el) =>
      el.textContent?.includes("PDF Page 3"),
    );
    expect(pdfPageThree).toBeTruthy();
    act(() => {
      triggerIntersection(pdfPageThree!, 0.95);
    });

    expect(screen.getByText("Page 2 / 3")).toBeInTheDocument();
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
