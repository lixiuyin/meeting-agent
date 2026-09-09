import { test as base } from "@playwright/test";

export * from "@playwright/test";

export function deploymentAuthHeaders(): Record<string, string> {
  const username = process.env.E2E_HTTP_USER;
  const password = process.env.E2E_HTTP_PASSWORD;
  return username && password
    ? { Authorization: `Basic ${Buffer.from(`${username}:${password}`).toString("base64")}` }
    : {};
}

// Authenticate the API test client only. Never attach the deployment key to
// browser-wide headers: those would also reach third-party font/image origins.
export const test = base.extend({
  request: async ({ playwright, baseURL }, use) => {
    const request = await playwright.request.newContext({
      baseURL,
      extraHTTPHeaders: process.env.E2E_API_KEY ? { "X-API-Key": process.env.E2E_API_KEY } : {},
      ...(process.env.E2E_HTTP_USER && process.env.E2E_HTTP_PASSWORD
        ? {
            httpCredentials: {
              username: process.env.E2E_HTTP_USER,
              password: process.env.E2E_HTTP_PASSWORD,
            },
          }
        : {}),
    });
    try {
      await use(request);
    } finally {
      await request.dispose();
    }
  },
});
