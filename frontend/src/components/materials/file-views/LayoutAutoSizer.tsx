import { useLayoutEffect, useRef, useState, type ReactNode } from "react";

/** Measure layout pixels, never the animated/transformed bounding rectangle. */
export default function LayoutAutoSizer({
  renderProp,
}: {
  renderProp: (size: { width: number }) => ReactNode;
}) {
  const host = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const parent = host.current?.parentElement;
    if (!parent) return;
    const measure = () => setWidth(parent.clientWidth);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(parent);
    return () => observer.disconnect();
  }, []);
  return (
    <div ref={host} style={{ width: "100%", height: "100%" }}>
      {renderProp({ width })}
    </div>
  );
}
