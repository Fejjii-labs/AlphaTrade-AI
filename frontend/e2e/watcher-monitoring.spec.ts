import { expect, test, type Page } from "@playwright/test";

import {
  getSharedE2EAccessToken,
  installSharedE2ESession,
  paperModeActive,
} from "./helpers/shared-e2e-auth";

const API_URL = process.env.PLAYWRIGHT_API_URL ?? "http://localhost:8000";

const MONITOR_ROUTES = ["/watcher", "/market-watcher", "/decision", "/strategy-lab"] as const;

async function hasHorizontalOverflow(page: Page): Promise<boolean> {
  return page.evaluate(() => {
    const root = document.documentElement;
    return root.scrollWidth > root.clientWidth + 1;
  });
}

test.describe("Watcher paper monitoring", () => {
  test("unauthenticated monitoring is 401", async ({ request }) => {
    const response = await request.get(`${API_URL}/market-watcher/monitoring`);
    expect(response.status()).toBe(401);
  });

  test("API snapshot is STOPPED, paper-only, and does not fabricate activity", async ({
    request,
  }) => {
    const token = await getSharedE2EAccessToken(request);
    const headers = { Authorization: `Bearer ${token}` };
    const response = await request.get(`${API_URL}/market-watcher/monitoring`, { headers });
    expect(response.ok()).toBeTruthy();
    const body = (await response.json()) as {
      watcher_status: string;
      paper_monitoring_status: string;
      paper_only: boolean;
      next_scan_at: string | null;
      scanner_candidates: { count: number };
      setup_assessments: unknown[];
      canonical_candidates: unknown[];
      paper_posture: {
        paper_only: boolean;
        real_trading_enabled: boolean;
        runtime_evidence: boolean;
        telegram_enabled: boolean;
      };
      config_flags: {
        market_watcher_enabled: boolean;
        watcher_orchestration_enabled: boolean;
        telegram_alerts_enabled: boolean;
      };
    };
    expect(body.watcher_status).toBe("STOPPED");
    expect(body.paper_monitoring_status).toBe("STOPPED");
    expect(body.paper_only).toBe(true);
    expect(body.paper_posture.paper_only).toBe(true);
    expect(body.paper_posture.real_trading_enabled).toBe(false);
    expect(body.paper_posture.runtime_evidence).toBe(false);
    expect(body.paper_posture.telegram_enabled).toBe(false);
    expect(body.config_flags.market_watcher_enabled).toBe(false);
    expect(body.config_flags.watcher_orchestration_enabled).toBe(false);
    expect(body.config_flags.telegram_alerts_enabled).toBe(false);
    expect(body.next_scan_at).toBeNull();
    expect(body.scanner_candidates.count).toBe(0);
    expect(body.setup_assessments).toEqual([]);
    expect(body.canonical_candidates).toEqual([]);

    const health = await request.get(`${API_URL}/health`);
    expect(health.ok()).toBeTruthy();
    const healthBody = (await health.json()) as {
      execution_mode?: string;
      real_trading_enabled?: boolean;
      market_watcher_enabled?: boolean;
      watcher_orchestration_enabled?: boolean;
      telegram_alerts_enabled?: boolean;
    };
    expect(healthBody.execution_mode).toBe("paper");
    expect(healthBody.real_trading_enabled).toBe(false);
    expect(healthBody.market_watcher_enabled).toBe(false);
    if (healthBody.watcher_orchestration_enabled !== undefined) {
      expect(healthBody.watcher_orchestration_enabled).toBe(false);
    }
    if (healthBody.telegram_alerts_enabled !== undefined) {
      expect(healthBody.telegram_alerts_enabled).toBe(false);
    }
  });

  test("Dashboard, Watcher, Plan, and Strategy Lab show runtime STOPPED paper monitoring", async ({
    page,
    request,
  }) => {
    await installSharedE2ESession(page, request);

    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
    await expect(paperModeActive(page)).toBeVisible();
    await page.getByRole("radiogroup", { name: "Dashboard venue" }).getByRole("radio", { name: "Paper" }).click();
    await page.getByText("Simulator daily status & monitoring", { exact: true }).click();
    await expect(page.getByTestId("dashboard-watcher-status")).toContainText("Stopped");
    await expect(page.getByTestId("watcher-monitoring-card")).toHaveCount(0);
    await expect(page.getByRole("button", { name: /place real order/i })).toHaveCount(0);

    for (const route of MONITOR_ROUTES) {
      await page.goto(route);
      await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
      await expect(paperModeActive(page)).toBeVisible();
      const card = page.getByTestId("watcher-monitoring-card");
      await expect(card).toBeVisible();
      await expect(page.getByTestId("watcher-monitoring-status-row")).toContainText("STOPPED");
      await expect(page.getByTestId("watcher-monitoring-paper-only")).toContainText("Paper only");
      await expect(page.getByTestId("watcher-monitoring-real-trading")).toContainText(
        "Real trading OFF",
      );
      await expect(page.getByTestId("watcher-monitoring-status-row")).not.toContainText("RUNNING");
      await expect(page.getByTestId("watcher-monitoring-candidates")).toContainText("None");
      await expect(page.getByRole("button", { name: /place real order/i })).toHaveCount(0);
      await expect(page.getByRole("button", { name: /execute live/i })).toHaveCount(0);
    }

    await page.goto("/watcher");
    await page.getByTestId("watcher-monitoring-refresh").click();
    await expect(page.getByTestId("watcher-monitoring-card")).toBeVisible();
    await expect(page.getByTestId("watcher-monitoring-status-row")).toContainText("STOPPED");
  });

  test("mobile layout keeps monitoring readable without overflow", async ({ page, request }) => {
    await installSharedE2ESession(page, request);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/watcher");
    await expect(page.getByTestId("watcher-monitoring-card")).toBeVisible();
    await expect(page.getByTestId("watcher-monitoring-status-row")).toContainText("STOPPED");
    expect(await hasHorizontalOverflow(page)).toBeFalsy();
  });
});
