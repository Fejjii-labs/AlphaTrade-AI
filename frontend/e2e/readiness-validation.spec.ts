import { expect, test, type ConsoleMessage, type Page } from "@playwright/test";

import { installSharedE2ESession, paperModeActive } from "./helpers/shared-e2e-auth";

/**
 * Automated browser readiness checks for AT-041 PR4.
 * These are NOT a substitute for physical iPhone / staging walkthroughs.
 */

const ROUTES = [
  "/",
  "/tradingview-signals",
  "/workspace",
  "/journal",
  "/knowledge",
  "/lessons",
  "/analytics",
  "/portfolio",
  "/positions",
  "/risk",
  "/proposals",
  "/approvals",
  "/market",
  "/settings",
  "/decision",
] as const;

test.describe("Automated readiness validation (AT-041 PR4)", () => {
  test.describe.configure({ mode: "serial" });

  test("representative routes: one h1, no 390px overflow, paper posture, no console/5xx", async ({
    page,
    request,
  }) => {
    await installSharedE2ESession(page, request);

    const consoleErrors: string[] = [];
    const onConsole = (msg: ConsoleMessage) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    };
    page.on("console", onConsole);

    const failedRequests: string[] = [];
    page.on("response", (response) => {
      if (response.status() >= 500) {
        failedRequests.push(`${response.status()} ${response.url()}`);
      }
    });

    await page.setViewportSize({ width: 390, height: 844 });

    for (const route of ROUTES) {
      consoleErrors.length = 0;
      failedRequests.length = 0;
      await page.goto(route);
      await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
      const h1Count = await page.getByRole("heading", { level: 1 }).count();
      expect(h1Count, `${route} should have exactly one h1`).toBe(1);

      const overflow = await page.evaluate(() => {
        const root = document.documentElement;
        return root.scrollWidth > root.clientWidth + 1;
      });
      expect(overflow, `${route} horizontal overflow at 390px`).toBeFalsy();

      await expect(paperModeActive(page), `${route} paper posture`).toBeVisible();
      expect(
        consoleErrors.filter(
          (text) =>
            !/favicon|Download the React DevTools|Encountered two children with the same key/i.test(
              text,
            ),
        ),
        `${route} console errors`,
      ).toEqual([]);
      expect(failedRequests, `${route} failed requests`).toEqual([]);
    }
  });

  test("kill-switch BLOCK visibility surface remains present on portfolio", async ({
    page,
    request,
  }) => {
    await installSharedE2ESession(page, request);

    await page.route("**/risk/kill-switch**", async (route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            organization_id: "org",
            active: true,
            reason: "Readiness drill",
            activated_by: "user",
            activated_at: "2026-07-29T12:00:00.000Z",
            deactivated_by: null,
            deactivated_at: null,
            version: 2,
            scope: "organization",
            global_active: false,
            execution_blocked: true,
          }),
        });
        return;
      }
      await route.continue();
    });

    await page.goto("/portfolio");
    await expect(page.getByTestId("paper-portfolio-page")).toBeVisible();
    await expect(page.getByText(/block|kill switch/i).first()).toBeVisible();
  });

  test("deep-link matrix smoke: tab/entry/document/signal notices are user-visible", async ({
    page,
    request,
  }) => {
    await installSharedE2ESession(page, request);

    await page.goto("/analytics?tab=comparison");
    await expect(page).toHaveURL(/tab=comparison/);

    await page.goto("/journal?entry=missing-for-readiness");
    await expect(page.getByTestId("journal-stale-entry")).toBeVisible();

    await page.goto("/knowledge?document=missing-for-readiness");
    await expect(page.getByTestId("knowledge-document-stale")).toBeVisible();

    await page.goto("/tradingview-signals?signal=missing-for-readiness");
    await expect(page.getByTestId("signal-deep-link-missing")).toBeVisible();
  });
});

test.describe("Pending physical / staging validation (documented, not automated)", () => {
  test("records that staging and physical iPhone validation remain pending", () => {
    test.info().annotations.push({
      type: "pending-manual",
      description:
        "Staging env drill + physical iPhone Safari walkthrough remain PENDING and are not claimed by this automated suite.",
    });
    expect(true).toBeTruthy();
  });
});
