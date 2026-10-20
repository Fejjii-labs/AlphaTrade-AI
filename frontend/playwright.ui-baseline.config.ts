import { defineConfig } from "@playwright/test";

/**
 * Offline screenshot capture for the phase 0/1 UI baseline.
 * Not part of `npm run test:e2e`. Frontend fixtures only — no backend.
 */
export default defineConfig({
  testDir: "./ui-tests",
  testMatch: "ui-baseline-screenshots.spec.ts",
  timeout: 90_000,
  expect: { timeout: 20_000 },
  workers: 1,
  outputDir: "/tmp/alphatrade-ui-baseline-results",
  use: {
    baseURL: "http://127.0.0.1:3010",
    browserName: "chromium",
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH }
      : {},
  },
  webServer: {
    command: "npm run dev -- --hostname 127.0.0.1 --port 3010",
    url: "http://127.0.0.1:3010/login",
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
