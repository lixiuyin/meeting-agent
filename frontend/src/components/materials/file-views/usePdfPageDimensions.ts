import { useCallback, useEffect, useRef, useState } from "react";
import type { PDFDocumentProxy } from "pdfjs-dist";

// Metadata dimensions preserve exact page anchors without rendering every canvas.
export function usePdfPageDimensions() {
  const [ratios, setRatios] = useState<Record<number, number>>({});
  const generation = useRef(0);
  useEffect(
    () => () => {
      generation.current += 1;
    },
    [],
  );
  const loadDimensions = useCallback(async (document: PDFDocumentProxy) => {
    const ticket = ++generation.current;
    const values: Record<number, number> = {};
    setRatios({});
    if (typeof document.getPage !== "function") return; // lightweight test documents
    let next = 1;
    await Promise.all(
      Array.from({ length: Math.min(4, document.numPages) }, async () => {
        while (next <= document.numPages && generation.current === ticket) {
          const number = next++;
          try {
            const page = await document.getPage(number);
            const viewport = page.getViewport({ scale: 1 });
            values[number] = viewport.height / viewport.width;
            if (generation.current === ticket && (number <= 4 || number % 16 === 0))
              setRatios({ ...values });
          } catch {
            // Individual errors remain visible in react-pdf's page error renderer.
          }
        }
      }),
    );
    if (generation.current === ticket) setRatios(values);
  }, []);
  return { ratios, loadDimensions };
}
