import { useRef, useEffect, useState, useCallback, useMemo } from "react";
import { Button, Spin, Tooltip, Segmented, Alert, Switch, Space } from "antd";
import { useIntl } from "react-intl";
import { ZoomInOutlined, ZoomOutOutlined, FilePdfOutlined } from "@ant-design/icons";
import { Document } from "react-pdf";
import type { PDFDocumentProxy } from "pdfjs-dist";
import VirtualPdfPage from "./VirtualPdfPage";
import { usePdfPageDimensions } from "./usePdfPageDimensions";
import "../../../pdf-worker";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";
import { AutoSizer } from "react-virtualized-auto-sizer";
import { getFileTimeline } from "../../../api/client";
import { reportNonCriticalError } from "../../../utils/monitoring";
import usePdfPageSync from "./usePdfPageSync";
import PageLayoutView from "./PageLayoutView";
import type { PageItem } from "./types";

const MOBILE_BREAKPOINT = 768;
const ZOOM_STEP = 0.25;
const MIN_ZOOM = 0.5;
const MAX_ZOOM = 3.0;

export default function PdfSplitViewer({
  url,
  page,
  meetingId,
  fileId,
  evidenceExcerpt,
}: {
  url: string;
  page?: number;
  meetingId: number;
  fileId: number;
  evidenceExcerpt?: string;
}) {
  return (
    <PdfSplitViewerInner
      key={`${meetingId}:${fileId}:${page ?? 1}`}
      url={url}
      page={page}
      meetingId={meetingId}
      fileId={fileId}
      evidenceExcerpt={evidenceExcerpt}
    />
  );
}

function PdfSplitViewerInner({
  url,
  page,
  meetingId,
  fileId,
  evidenceExcerpt,
}: {
  url: string;
  page?: number;
  meetingId: number;
  fileId: number;
  evidenceExcerpt?: string;
}) {
  const { formatMessage: t } = useIntl();
  const [syncEnabled, setSyncEnabled] = useState(true);
  const [numPages, setNumPages] = useState(0);
  const { ratios, loadDimensions } = usePdfPageDimensions();
  const [zoom, setZoom] = useState(1.0);
  const [pdfError, setPdfError] = useState<string | null>(null);
  const [parsedPages, setParsedPages] = useState<PageItem[]>([]);
  const [loadingParsed, setLoadingParsed] = useState(true);
  const [mobilePane, setMobilePane] = useState<"PDF" | "Parsed">("PDF");
  const [isMobile, setIsMobile] = useState(false);

  const safePage = typeof page === "number" && page > 0 ? page : 1;

  const sortedPages = useMemo(
    () => [...parsedPages].sort((a, b) => a.page_num - b.page_num),
    [parsedPages],
  );

  const sync = usePdfPageSync({
    totalPages: Math.max(numPages, sortedPages.length),
    evidenceExcerpt,
    enabled: syncEnabled,
  });

  // Mobile breakpoint detection
  useEffect(() => {
    let rafId: number;
    const check = () => {
      cancelAnimationFrame(rafId);
      rafId = requestAnimationFrame(() => setIsMobile(window.innerWidth < MOBILE_BREAKPOINT));
    };
    check();
    window.addEventListener("resize", check);
    return () => {
      window.removeEventListener("resize", check);
      cancelAnimationFrame(rafId);
    };
  }, []);

  const [timelineError, setTimelineError] = useState<string | null>(null);

  // Fetch parsed pages from timeline API
  useEffect(() => {
    let cancelled = false;
    getFileTimeline(meetingId, fileId)
      .then((res) => {
        if (cancelled) return;
        if (res.data?.kind === "pages") {
          setParsedPages(res.data.pages ?? []);
        } else {
          setTimelineError("Timeline extraction failed for this document");
          reportNonCriticalError(
            "PdfSplitViewer timeline",
            new Error(`Unexpected timeline response kind: ${res.data?.kind}`),
          );
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setParsedPages([]);
          setTimelineError("Failed to load parsed pages");
          reportNonCriticalError("Failed to load parsed pages for PDF viewer", err);
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingParsed(false);
      });
    return () => {
      cancelled = true;
    };
  }, [meetingId, fileId]);

  // Sync initial page from prop
  const initialSyncDone = useRef(false);
  useEffect(() => {
    if (initialSyncDone.current) return;
    if (safePage <= 0) return;
    if (numPages === 0) return;
    const targetPage = Math.min(safePage, numPages);
    initialSyncDone.current = true;
    // The sync controller retains this anchor as either pane finishes loading.
    sync.scrollBothToPage(targetPage, Boolean(evidenceExcerpt));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [safePage, sync.scrollBothToPage, numPages, evidenceExcerpt]);

  const onDocumentLoadSuccess = useCallback(
    (document: PDFDocumentProxy) => {
      setNumPages(document.numPages);
      setPdfError(null);
      void loadDimensions(document);
    },
    [loadDimensions],
  );

  const onDocumentLoadError = useCallback((error: Error) => {
    setPdfError(error.message || "Failed to load PDF");
  }, []);

  const zoomIn = useCallback(() => setZoom((z) => Math.min(z + ZOOM_STEP, MAX_ZOOM)), []);
  const zoomOut = useCallback(() => setZoom((z) => Math.max(z - ZOOM_STEP, MIN_ZOOM)), []);

  const handlePageClick = useCallback(
    (pageNum: number) => {
      sync.scrollToPage("parsed", pageNum);
    },
    [sync],
  );

  const pageNumbers = useMemo(() => Array.from({ length: numPages }, (_, i) => i + 1), [numPages]);

  // --- Left pane: PDF ---
  const renderPdfPane = () => (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        overflow: "hidden",
        border: "1px solid var(--color-border)",
        borderRadius: 8,
        background: "var(--color-bg-elevated)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "8px 12px",
          borderBottom: "1px solid var(--color-border)",
          background: "var(--color-bg-muted)",
          flexShrink: 0,
        }}
      >
        <span style={{ fontSize: 13, fontWeight: 600 }}>
          <FilePdfOutlined style={{ marginRight: 6 }} />
          Page {sync.currentPage} / {numPages || "—"}
        </span>
        <div style={{ display: "flex", gap: 4 }}>
          <Tooltip title="Zoom out">
            <Button
              size="small"
              icon={<ZoomOutOutlined />}
              aria-label="Zoom out"
              onClick={zoomOut}
              disabled={zoom <= MIN_ZOOM}
            />
          </Tooltip>
          <span style={{ fontSize: 12, lineHeight: "24px", minWidth: 40, textAlign: "center" }}>
            {Math.round(zoom * 100)}%
          </span>
          <Tooltip title="Zoom in">
            <Button
              size="small"
              icon={<ZoomInOutlined />}
              aria-label="Zoom in"
              onClick={zoomIn}
              disabled={zoom >= MAX_ZOOM}
            />
          </Tooltip>
        </div>
      </div>

      {pdfError ? (
        <div style={{ padding: 24 }}>
          <Alert type="error" description={pdfError} showIcon />
        </div>
      ) : (
        <div ref={sync.pdfContainerRef} style={{ flex: 1, overflow: "auto" }}>
          <AutoSizer
            style={{ width: "100%", height: "100%" }}
            renderProp={({ width }: { width?: number }) => {
              if (!width) return null;
              const scaledWidth = Math.floor(width * zoom);
              return (
                <div style={{ paddingBottom: "var(--viewer-tail-space, 0px)" }}>
                  <Document
                    file={url}
                    onLoadSuccess={onDocumentLoadSuccess}
                    onLoadError={onDocumentLoadError}
                    loading={
                      <div style={{ textAlign: "center", padding: 40 }}>
                        <Spin />
                      </div>
                    }
                  >
                    {pageNumbers.map((pageNum) => (
                      <VirtualPdfPage
                        key={pageNum}
                        number={pageNum}
                        width={scaledWidth}
                        ratio={ratios[pageNum]}
                        activePage={sync.currentPage}
                      />
                    ))}
                  </Document>
                </div>
              );
            }}
          />
        </div>
      )}
    </div>
  );

  // --- Right pane: Parsed content ---
  const renderParsedPane = () => (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        overflow: "hidden",
        border: "1px solid var(--color-border)",
        borderRadius: 8,
        background: "var(--color-bg-elevated)",
      }}
    >
      <div
        style={{
          padding: "8px 12px",
          borderBottom: "1px solid var(--color-border)",
          background: "var(--color-bg-muted)",
          flexShrink: 0,
          fontSize: 13,
          fontWeight: 600,
        }}
      >
        Parsed Content ({sortedPages.length} pages)
      </div>

      <div ref={sync.parsedContainerRef} style={{ flex: 1, minHeight: 0, overflow: "auto" }}>
        {loadingParsed ? (
          <div style={{ textAlign: "center", padding: 40 }}>
            <Spin />
          </div>
        ) : timelineError ? (
          <Alert
            type="warning"
            message={timelineError}
            showIcon
            action={
              <Button
                size="small"
                onClick={() => {
                  const ctrl = new AbortController();
                  setTimelineError(null);
                  setLoadingParsed(true);
                  getFileTimeline(meetingId, fileId, { signal: ctrl.signal })
                    .then((res) => {
                      if (ctrl.signal.aborted) return;
                      if (res.data?.kind === "pages") {
                        setParsedPages(res.data.pages ?? []);
                      } else {
                        setTimelineError("Timeline extraction failed for this document");
                      }
                    })
                    .catch((err) => {
                      if (ctrl.signal.aborted) return;
                      if (err.name !== "CanceledError" && err.name !== "AbortError") {
                        setTimelineError("Failed to load parsed pages");
                      }
                    })
                    .finally(() => {
                      if (!ctrl.signal.aborted) setLoadingParsed(false);
                    });
                }}
              >
                Retry
              </Button>
            }
          />
        ) : sortedPages.length === 0 ? (
          <div style={{ padding: 24, color: "var(--color-text-muted)", textAlign: "center" }}>
            No parsed content available
          </div>
        ) : (
          <div style={{ padding: "12px 12px var(--viewer-tail-space, 0px)" }}>
            {sortedPages.map((p) => {
              const isActive = p.page_num === sync.currentPage;
              return (
                <div
                  key={p.page_num}
                  data-page-num={p.page_num}
                  style={{
                    marginBottom: 12,
                    borderLeft: isActive
                      ? "3px solid var(--color-primary)"
                      : "3px solid transparent",
                    paddingLeft: 12,
                    borderRadius: 4,
                    transition: "border-left-color 0.2s",
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                    <button
                      type="button"
                      aria-label={`Go to page ${p.page_num}`}
                      onClick={() => handlePageClick(p.page_num)}
                      style={{
                        border: 0,
                        cursor: "pointer",
                        fontSize: 11,
                        fontWeight: 700,
                        color: "var(--color-primary)",
                        background: "var(--color-bg-muted)",
                        padding: "2px 8px",
                        borderRadius: 10,
                      }}
                    >
                      Page {p.page_num}
                    </button>
                    {p.heading && (
                      <span
                        style={{
                          fontSize: 13,
                          fontWeight: 600,
                          color: "var(--color-text-primary)",
                        }}
                      >
                        {p.heading.replace(/^#{1,6}\s*/, "")}
                      </span>
                    )}
                  </div>
                  <PageLayoutView
                    pageNum={p.page_num}
                    heading={p.heading}
                    text={p.text}
                    imageAssets={p.image_assets ?? []}
                    label="Page"
                    variant="modal"
                    evidenceExcerpt={p.page_num === safePage ? evidenceExcerpt : undefined}
                  />
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <Space wrap style={{ padding: "8px 12px", flexShrink: 0 }}>
        <Switch
          checked={syncEnabled}
          onChange={setSyncEnabled}
          aria-label={t({ id: "viewer.syncEnabled" })}
        />
        <span>{t({ id: "viewer.syncEnabled" })}</span>
        <Button onClick={() => sync.scrollBothToPage(safePage, Boolean(evidenceExcerpt))}>
          {t({ id: "viewer.returnCitation" })}
        </Button>
        <span style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>
          {t({ id: "viewer.syncPrecision" })}
        </span>
      </Space>
      {isMobile && (
        <div
          style={{
            padding: "8px 16px",
            borderBottom: "1px solid var(--color-border)",
            flexShrink: 0,
          }}
        >
          <Segmented
            options={["PDF", "Parsed"]}
            value={mobilePane}
            onChange={(v) => setMobilePane(v as "PDF" | "Parsed")}
            block
          />
        </div>
      )}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: isMobile ? "1fr" : "1fr 1fr",
          gap: 12,
          flex: 1,
          minHeight: 0,
          padding: isMobile ? 8 : 12,
        }}
      >
        {(!isMobile || mobilePane === "PDF") && renderPdfPane()}
        {(!isMobile || mobilePane === "Parsed") && renderParsedPane()}
      </div>
    </div>
  );
}
