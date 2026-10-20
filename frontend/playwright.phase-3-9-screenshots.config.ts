import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./ui-tests",
  testMatch: "phase-3-9-griller-screenshots.spec.ts",
  timeout: 120_000,
  expect: { timeout: 20_000 },
  workers: 1,
  outputDir: "/tmp/alphatrade-phase-3-9-screenshot-results",
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
