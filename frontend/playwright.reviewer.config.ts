import path from "node:path";
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./ui-tests", testMatch: "reviewer-wave.spec.ts", workers: 1,
  timeout: 60_000, expect: { timeout: 15_000 },
  outputDir: "/tmp/alphatrade-reviewer-browser-results",
  use: { baseURL: "http://127.0.0.1:3000", browserName: "chromium",
    launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ?? "/usr/bin/chromium" } },
  webServer: {
    command: "npm run start -- --hostname 127.0.0.1", url: "http://127.0.0.1:3000/login",
    reuseExistingServer: false, timeout: 120_000,
    env: { NEXT_FONT_GOOGLE_MOCKED_RESPONSES: path.resolve("test-fixtures/offline-fonts.cjs") },
  },
});
