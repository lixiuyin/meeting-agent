import "@testing-library/jest-dom/vitest";

const SUPPRESSED_WARNING_SNIPPETS = [
  "[antd: message] Static function can not consume context like dynamic theme.",
  "[antd: compatible] antd v5 support React is 16 ~ 18.",
  "`--localstorage-file` was provided without a valid path",
  "localStorage is not available because --localstorage-file was not provided.",
] as const;

const shouldSuppressWarning = (value: unknown): boolean => {
  const text = value instanceof Error ? value.message : String(value ?? "");
  return SUPPRESSED_WARNING_SNIPPETS.some((snippet) => text.includes(snippet));
};

const originalConsoleWarn = console.warn.bind(console);
console.warn = ((...args: unknown[]) => {
  if (args.some(shouldSuppressWarning)) return;
  originalConsoleWarn(...args);
}) as typeof console.warn;

const originalConsoleError = console.error.bind(console);
console.error = ((...args: unknown[]) => {
  if (args.some(shouldSuppressWarning)) return;
  originalConsoleError(...args);
}) as typeof console.error;

const nodeProcess = (
  globalThis as typeof globalThis & {
    process?: { emitWarning: (...args: unknown[]) => void };
  }
).process;
if (nodeProcess?.emitWarning) {
  const originalEmitWarning = nodeProcess.emitWarning.bind(nodeProcess);
  nodeProcess.emitWarning = ((warning: string | Error, ...args: unknown[]) => {
    if (shouldSuppressWarning(warning)) return;
    return (originalEmitWarning as (...innerArgs: unknown[]) => void)(warning, ...args);
  }) as typeof nodeProcess.emitWarning;
}

// Mock matchMedia for Ant Design
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});

const originalGetComputedStyle = window.getComputedStyle.bind(window);
window.getComputedStyle = ((elt: Element, pseudoElt?: string) =>
  originalGetComputedStyle(
    elt,
    pseudoElt && pseudoElt !== "" ? undefined : pseudoElt,
  )) as typeof window.getComputedStyle;

// Mock scrollIntoView
Element.prototype.scrollIntoView = vi.fn();

// Mock scrollTo
Element.prototype.scrollTo = vi.fn();
window.scrollTo = vi.fn();

// Mock ResizeObserver
class ResizeObserverMock {
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
}
window.ResizeObserver = ResizeObserverMock;

// Mock DOMMatrix for react-pdf (pdfjs-dist)
class DOMMatrixMock {
  a = 1;
  b = 0;
  c = 0;
  d = 1;
  e = 0;
  f = 0;
  translate() {
    return this;
  }
  scale() {
    return this;
  }
}
(globalThis as Record<string, unknown>).DOMMatrix = DOMMatrixMock;

// Mock IntersectionObserver
class IntersectionObserverMock {
  root: Element | Document | null = null;
  rootMargin: string = "0px";
  thresholds: ReadonlyArray<number> = [0];
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
  takeRecords = vi.fn().mockReturnValue([]);
}
window.IntersectionObserver = IntersectionObserverMock as unknown as typeof IntersectionObserver;
