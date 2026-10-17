import { defineConfig } from "@playwright/test";

/** Real loopback API/PostgreSQL, actual login; no hosted/provider acceptance. */
export default defineConfig({
  testDir: "./ui-tests", testMatch: "voice-monitoring-api.spec.ts", workers: 1,
  timeout: 120_000, expect: { timeout: 20_000 },
  outputDir: "/tmp/alphatrade-voice-monitoring-browser-results",
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL,
    browserName: "chromium",
    launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ?? "/usr/bin/chromium" },
    trace: "retain-on-failure", screenshot: "only-on-failure",
  },
});
