/**
 * Validate that a URL uses a safe scheme (`http:` / `https:`).
 *
 * Rejects `javascript:`, `data:`, `file:`, and malformed URLs to prevent
 * XSS when rendering user-controlled links as clickable anchors.
 */
export function isSafeExternalUrl(raw?: string | null): boolean {
  if (!raw) return false;
  try {
    const u = new URL(raw);
    return u.protocol === "http:" || u.protocol === "https:";
  } catch {
    return false;
  }
}

/**
 * Open a URL in a new tab with origin/scheme validation.
 *
 * Rejects `javascript:` and `data:` schemes to prevent XSS, and only
 * allows `http:` / `https:` origins.
 */
export function openExternalInNewTab(url: string): void {
  let parsed: URL;
  try {
    parsed = new URL(url, window.location.origin);
  } catch {
    return;
  }

  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    return;
  }

  const opened = window.open(parsed.href, "_blank", "noopener=yes,noreferrer=yes");
  if (opened) opened.opener = null;
}
