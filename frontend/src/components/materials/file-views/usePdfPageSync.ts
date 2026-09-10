import { useCallback, useEffect, useRef, useState } from "react";
import { findPdfExcerptRange } from "../../../utils/evidenceHighlight";

type SyncSource = "pdf" | "parsed";
type Anchor = {
  page: number;
  progress: number;
  citation?: boolean;
  blockText?: string;
  blockProgress?: number;
};
const SOURCES: SyncSource[] = ["pdf", "parsed"];
const SCROLL_KEYS = new Set(["ArrowDown", "ArrowUp", "PageDown", "PageUp", "Home", "End", " "]);

function readingBlocks(page: HTMLElement) {
  return [
    ...page.querySelectorAll<HTMLElement>(
      ".markdown-body p, .markdown-body h1, .markdown-body h2, .markdown-body h3, .markdown-body li",
    ),
  ].filter((node) => !node.querySelector("p,li") && (node.textContent?.trim().length ?? 0) >= 16);
}

function textOf(node: HTMLElement) {
  return node.textContent?.replace(/\s+/g, " ").trim() ?? "";
}

type MatchedBlock = { text: string; node: HTMLElement; range: Range };
const blockCache = new WeakMap<
  HTMLElement,
  { pdf: HTMLElement; signature: string; blocks: MatchedBlock[] }
>();

function matchedBlocks(parsed: HTMLElement, pdf: HTMLElement): MatchedBlock[] {
  const signature = `${parsed.textContent}\0${pdf.textContent}`;
  const cached = blockCache.get(parsed);
  if (
    cached?.pdf === pdf &&
    cached.signature === signature &&
    cached.blocks.every((b) => b.node.isConnected && b.range.startContainer.isConnected)
  )
    return cached.blocks;
  const nodes = readingBlocks(parsed);
  const counts = new Map<string, number>();
  for (const node of nodes) {
    const text = textOf(node);
    counts.set(text, (counts.get(text) ?? 0) + 1);
  }
  const blocks = nodes.flatMap((node) => {
    const text = textOf(node);
    if (text.length > 1200 || counts.get(text) !== 1) return [];
    const range = findPdfExcerptRange(pdf, text);
    return range ? [{ text, node, range }] : [];
  });
  blockCache.set(parsed, { pdf, signature, blocks });
  return blocks;
}

/** Match actual rendered text, not a percentage of two differently sized pages. */
function semanticAnchor(
  position: Anchor,
  source: SyncSource,
  panes: Partial<Record<SyncSource, HTMLDivElement>>,
): Anchor {
  const parsed = panes.parsed?.querySelector<HTMLElement>(`[data-page-num="${position.page}"]`);
  const pdf = panes.pdf?.querySelector<HTMLElement>(`[data-page-num="${position.page}"]`);
  const container = panes[source];
  if (!parsed || !pdf || !container) return position;
  const edge = container.getBoundingClientRect().top + container.clientTop;
  const candidates = matchedBlocks(parsed, pdf)
    .flatMap(({ node, text, range }) => {
      const rect = source === "pdf" ? range.getBoundingClientRect() : node.getBoundingClientRect();
      return rect.height > 0 ? [{ text, rect }] : [];
    })
    .sort((a, b) => a.rect.top - b.rect.top);
  const preceding = candidates.filter((item) => item.rect.top <= edge + 12);
  const at = preceding[preceding.length - 1];
  if (!at || edge > at.rect.bottom + 24) return position;
  return {
    ...position,
    blockText: at.text,
    blockProgress: Math.max(0, Math.min(1, (edge - at.rect.top) / at.rect.height)),
  };
}

function pageNodes(container: HTMLElement) {
  return [...container.querySelectorAll<HTMLElement>("[data-page-num]")];
}

function layoutSignature(container: HTMLElement) {
  return [
    container.clientWidth,
    container.clientHeight,
    ...pageNodes(container).map(
      (node) => `${node.dataset.pageNum}:${node.getBoundingClientRect().height}`,
    ),
  ].join("|");
}

// A top-edge reading anchor works even when a parsed page is taller than the viewport.
function readAnchor(container: HTMLElement): Anchor | null {
  const nodes = pageNodes(container);
  if (!nodes.length) return null;
  const edge = container.getBoundingClientRect().top + container.clientTop;
  let index = 0;
  for (let i = 0; i < nodes.length; i += 1) {
    if (nodes[i].getBoundingClientRect().top <= edge + 1) index = i;
    else break;
  }
  const rect = nodes[index].getBoundingClientRect();
  const next = nodes[index + 1]?.getBoundingClientRect();
  const height = next ? next.top - rect.top : rect.height;
  return {
    page: Number(nodes[index].dataset.pageNum),
    progress: Math.max(0, Math.min(0.999, (edge - rect.top) / Math.max(1, height))),
  };
}

export default function usePdfPageSync({
  totalPages,
  evidenceExcerpt,
  enabled = true,
}: {
  totalPages: number;
  evidenceExcerpt?: string;
  enabled?: boolean;
}) {
  const [currentPage, setCurrentPage] = useState(1);
  const [syncSource, setSyncSource] = useState<SyncSource | null>(null);
  const containers = useRef<Partial<Record<SyncSource, HTMLDivElement>>>({});
  const cleanups = useRef<Partial<Record<SyncSource, () => void>>>({});
  const anchor = useRef<Anchor>({ page: 1, progress: 0 });
  const owner = useRef<SyncSource | null>(null);
  const expectedScroll = useRef<Partial<Record<SyncSource, number>>>({});
  const expectedLayout = useRef<Partial<Record<SyncSource, string>>>({});
  const frame = useRef<number | null>(null);
  const pendingUserScroll = useRef<SyncSource | null>(null);
  const layoutChangePending = useRef(false);

  const writeAnchor = useCallback(
    (source: SyncSource) => {
      const container = containers.current[source];
      if (!container || !container.clientHeight) return;
      const tail = container.clientHeight + "px";
      if (container.style.getPropertyValue("--viewer-tail-space") !== tail) {
        container.style.setProperty("--viewer-tail-space", tail);
      }
      const nodes = pageNodes(container);
      const index = nodes.findIndex((node) => Number(node.dataset.pageNum) === anchor.current.page);
      if (index < 0) return; // Never substitute a missing page with its array index.
      const rect = nodes[index].getBoundingClientRect();
      const next = nodes[index + 1]?.getBoundingClientRect();
      const height = next ? next.top - rect.top : rect.height;
      let top =
        container.scrollTop +
        rect.top -
        container.getBoundingClientRect().top -
        container.clientTop +
        anchor.current.progress * height;
      if (anchor.current.citation && evidenceExcerpt) {
        const quote =
          source === "parsed"
            ? nodes[index].querySelector<HTMLElement>("[data-evidence-highlight]")
            : findPdfExcerptRange(nodes[index], evidenceExcerpt);
        if (quote) {
          // Citation landing uses each representation's actual quotation position.
          // Plain scrolling switches back to the shared page + progress anchor.
          top += Math.max(0, quote.getBoundingClientRect().top - rect.top - 48);
        }
      } else if (anchor.current.blockText) {
        const text = anchor.current.blockText;
        const block =
          source === "pdf"
            ? findPdfExcerptRange(nodes[index], text)
            : readingBlocks(nodes[index]).find((node) => textOf(node) === text);
        if (block) {
          const blockRect = block.getBoundingClientRect();
          top =
            container.scrollTop +
            blockRect.top -
            container.getBoundingClientRect().top -
            container.clientTop +
            (anchor.current.blockProgress ?? 0) * blockRect.height;
        }
      }
      container.scrollTop = Math.max(0, top);
      expectedScroll.current[source] = container.scrollTop;
      expectedLayout.current[source] = layoutSignature(container);
    },
    [evidenceExcerpt],
  );

  const schedule = useCallback(() => {
    if (frame.current !== null) return;
    frame.current = requestAnimationFrame(() => {
      frame.current = null;
      const layoutChanged = layoutChangePending.current;
      layoutChangePending.current = false;
      const source = pendingUserScroll.current;
      pendingUserScroll.current = null;
      if (layoutChanged) {
        // A user position captured before this frame remains authoritative.
        // Reapply it to both panes after late PDF metadata, zoom or resize has
        // changed their geometry.
        if (enabled) SOURCES.forEach(writeAnchor);
      } else if (source && source === owner.current) {
        // The user's pane is never moved by its follower.
        if (enabled) writeAnchor(source === "pdf" ? "parsed" : "pdf");
      } else {
        // Remounts and explicit page jumps preserve the logical position.
        if (enabled) SOURCES.forEach(writeAnchor);
      }
    });
  }, [writeAnchor, enabled]);

  const prepareForLayoutChange = useCallback(
    (event?: Event) => {
      const source = owner.current;
      const container = source ? containers.current[source] : undefined;
      // Explicit callers run before changing zoom and can still read the old
      // geometry. A window resize event arrives after layout, so retain the last
      // stable anchor instead of deriving a new one from shifted pixels.
      const position = event ? null : container ? readAnchor(container) : null;
      if (source && position) {
        anchor.current = semanticAnchor(position, source, containers.current);
        setCurrentPage(position.page);
      }
      // WebKit may emit scroll events before ResizeObserver when zoom or viewport
      // changes clamp scrollTop. They are layout corrections, not user input.
      layoutChangePending.current = true;
      pendingUserScroll.current = null;
      expectedScroll.current = {};
      expectedLayout.current = {};
      schedule();
    },
    [schedule],
  );

  useEffect(() => {
    window.addEventListener("resize", prepareForLayoutChange);
    return () => window.removeEventListener("resize", prepareForLayoutChange);
  }, [prepareForLayoutChange]);

  const connect = useCallback(
    (source: SyncSource, container: HTMLDivElement | null) => {
      cleanups.current[source]?.();
      delete cleanups.current[source];
      delete containers.current[source];
      delete expectedScroll.current[source];
      delete expectedLayout.current[source];
      if (!container) return;
      containers.current[source] = container;
      container.dataset.pdfPane = source;
      container.tabIndex = 0;
      container.style.overflowAnchor = "none";
      container.style.overscrollBehavior = "contain";
      container.style.scrollBehavior = "auto";
      const takeControl = (event: Event) => {
        if (event instanceof KeyboardEvent && !SCROLL_KEYS.has(event.key)) return;
        owner.current = source;
        expectedLayout.current[source] = layoutSignature(container);
        setSyncSource(source);
      };
      const onScroll = () => {
        const expected = expectedScroll.current[source];
        if (expected !== undefined && Math.abs(container.scrollTop - expected) < 1) {
          delete expectedScroll.current[source];
          return;
        }
        delete expectedScroll.current[source];
        if (owner.current !== source) return;
        if (expectedLayout.current[source] !== layoutSignature(container)) {
          // Shrinking the document can clamp scrollTop before ResizeObserver
          // runs. Restore the last stable anchor rather than interpreting the
          // shifted pixels as a new user position.
          layoutChangePending.current = true;
          pendingUserScroll.current = null;
          schedule();
          return;
        }
        const position = readAnchor(container);
        if (!position) return;
        // Capture synchronously. Deferring this read until animation frame lets
        // late page-size metadata replace the geometry underneath the event.
        anchor.current = semanticAnchor(position, source, containers.current);
        setCurrentPage(position.page);
        pendingUserScroll.current = source;
        schedule();
      };
      const onResize = () => {
        layoutChangePending.current = true;
        pendingUserScroll.current = null;
        schedule();
      };
      const resize = new ResizeObserver(onResize);
      const observed = new Set<Element>();
      const observePages = () => {
        const next = new Set<Element>([container, ...pageNodes(container)]);
        for (const node of observed)
          if (!next.has(node)) {
            resize.unobserve(node);
            observed.delete(node);
          }
        for (const node of next)
          if (!observed.has(node)) {
            resize.observe(node);
            observed.add(node);
          }
        schedule();
      };
      const mutation = new MutationObserver(observePages);
      mutation.observe(container, { childList: true, subtree: true });
      observePages();
      for (const type of ["wheel", "pointerdown", "touchstart", "keydown"])
        container.addEventListener(type, takeControl, { passive: true });
      container.addEventListener("scroll", onScroll, { passive: true });
      cleanups.current[source] = () => {
        mutation.disconnect();
        resize.disconnect();
        for (const type of ["wheel", "pointerdown", "touchstart", "keydown"])
          container.removeEventListener(type, takeControl);
        container.removeEventListener("scroll", onScroll);
      };
    },
    [schedule],
  );

  const pdfContainerRef = useCallback(
    (node: HTMLDivElement | null) => connect("pdf", node),
    [connect],
  );
  const parsedContainerRef = useCallback(
    (node: HTMLDivElement | null) => connect("parsed", node),
    [connect],
  );

  const scrollBothToPage = useCallback(
    (page: number, citation = false) => {
      if (!Number.isInteger(page) || page < 1 || page > totalPages) return;
      owner.current = null;
      pendingUserScroll.current = null;
      layoutChangePending.current = false;
      anchor.current = { page, progress: 0, citation };
      setCurrentPage(page);
      setSyncSource(null);
      SOURCES.forEach(writeAnchor);
      schedule();
    },
    [totalPages, schedule, writeAnchor],
  );

  const scrollToPage = useCallback(
    (_source: SyncSource, page: number) => {
      scrollBothToPage(page);
    },
    [scrollBothToPage],
  );

  useEffect(
    () => () => {
      if (frame.current !== null) cancelAnimationFrame(frame.current);
      frame.current = null;
    },
    [],
  );

  return {
    currentPage,
    syncSource,
    scrollToPage,
    scrollBothToPage,
    pdfContainerRef,
    parsedContainerRef,
    prepareForLayoutChange,
  };
}

export type { SyncSource };
