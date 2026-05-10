import { useEffect } from "react";

const LOCK_CLASS = "main-content-scroll-locked";
const LOCK_COUNT_ATTR = "data-main-scroll-lock-count";

export function useMainContentScrollLock(locked: boolean) {
  useEffect(() => {
    if (!locked || typeof document === "undefined") return;

    const { body } = document;
    const current = Number(body.getAttribute(LOCK_COUNT_ATTR) ?? "0");
    const next = current + 1;

    body.setAttribute(LOCK_COUNT_ATTR, String(next));
    body.classList.add(LOCK_CLASS);

    return () => {
      const active = Number(body.getAttribute(LOCK_COUNT_ATTR) ?? "0");
      const updated = Math.max(0, active - 1);
      if (updated === 0) {
        body.removeAttribute(LOCK_COUNT_ATTR);
        body.classList.remove(LOCK_CLASS);
      } else {
        body.setAttribute(LOCK_COUNT_ATTR, String(updated));
      }
    };
  }, [locked]);
}
