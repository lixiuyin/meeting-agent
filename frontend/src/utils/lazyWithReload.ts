import { lazy, type ComponentType } from "react";

const LAZY_RELOAD_KEY_PREFIX = "lazy-chunk-reload";
const RELOAD_COOLDOWN_MS = 5 * 60 * 1000;

function shouldReloadForChunkError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error);
  return (
    message.includes("Failed to fetch dynamically imported module") ||
    message.includes("Importing a module script failed") ||
    message.includes("Loading chunk")
  );
}

export function lazyWithReload<T extends ComponentType<unknown>>(
  importer: () => Promise<{ default: T }>,
) {
  return lazy(async () => {
    try {
      const mod = await importer();
      sessionStorage.removeItem(LAZY_RELOAD_KEY_PREFIX);
      return mod;
    } catch (error) {
      if (shouldReloadForChunkError(error)) {
        const reloadKey = `${LAZY_RELOAD_KEY_PREFIX}:${window.location.pathname}`;
        const lastReload = sessionStorage.getItem(reloadKey);
        const now = Date.now();
        if (!lastReload || now - Number(lastReload) > RELOAD_COOLDOWN_MS) {
          sessionStorage.setItem(reloadKey, String(now));
          window.location.reload();
        }
      }
      throw error;
    }
  });
}
