import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Button, Modal, Segmented, Spin, Tooltip } from "antd";
import { ZoomInOutlined, ZoomOutOutlined, FilePdfOutlined } from "@ant-design/icons";
import { Document, Page } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";
import { AutoSizer } from "react-virtualized-auto-sizer";
import "../../../pdf-worker";
import { getMeetingFileUrl } from "../../../api/client";
import PageLayoutView from "./PageLayoutView";
import usePdfPageSync from "./usePdfPageSync";
import type { PageItem } from "./types";
import type { PDFDocumentProxy } from "pdfjs-dist";
import { DOCUMENT_VIEWER_MODAL } from "../viewerModalPresets";
import { useMainContentScrollLock } from "../../../hooks/useMainContentScrollLock";

interface PdfComparisonModalProps {
  open: boolean;
  meetingId: number;
  fileId: number;
  fileName: string;
  pages: PageItem[];
  loading?: boolean;
  onClose: () => void;
}

const ZOOM_STEP = 0.25;
const MIN_ZOOM = 0.5;
const MAX_ZOOM = 3.0;
const MOBILE_BREAKPOINT = 768;

export default function PdfComparisonModal({
  open,
  meetingId,
  fileId,
  fileName,
  pages,
  loading,
  onClose,
}: PdfComparisonModalProps) {
  const [numPages, setNumPages] = useState(0);
  const [pdfError, setPdfError] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1.0);
  const [mobilePane, setMobilePane] = useState<"PDF" | "Parsed">("PDF");
  const [isMobile, setIsMobile] = useState(false);

  useMainContentScrollLock(open);

  const sortedPages = useMemo(() => [...pages].sort((a, b) => a.page_num - b.page_num), [pages]);

  const sync = usePdfPageSync({
    totalPages: Math.max(numPages, sortedPages.length),
  });

  // Mobile breakpoint detection (RAF-debounced)
  useEffect(() => {
    if (!open) return;
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
  }, [open]);

  const onDocumentLoadSuccess = useCallback(({ numPages: n }: PDFDocumentProxy) => {
    setNumPages(n);
    setPdfError(null);
  }, []);

  const onDocumentLoadError = useCallback((error: Error) => {
    setPdfError(error.message || "Failed to load PDF");
  }, []);

  const zoomIn = useCallback(() => setZoom((z) => Math.min(z + ZOOM_STEP, MAX_ZOOM)), []);
  const zoomOut = useCallback(() => setZoom((z) => Math.max(z - ZOOM_STEP, MIN_ZOOM)), []);

  const pdfUrl = useMemo(() => getMeetingFileUrl(meetingId, fileId), [meetingId, fileId]);

  const handlePageClick = useCallback(
    (pageNum: number) => {
      sync.scrollToPage("parsed", pageNum);
    },
    [sync],
  );

  const pageNumbers = useMemo(() => Array.from({ length: numPages }, (_, i) => i + 1), [numPages]);

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
          <Alert type="error" title="Failed to load PDF" description={pdfError} showIcon />
        </div>
      ) : (
        <div ref={sync.pdfContainerRef} style={{ flex: 1, overflow: "auto" }}>
          <AutoSizer
            style={{ width: "100%", height: "100%" }}
            renderProp={({ width }: { width?: number }) => {
              if (width === 0) return null;
              if (!width) return null;
              const scaledWidth = Math.floor(width * zoom);
              return (
                <Document
                  file={pdfUrl}
                  onLoadSuccess={onDocumentLoadSuccess}
                  onLoadError={onDocumentLoadError}
                  loading={
                    <div style={{ textAlign: "center", padding: 40 }}>
                      <Spin />
                    </div>
                  }
                >
                  {pageNumbers.map((pageNum) => (
                    <div
                      key={pageNum}
                      data-page-num={pageNum}
                      style={{
                        marginBottom: 8,
                        boxShadow: "var(--shadow-sm)",
                      }}
                    >
                      <Page
                        pageNumber={pageNum}
                        width={scaledWidth}
                        loading={
                          <div
                            style={{
                              width: scaledWidth,
                              height: scaledWidth * 1.414,
                              display: "flex",
                              alignItems: "center",
                              justifyContent: "center",
                              background: "var(--color-bg-muted)",
                            }}
                          >
                            <Spin />
                          </div>
                        }
                      />
                    </div>
                  ))}
                </Document>
              );
            }}
          />
        </div>
      )}
    </div>
  );

  const renderParsedPane = () => (
    <div
      ref={sync.parsedContainerRef}
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        overflow: "auto",
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

      {loading ? (
        <div style={{ textAlign: "center", padding: 40 }}>
          <Spin />
        </div>
      ) : sortedPages.length === 0 ? (
        <div
          style={{
            padding: 24,
            color: "var(--color-text-muted)",
            textAlign: "center",
          }}
        >
          No parsed content available
        </div>
      ) : (
        <div style={{ padding: 12 }}>
          {sortedPages.map((page) => {
            const isActive = page.page_num === sync.currentPage;
            return (
              <div
                key={page.page_num}
                data-page-num={page.page_num}
                ref={(el) => sync.registerPageRef(page.page_num, el)}
                role="button"
                tabIndex={0}
                style={{
                  marginBottom: 12,
                  borderLeft: isActive ? "3px solid var(--color-primary)" : "3px solid transparent",
                  paddingLeft: 12,
                  borderRadius: 4,
                  transition: "border-left-color 0.2s",
                  cursor: "pointer",
                }}
                onClick={() => handlePageClick(page.page_num)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    handlePageClick(page.page_num);
                  }
                }}
              >
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    marginBottom: 8,
                  }}
                >
                  <span
                    style={{
                      fontSize: 11,
                      fontWeight: 700,
                      color: "var(--color-primary)",
                      background: "var(--color-bg-muted)",
                      padding: "2px 8px",
                      borderRadius: 10,
                    }}
                  >
                    Page {page.page_num}
                  </span>
                  {page.heading && (
                    <span
                      style={{
                        fontSize: 13,
                        fontWeight: 600,
                        color: "var(--color-text-primary)",
                      }}
                    >
                      {page.heading.replace(/^#{1,6}\s*/, "")}
                    </span>
                  )}
                </div>
                <PageLayoutView
                  pageNum={page.page_num}
                  heading={page.heading}
                  text={page.text}
                  imageAssets={page.image_assets ?? []}
                  label="Page"
                  variant="modal"
                />
              </div>
            );
          })}
        </div>
      )}
    </div>
  );

  return (
    <Modal
      title={`PDF Comparison — ${fileName}`}
      open={open}
      onCancel={onClose}
      footer={null}
      width={DOCUMENT_VIEWER_MODAL.width}
      style={{ top: isMobile ? 12 : DOCUMENT_VIEWER_MODAL.top }}
      wrapClassName={DOCUMENT_VIEWER_MODAL.wrapClassName}
      styles={{
        body: {
          height: DOCUMENT_VIEWER_MODAL.bodyHeight,
          padding: DOCUMENT_VIEWER_MODAL.bodyPadding,
        },
      }}
      zIndex={1210}
      destroyOnHidden
    >
      {isMobile && (
        <div style={{ marginBottom: 12 }}>
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
          height: isMobile ? "calc(100% - 48px)" : "100%",
        }}
      >
        {(!isMobile || mobilePane === "PDF") && renderPdfPane()}
        {(!isMobile || mobilePane === "Parsed") && renderParsedPane()}
      </div>
    </Modal>
  );
}
