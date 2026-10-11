import path from "node:path";
import { defineConfig } from "@playwright/test";
import base from "./playwright.polish.config";

/** Frontend contract fixtures only; never starts a backend or a provider. */
export default defineConfig({
  ...base,
  testMatch: "frontend-refinement.spec.ts",
  outputDir: "/tmp/alphatrade-refinement-results",
  use: { ...base.use, trace: "retain-on-failure", screenshot: "only-on-failure" },
  webServer: {
    command: "npm run dev -- --hostname 127.0.0.1",
    url: "http://127.0.0.1:3000/login",
    reuseExistingServer: true,
    timeout: 120_000,
    env: { NEXT_FONT_GOOGLE_MOCKED_RESPONSES: path.resolve("test-fixtures/offline-fonts.cjs") },
  },
});
