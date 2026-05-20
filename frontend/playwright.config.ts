import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  timeout: 30000,
  tsconfig: "./tests/tsconfig.json",
  reporter: [["list"], ["html", { open: "always" }]],
  use: {
    baseURL: "http://localhost:3002",
    screenshot: "only-on-failure",
    launchOptions: {
      headless: false,      // เปิด browser ให้เห็นขณะทดสอบ
      slowMo: 500,          // ชะลอ 500ms ต่อ action เพื่อให้ดูทัน
    },
  },
  projects: [
    { name: "chromium", use: { browserName: "chromium" } },
  ],
});
