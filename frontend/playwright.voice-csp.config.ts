import { defineConfig, devices } from "@playwright/test";

/** Voice socket vs production CSP; self-contained local endpoints, no web servers. */
export default defineConfig({
  testDir: "./e2e",
  testMatch: "voice-csp-websocket.spec.ts",
  outputDir: "/tmp/alphatrade-voice-csp-test-results",
  timeout: 30_000,
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
          ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH }
          : undefined,
      },
    },
  ],
});
