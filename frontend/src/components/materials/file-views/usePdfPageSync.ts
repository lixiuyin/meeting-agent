import { useCallback, useEffect, useRef, useState } from "react";

const SYNC_GUARD_MS = 350;
const INTERSECTION_THRESHOLD = 0.5;
const MAX_SCROLL_RETRIES = 6;

type SyncSource = "pdf" | "parsed";

interface UsePdfPageSyncOptions {
  totalPages: number;
}

interface UsePdfPageSyncReturn {
  currentPage: number;
  setCurrentPage: (page: number) => void;
  syncSource: SyncSource | null;
  scrollToPage: (source: SyncSource, pageNum: number) => void;
  scrollBothToPage: (pageNum: number) => void;
  registerPageRef: (pageNum: number, el: HTMLDivElement | null) => void;
  pdfContainerRef: React.RefObject<HTMLDivElement | null>;
  parsedContainerRef: React.RefObject<HTMLDivElement | null>;
}

export default function usePdfPageSync({
  totalPages,
}: UsePdfPageSyncOptions): UsePdfPageSyncReturn {
  const [currentPage, setCurrentPage] = useState(1);
  const [syncSourceValue, setSyncSourceValue] = useState<SyncSource | null>(null);
  const syncSource = useRef<SyncSource | null>(null);
  const pageRefs = useRef<Map<number, HTMLDivElement>>(new Map());
  const pdfContainerRef = useRef<HTMLDivElement | null>(null);
  const parsedContainerRef = useRef<HTMLDivElement | null>(null);
  const guardTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingRetries = useRef<Set<ReturnType<typeof setTimeout>>>(new Set());

  const clearGuard = useCallback(() => {
    if (guardTimer.current) {
      clearTimeout(guardTimer.current);
      guardTimer.current = null;
    }
  }, []);

  const startGuardTimer = useCallback(() => {
    clearGuard();
    guardTimer.current = setTimeout(() => {
      syncSource.current = null;
      setSyncSourceValue(null);
    }, SYNC_GUARD_MS);
  }, [clearGuard]);

  const scrollToPage = useCallback(
    function scrollToPageImpl(source: SyncSource, pageNum: number, _attempt = 0) {
      if (pageNum < 1 || pageNum > totalPages) return;

      clearGuard();
      syncSource.current = source;
      setSyncSourceValue(source);
      setCurrentPage(pageNum);

      const scrollAndGuard = (target: HTMLElement) => {
        target.scrollIntoView({ block: "start", behavior: "smooth" });
        startGuardTimer();
      };

      if (source === "pdf") {
        const el = pageRefs.current.get(pageNum);
        if (el) scrollAndGuard(el);
        else startGuardTimer();
      } else {
        const container = pdfContainerRef.current;
        if (!container) {
          startGuardTimer();
          return;
        }

        const immediateTarget = container.querySelector(
          `[data-page-num="${pageNum}"]`,
        ) as HTMLElement | null;
        if (immediateTarget) {
          scrollAndGuard(immediateTarget);
        } else if (_attempt < MAX_SCROLL_RETRIES) {
          const nextAttempt = _attempt + 1;
          const delay = _attempt === 0 ? 16 : 80 * nextAttempt;
          const tid = setTimeout(() => {
            pendingRetries.current.delete(tid);
            scrollToPageImpl(source, pageNum, nextAttempt);
          }, delay);
          pendingRetries.current.add(tid);
        } else {
          startGuardTimer();
        }
      }
    },
    [totalPages, clearGuard, startGuardTimer],
  );

  const registerPageRef = useCallback((pageNum: number, el: HTMLDivElement | null) => {
    if (el) {
      pageRefs.current.set(pageNum, el);
    } else {
      pageRefs.current.delete(pageNum);
    }
  }, []);

  // Scroll BOTH panes to the same page.  Used for initial citation jumps
  // where the user expects PDF and parsed views to land on the target page
  // simultaneously.  Retries while the PDF pages stream in via AutoSizer.
  const scrollBothToPage = useCallback(
    function scrollBothToPageImpl(pageNum: number, _attempt = 0) {
      if (pageNum < 1 || pageNum > totalPages) return;
      clearGuard();
      // Mark as a programmatic scroll so the IntersectionObservers don't fire
      // back-and-forth while smooth-scrolling settles.
      syncSource.current = "pdf";
      setSyncSourceValue("pdf");
      setCurrentPage(pageNum);

      const parsedEl = pageRefs.current.get(pageNum);
      const pdfContainer = pdfContainerRef.current;
      const pdfEl = pdfContainer?.querySelector(
        `[data-page-num="${pageNum}"]`,
      ) as HTMLElement | null;

      let didScroll = false;
      if (parsedEl) {
        parsedEl.scrollIntoView({ block: "start", behavior: "smooth" });
        didScroll = true;
      }
      if (pdfEl) {
        pdfEl.scrollIntoView({ block: "start", behavior: "smooth" });
        didScroll = true;
      }

      if (!didScroll && _attempt < MAX_SCROLL_RETRIES) {
        const nextAttempt = _attempt + 1;
        const delay = _attempt === 0 ? 32 : 100 * nextAttempt;
        const tid = setTimeout(() => {
          pendingRetries.current.delete(tid);
          scrollBothToPageImpl(pageNum, nextAttempt);
        }, delay);
        pendingRetries.current.add(tid);
        return;
      }
      startGuardTimer();
    },
    [totalPages, clearGuard, startGuardTimer],
  );

  // Helper: create an IntersectionObserver for a container
  const createPaneObserver = useCallback(
    (container: HTMLDivElement, source: SyncSource, oppositeSource: SyncSource) => {
      const observer = new IntersectionObserver(
        (entries) => {
          if (syncSource.current === oppositeSource) return;

          let bestPage = 0;
          let bestRatio = 0;
          for (const entry of entries) {
            if (entry.intersectionRatio > bestRatio) {
              bestRatio = entry.intersectionRatio;
              const pageNum = Number((entry.target as HTMLElement).dataset.pageNum);
              if (pageNum > 0) bestPage = pageNum;
            }
          }
          if (bestPage > 0 && bestRatio >= INTERSECTION_THRESHOLD) {
            scrollToPage(source, bestPage);
          }
        },
        { root: container, threshold: INTERSECTION_THRESHOLD },
      );

      const observeAll = () => {
        const pages = container.querySelectorAll("[data-page-num]");
        for (const page of pages) observer.observe(page);
      };

      return { observer, observeAll };
    },
    [scrollToPage],
  );

  // Cleanup all pending retry timers on unmount
  useEffect(() => {
    const pending = pendingRetries.current;
    return () => {
      pending.forEach((tid) => clearTimeout(tid));
      pending.clear();
    };
  }, []);

  // IntersectionObserver for PDF pages (left pane)
  useEffect(() => {
    const container = pdfContainerRef.current;
    if (!container) return;

    const { observer, observeAll } = createPaneObserver(container, "pdf", "parsed");
    observeAll();

    // Re-observe after a frame to catch late-rendered PDF pages (AutoSizer)
    const rafId = requestAnimationFrame(observeAll);

    return () => {
      observer.disconnect();
      cancelAnimationFrame(rafId);
    };
  }, [createPaneObserver, totalPages]);

  // IntersectionObserver for parsed pages (right pane)
  useEffect(() => {
    const container = parsedContainerRef.current;
    if (!container) return;

    const { observer, observeAll } = createPaneObserver(container, "parsed", "pdf");
    observeAll();

    return () => observer.disconnect();
  }, [createPaneObserver, totalPages]);

  return {
    currentPage,
    setCurrentPage,
    syncSource: syncSourceValue,
    scrollToPage,
    scrollBothToPage,
    registerPageRef,
    pdfContainerRef,
    parsedContainerRef,
  };
}

export { SYNC_GUARD_MS, INTERSECTION_THRESHOLD };
export type { SyncSource };
