import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:4173";
const skipWebServer = process.env.PLAYWRIGHT_SKIP_WEBSERVER === "1";
const isHeaded = process.env.PLAYWRIGHT_HEADED === "1";
const browserChannel = process.env.PLAYWRIGHT_BROWSER_CHANNEL;
const runFullStack = process.env.PLAYWRIGHT_FULL_STACK === "1";
const httpUsername = process.env.E2E_HTTP_USER;
const httpPassword = process.env.E2E_HTTP_PASSWORD;

export default defineConfig({
  testDir: "./e2e",
  // Tests under e2e/full-stack target a separately running production-style
  // stack on port 8307. Keep them out of the self-contained preview suite.
  testIgnore: runFullStack ? undefined : "**/full-stack/**",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: "list",
  use: {
    baseURL,
    locale: "en-US",
    headless: !isHeaded,
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
    trace: "on-first-retry",
    ...(httpUsername && httpPassword
      ? { httpCredentials: { username: httpUsername, password: httpPassword } }
      : {}),
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        ...(browserChannel ? { channel: browserChannel } : {}),
      },
    },
    {
      name: "firefox",
      use: { ...devices["Desktop Firefox"] },
    },
    {
      name: "webkit",
      use: { ...devices["Desktop Safari"] },
    },
  ],
  webServer: skipWebServer
    ? undefined
    : {
        command: "npm run build && npm run preview:e2e",
        url: "http://localhost:4173",
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
      },
});
