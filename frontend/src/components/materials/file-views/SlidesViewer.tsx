import { useEffect, useState, useMemo } from "react";
import { Spin, Segmented } from "antd";
import { getFileTimeline } from "../../../api/client";
import { reportNonCriticalError } from "../../../utils/monitoring";
import PageLayoutView from "./PageLayoutView";
import type { PageItem } from "./types";

const MOBILE_BREAKPOINT = 768;

export default function SlidesViewer({
  meetingId,
  fileId,
  page,
}: {
  meetingId: number;
  fileId: number;
  fileName: string;
  page?: number;
}) {
  return (
    <SlidesViewerInner
      key={`${meetingId}:${fileId}:${page ?? 1}`}
      meetingId={meetingId}
      fileId={fileId}
      page={page}
    />
  );
}

function SlidesViewerInner({
  meetingId,
  fileId,
  page,
}: {
  meetingId: number;
  fileId: number;
  page?: number;
}) {
  const [parsedPages, setParsedPages] = useState<PageItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedPageOverride, setSelectedPage] = useState<number | null>(null);
  const [isMobile, setIsMobile] = useState(false);
  const [mobilePane, setMobilePane] = useState<"Slides" | "Content">(page ? "Content" : "Slides");

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

  useEffect(() => {
    let cancelled = false;
    getFileTimeline(meetingId, fileId)
      .then((res) => {
        if (!cancelled && res.data?.kind === "pages") {
          setParsedPages(res.data.pages ?? []);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setParsedPages([]);
        }
        reportNonCriticalError("Failed to load parsed pages for slides viewer", err);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [meetingId, fileId]);

  const sortedPages = useMemo(
    () =>
      [...parsedPages]
        .filter((p) => Number.isFinite(p.page_num))
        .sort((a, b) => a.page_num - b.page_num),
    [parsedPages],
  );
  const selectedPage =
    selectedPageOverride ??
    Math.max(
      0,
      sortedPages.findIndex((item) => item.page_num === page),
    );

  // --- Left pane: Slide thumbnail grid ---
  const renderSlidesPane = () => (
    <div
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
        Slides ({sortedPages.length})
      </div>
      {loading ? (
        <div style={{ textAlign: "center", padding: 40 }}>
          <Spin />
        </div>
      ) : (
        <div
          style={{
            padding: 8,
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))",
            gap: 8,
          }}
        >
          {sortedPages.map((p, idx) => {
            const heading = p.heading?.replace(/^#{1,6}\s*/, "").trim();
            const preview =
              p.text
                ?.split("\n")
                .find((l) => l.trim() && !/^#{1,6}/.test(l.trim()))
                ?.trim()
                .slice(0, 80) ?? "";
            const isSelected = idx === selectedPage;
            return (
              <div
                key={p.page_num}
                onClick={() => setSelectedPage(idx)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    setSelectedPage(idx);
                  }
                }}
                role="button"
                tabIndex={0}
                style={{
                  border: isSelected
                    ? "2px solid var(--ant-color-primary)"
                    : "1px solid var(--color-border)",
                  borderRadius: 6,
                  padding: 8,
                  cursor: "pointer",
                  background: isSelected
                    ? "var(--ant-color-primary-bg)"
                    : "var(--color-bg-elevated)",
                  transition: "all 0.15s",
                }}
              >
                <div style={{ fontSize: 10, color: "var(--color-text-muted)", marginBottom: 4 }}>
                  Slide {p.page_num}
                </div>
                <div
                  style={{
                    fontSize: 12,
                    fontWeight: 600,
                    color: "var(--color-text-primary)",
                    lineHeight: 1.4,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    display: "-webkit-box",
                    WebkitLineClamp: 2,
                    WebkitBoxOrient: "vertical",
                  }}
                >
                  {heading || preview || "(empty)"}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );

  // --- Right pane: Selected slide content ---
  const renderContentPane = () => {
    const page = sortedPages[selectedPage];
    return (
      <div
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
          {page ? `Slide ${page.page_num} Content` : "Select a slide"}
        </div>
        {page ? (
          <PageLayoutView
            pageNum={page.page_num}
            heading={page.heading}
            text={page.text}
            imageAssets={page.image_assets ?? []}
            label="Slide"
            variant="materials"
          />
        ) : (
          <div style={{ padding: 24, color: "var(--color-text-muted)" }}>
            Select a slide on the left
          </div>
        )}
      </div>
    );
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {isMobile && (
        <div
          style={{
            padding: "8px 16px",
            borderBottom: "1px solid var(--color-border)",
            flexShrink: 0,
          }}
        >
          <Segmented
            options={["Slides", "Content"]}
            value={mobilePane}
            onChange={(v) => setMobilePane(v as "Slides" | "Content")}
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
        {(!isMobile || mobilePane === "Slides") && renderSlidesPane()}
        {(!isMobile || mobilePane === "Content") && renderContentPane()}
      </div>
    </div>
  );
}
