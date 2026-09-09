/// <reference types="vitest/config" />
import { createLogger, defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backendProxyTarget = process.env.VITE_BACKEND_PROXY_TARGET ?? "http://localhost:7008";

// Vite registers its own `socket.on("error", …)` inside `proxyReqWs` and
// unconditionally calls `logger.error("ws proxy socket error: …")` from
// there — separate from the proxy-level `error` event we hook below. The
// suppression we install on `proxy.on("error")` therefore can't silence
// those frames, so we filter them at the logger layer instead. EPIPE and
// ECONNRESET on the WS proxy are expected dev-mode noise (backend reload,
// frontend HMR full-reload, client tab close) and should never fail loud.
const SUPPRESSED_WS_ERROR_PATTERNS: readonly RegExp[] = [
  /ws proxy socket error[\s\S]*\b(EPIPE|ECONNRESET)\b/,
];

const filteringLogger = createLogger();
const originalError = filteringLogger.error.bind(filteringLogger);
filteringLogger.error = (msg, options) => {
  if (typeof msg === "string" && SUPPRESSED_WS_ERROR_PATTERNS.some((re) => re.test(msg))) {
    return;
  }
  originalError(msg, options);
};

export default defineConfig({
  customLogger: filteringLogger,
  plugins: [react()],
  build: {
    chunkSizeWarningLimit: 1000,
    rollupOptions: {
      output: {
        manualChunks(id) {
          // A shared helper inside the PDF chunk would make every route preload
          // the whole reader. Keep runtime glue in a tiny independent chunk.
          if (id.includes("commonjsHelpers") || id.includes("/node_modules/clsx/"))
            return "runtime";
          if (id.includes("node_modules")) {
            // Let Rollup partition Ant Design with the lazy routes that use it.
            // Forcing every Ant component into one shared chunk made a simple
            // first render download almost 1 MB of route-only UI code.
            if (id.includes("framer-motion")) return "motion";
            if (
              id.includes("react-markdown") ||
              id.includes("rehype-") ||
              id.includes("unified") ||
              id.includes("remark-") ||
              id.includes("micromark")
            )
              return "markdown";
            if (id.includes("pdfjs-dist") || id.includes("react-pdf")) return "pdf";
            if (id.includes("react-virtuoso")) return "virtualization";
            if (id.includes("react-dom") || id.includes("react/") || id.includes("react-router"))
              return "vendor";
            if (id.includes("@sentry")) return "sentry";
          }
        },
      },
    },
  },
  server: {
    host: "127.0.0.1",
    port: 8307,
    strictPort: true,
    proxy: {
      "/api": {
        target: backendProxyTarget,
        changeOrigin: true,
        ws: true,
        configure: (proxy) => {
          proxy.on("error", (err, _req, _res) => {
            // Suppress EPIPE / ECONNRESET on WebSocket proxy — these are
            // expected when the backend restarts or a WS client disconnects
            // before the proxy finishes writing.  Harmless dev-mode noise.
            if (
              err &&
              typeof err === "object" &&
              "code" in err &&
              (err.code === "EPIPE" || err.code === "ECONNRESET")
            ) {
              return;
            }
            console.warn("[vite] proxy error:", (err as Error).message);
          });
        },
      },
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    coverage: {
      thresholds: {
        // Enforced in CI. Raise this baseline as component/API coverage grows.
        lines: 50,
      },
    },
  },
});
