import { defineConfig } from "@playwright/test";
import polish from "./playwright.polish.config";

/** Actual Dashboard/Journal routes with read-only synthetic contract responses. */
export default defineConfig({
  ...polish,
  testDir: ".",
  testMatch: "e2e/native-activity.spec.ts",
  outputDir: "/tmp/alphatrade-native-activity-results",
});
