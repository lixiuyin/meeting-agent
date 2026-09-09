import { useEffect, useRef, useState } from "react";
import { Page } from "react-pdf";

// Retain every page anchor; only expensive canvas/text/annotation layers are virtualized.
// Dimensions come from PDF metadata, not rendered DOM estimates, including mixed page sizes.
export default function VirtualPdfPage({
  number,
  width,
  ratio,
  activePage,
}: {
  number: number;
  width: number;
  ratio?: number;
  activePage: number;
}) {
  const element = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);
  const [measuredRatio, setMeasuredRatio] = useState<number>();
  useEffect(() => {
    const node = element.current;
    if (!node || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting), {
      root: node.closest('[data-pdf-pane="pdf"]'),
      rootMargin: "600px 0px",
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  const height = width * (ratio ?? measuredRatio ?? Math.SQRT2);
  const rendered = visible || Math.abs(number - activePage) <= 1;
  return (
    <div
      ref={element}
      data-page-num={number}
      data-rendered={rendered}
      style={{
        width,
        height,
        marginBottom: 8,
        boxShadow: "var(--shadow-sm)",
        background: "var(--color-bg-muted)",
      }}
    >
      {rendered && (
        <Page
          pageNumber={number}
          width={width}
          devicePixelRatio={Math.min(window.devicePixelRatio || 1, 2)}
          onLoadSuccess={(page) => {
            const viewport = page.getViewport({ scale: 1 });
            setMeasuredRatio(viewport.height / viewport.width);
          }}
          loading={<div style={{ height }} aria-label={`Loading page ${number}`} />}
        />
      )}
    </div>
  );
}
