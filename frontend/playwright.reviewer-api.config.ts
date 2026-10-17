import { defineConfig } from "@playwright/test";

/** Disposable loopback PostgreSQL HTTP persistence. No deployed/live release acceptance. */
export default defineConfig({
  testDir: "./ui-tests", testMatch: "reviewer-api-persistence.spec.ts", workers: 1,
  timeout: 60_000, outputDir: "/tmp/alphatrade-reviewer-api-results",
  webServer: {
    command: "node scripts/run-reviewer-api.mjs", url: "http://127.0.0.1:8000/health",
    reuseExistingServer: false, timeout: 120_000,
    env: {
      UV_CACHE_DIR: "/tmp/reviewer-uv-cache", PYTHONPATH: "src",
      DATABASE_URL: "postgresql+psycopg://reviewer:local-fixture-only@127.0.0.1:55432/reviewer",
      ENVIRONMENT: "local", PROVIDER_MODE: "mock", MARKET_DATA_PROVIDER: "mock",
      EXECUTION_MODE: "paper", ENABLE_REAL_TRADING: "false", EXCHANGE_MODE: "paper_internal",
      WORKER_ENABLED: "false", WATCHER_ORCHESTRATION_ENABLED: "false",
      TELEGRAM_ALERTS_ENABLED: "false", TELEGRAM_INTERACTION_ENABLED: "false",
      PERPETUAL_EVIDENCE_SOURCE: "replay", RATE_LIMIT_USE_REDIS: "false",
      MARKET_DATA_CACHE_USE_REDIS: "false", ACCESS_TOKEN_DENYLIST_ENABLED: "false",
      EMAIL_AUTO_VERIFY_LOCAL: "true", JWT_SECRET: "local-fixture-only-secret-at-least-32-characters",
    },
  },
});
