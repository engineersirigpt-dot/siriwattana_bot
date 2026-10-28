// V5 chat UI tests: the real frontend (next dev) against the mock API
// (e2e-ui/mock-api.mjs) — no backend, no OpenAI.
//
//   npx playwright test -c e2e-ui            # all
//   SHOTS_DIR=/path npx playwright test -c e2e-ui screenshots   # before/after pictures
import { defineConfig } from "@playwright/test";

const FRONT = 3100;
const MOCK = 4010;

export default defineConfig({
  testDir: ".",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  workers: 1, // one mock server with shared state
  reporter: [["list"]],
  outputDir: "./test-results",
  use: {
    baseURL: `http://127.0.0.1:${FRONT}`,
    headless: true,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: [
    {
      command: "node e2e-ui/mock-api.mjs",
      cwd: "..",
      url: `http://127.0.0.1:${MOCK}/health`,
      reuseExistingServer: true,
      env: { MOCK_PORT: String(MOCK) },
    },
    {
      command: `npx next dev -p ${FRONT} -H 127.0.0.1`,
      cwd: "..",
      url: `http://127.0.0.1:${FRONT}/login`,
      reuseExistingServer: true,
      timeout: 240_000,
      env: { NEXT_PUBLIC_API_BASE: `http://127.0.0.1:${MOCK}` },
    },
  ],
  projects: [
    { name: "desktop", use: { browserName: "chromium", viewport: { width: 1440, height: 900 } } },
    {
      name: "mobile",
      use: { browserName: "chromium", viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 },
    },
  ],
});
