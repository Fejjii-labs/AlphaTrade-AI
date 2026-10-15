import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3000";
const apiURL = process.env.PLAYWRIGHT_API_URL ?? "http://localhost:8000";

const e2eBackendEnv = {
  PYTHONPATH: "src",
  RATE_LIMIT_USE_REDIS: "false",
  MARKET_DATA_CACHE_USE_REDIS: "false",
  PROVIDER_MODE: "mock",
  MARKET_DATA_PROVIDER: "mock",
  JOURNAL_RAG_SYNC_ENABLED: "true",
  EMAIL_AUTO_VERIFY_LOCAL: "true",
  ACCESS_TOKEN_DENYLIST_ENABLED: "false",
  JWT_SECRET: "e2e-test-secret-at-least-32-characters-long",
  DATABASE_URL: "sqlite+pysqlite:///./.e2e-alphatrade.db",
  LOG_JSON: "false",
  CORS_ORIGINS: "http://localhost:3000,http://127.0.0.1:3000",
};

export default defineConfig({
  testDir: ".",
  // Research persistence requires an explicit disposable PostgreSQL fixture and
  // two selected phases separated by a real API restart; never run it on staging.
  testMatch: ["e2e/**/*.spec.ts", "ui-tests/reviewer-wave.spec.ts", "ui-tests/reviewer-api-persistence.spec.ts", "ui-tests/experiments-api.spec.ts", ...(process.env.EXPERIMENT_BROWSER_AUTH_FILE ? ["ui-tests/research-api.spec.ts", "ui-tests/auth-recovery-api.spec.ts", "ui-tests/agent-knowledge-api.spec.ts", "ui-tests/conversation-viewport-api.spec.ts"] : [])],
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  // Local web servers share one SQLite database for durable write flows.
  // PostgreSQL concurrency is exercised by the backend capture regressions.
  workers: process.env.PLAYWRIGHT_SKIP_WEBSERVER ? undefined : 1,
  retries: process.env.CI ? 1 : 0,
  use: {
    baseURL,
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      testIgnore: [
        "**/capture-screenshots.spec.ts",
        "**/webkit-iphone-audit.spec.ts",
      ],
      use: {
        ...devices["Desktop Chrome"],
        launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
          ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH }
          : undefined,
      },
    },
    {
      name: "webkit",
      testIgnore: [
        "**/capture-screenshots.spec.ts",
        "**/webkit-iphone-audit.spec.ts",
        "**/*staging*.spec.ts",
      ],
      use: { ...devices["Desktop Safari"] },
    },
    {
      name: "iphone-15-pro",
      testMatch: [
        "**/webkit-iphone-audit.spec.ts",
        "**/notification-settings-v2.spec.ts",
      ],
      use: { ...devices["iPhone 15 Pro"] },
    },
    {
      name: "iphone-15-pro-landscape",
      testMatch: [
        "**/webkit-iphone-audit.spec.ts",
        "**/notification-settings-v2.spec.ts",
      ],
      use: { ...devices["iPhone 15 Pro landscape"] },
    },
    {
      name: "screenshots",
      testMatch: "**/capture-screenshots.spec.ts",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: process.env.PLAYWRIGHT_SKIP_WEBSERVER
    ? undefined
    : [
        {
          command: "bash scripts/run_e2e_server.sh",
          cwd: "../backend",
          env: e2eBackendEnv,
          url: `${apiURL}/health`,
          reuseExistingServer: !process.env.CI,
          timeout: 120_000,
        },
        {
          command: process.env.PLAYWRIGHT_PRODUCTION === "true"
            ? "npm run start -- --port 3000 --hostname localhost"
            : "npm run dev -- --port 3000 --hostname localhost",
          env: { NEXT_PUBLIC_API_URL: apiURL },
          url: baseURL,
          reuseExistingServer: !process.env.CI,
          timeout: 120_000,
        },
      ],
});
