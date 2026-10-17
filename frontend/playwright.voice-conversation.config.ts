import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./ui-tests",
  testMatch: "voice-conversation.spec.ts",
  workers: 1,
  outputDir: "/tmp/alphatrade-voice-conversation-results",
  use: {
    baseURL: "http://127.0.0.1:4175", browserName: "chromium",
    launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ?? "/usr/bin/chromium" },
  },
  webServer: {
    command: "npx vite --config voice-harness/vite.config.ts",
    url: "http://127.0.0.1:4175", reuseExistingServer: false,
  },
});
