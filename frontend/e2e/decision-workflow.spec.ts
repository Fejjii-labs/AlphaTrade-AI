import { expect, test } from "@playwright/test";

import { getSharedE2EAccessToken, installSharedE2ESession, paperModeActive } from "./helpers/shared-e2e-auth";

async function hasHorizontalOverflow(page: import("@playwright/test").Page): Promise<boolean> {
  return page.evaluate(() => {
    const root = document.documentElement;
    return root.scrollWidth > root.clientWidth + 1;
  });
}

test.describe("Canonical paper decision workflow", () => {
  test("unauthenticated /decision redirects to login", async ({ page }) => {
    await page.goto("/decision");
    await expect(page).toHaveURL(/\/login\?next=%2Fdecision/);
  });

  test("hub, market, candidates, strategy: paper-only, one h1, no live execution", async ({
    page,
    request,
  }) => {
    await installSharedE2ESession(page, request);

    for (const route of ["/decision", "/decision/market", "/decision/candidates", "/decision/strategy"]) {
      await page.goto(route);
      await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
      expect(await page.getByRole("heading", { level: 1 }).count()).toBe(1);
      await expect(paperModeActive(page)).toBeVisible();
      await expect(page.getByTestId("decision-safety-rail")).toBeVisible();
      await expect(page.getByTestId("decision-human-approval-copy")).toBeVisible();
      await expect(page.getByTestId("decision-stepper")).toBeVisible();
      await expect(page.getByRole("button", { name: /place real order/i })).toHaveCount(0);
      await expect(page.getByRole("button", { name: /execute live/i })).toHaveCount(0);
    }

    await page.goto("/decision/strategy");
    await expect(page.getByTestId("paper-evaluation-summary")).toBeVisible();
    await expect(page.getByTestId("paper-evaluation-summary")).toContainText(
      /refinements are suggestions/i,
    );

    await page.goto("/decision");
    await expect(page.getByRole("link", { name: /legacy ai assist/i })).toBeVisible();
    await expect(page.getByRole("link", { name: /legacy approvals/i })).toBeVisible();
    await expect(page.getByText(/canonical candidate http is unbound/i)).toHaveCount(0);

    await page.goto("/decision/market");
    await expect(page.getByTestId("canonical-first-slice-copy")).toBeVisible();
    const price = page.getByTestId("canonical-current-price");
    await expect(price).toBeVisible();
    await expect(price).toContainText(/replay fixture/i);
    await expect(price.getByText("Live", { exact: true })).toHaveCount(0);
    const monitor = page.getByTestId("perpetual-market-status");
    await expect(monitor).toBeVisible();
    await expect(monitor).toContainText(/replay fixture/i);
    await expect(monitor.getByText("Live mark", { exact: true })).toHaveCount(0);
  });

  test("canonical HTTP reads require auth and stay tenant-empty without seed", async ({
    request,
  }) => {
    const apiURL = process.env.PLAYWRIGHT_API_URL ?? "http://localhost:8000";
    const unauth = await request.get(`${apiURL}/canonical/candidates`);
    expect(unauth.status()).toBe(401);

    const health = await request.get(`${apiURL}/health`);
    expect(health.ok()).toBeTruthy();
    const healthBody = (await health.json()) as {
      execution_mode?: string;
      real_trading_enabled?: boolean;
      market_watcher_enabled?: boolean;
      telegram_alerts_enabled?: boolean;
      telegram_interaction_enabled?: boolean;
    };
    expect(healthBody.execution_mode).toBe("paper");
    expect(healthBody.real_trading_enabled).toBe(false);
    if (healthBody.market_watcher_enabled !== undefined) {
      expect(healthBody.market_watcher_enabled).toBe(false);
    }
    if (healthBody.telegram_alerts_enabled !== undefined) {
      expect(healthBody.telegram_alerts_enabled).toBe(false);
    }
    if (healthBody.telegram_interaction_enabled !== undefined) {
      expect(healthBody.telegram_interaction_enabled).toBe(false);
    }

    const token = await getSharedE2EAccessToken(request);
    const candidates = await request.get(`${apiURL}/canonical/candidates`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(candidates.ok(), `canonical candidates HTTP ${candidates.status()}`).toBeTruthy();
    const listed = (await candidates.json()) as { total: number };
    expect(listed.total).toBe(0);

    const stats = await request.get(`${apiURL}/canonical/learning/strategy-stats`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(stats.ok(), `strategy stats HTTP ${stats.status()}`).toBeTruthy();

    const evaluation = await request.get(`${apiURL}/canonical/paper-evaluation/summary`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(evaluation.ok(), `paper evaluation HTTP ${evaluation.status()}`).toBeTruthy();
    const evaluationBody = (await evaluation.json()) as {
      watcher_activated?: boolean;
      live_executable?: boolean;
      summary?: {
        authority?: string;
        live_executable?: boolean;
        facts?: {
          watcher_orchestration_enabled?: boolean;
          telegram_interaction_enabled?: boolean;
          missed_opportunities?: { counterfactual_pnl?: unknown };
        };
        refinements?: Array<{ activate?: boolean; auto_activate?: boolean }>;
      };
    };
    expect(evaluationBody.watcher_activated).toBe(false);
    expect(evaluationBody.live_executable).toBe(false);
    expect(evaluationBody.summary?.authority).toBe("paper_evaluation_measurement");
    expect(evaluationBody.summary?.live_executable).toBe(false);
    expect(evaluationBody.summary?.facts?.watcher_orchestration_enabled).toBe(false);
    expect(evaluationBody.summary?.facts?.telegram_interaction_enabled).toBe(false);
    expect(evaluationBody.summary?.facts?.missed_opportunities?.counterfactual_pnl).toBeNull();
    for (const item of evaluationBody.summary?.refinements ?? []) {
      expect(item.activate).toBe(false);
      expect(item.auto_activate).toBe(false);
    }

    const evidence = await request.get(`${apiURL}/canonical/evidence`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(evidence.ok(), `canonical evidence HTTP ${evidence.status()}`).toBeTruthy();
    const body = (await evidence.json()) as {
      watcher_activated?: boolean;
      live_executable?: boolean;
      current_price?: {
        usable_as_current_market_price?: boolean;
        presentation?: string;
        is_live?: boolean;
      };
      source?: { source_family?: string };
    };
    expect(body.watcher_activated).toBe(false);
    expect(body.live_executable).toBe(false);
    expect(body.current_price?.usable_as_current_market_price).toBe(false);
    expect(body.current_price?.presentation).not.toBe("live_mark");
    expect(body.current_price?.is_live).toBe(false);
    expect(body.source?.source_family).toBe("replay_fixture");

    const status = await request.get(`${apiURL}/canonical/market-status`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(status.ok(), `canonical market-status HTTP ${status.status()}`).toBeTruthy();
    const monitor = (await status.json()) as {
      watcher_activated?: boolean;
      live_executable?: boolean;
      compatibility_price_used?: boolean;
      availability?: string;
      current_price?: {
        usable_as_current_market_price?: boolean;
        presentation?: string;
        is_live?: boolean;
      };
    };
    expect(monitor.watcher_activated).toBe(false);
    expect(monitor.live_executable).toBe(false);
    expect(monitor.compatibility_price_used).toBe(false);
    expect(monitor.availability).toBe("replay");
    expect(monitor.current_price?.usable_as_current_market_price).toBe(false);
    expect(monitor.current_price?.presentation).not.toBe("live_mark");
    expect(monitor.current_price?.is_live).toBe(false);
  });

  test("iPhone-width decision hub does not overflow and keeps trader tabs", async ({
    page,
    request,
  }) => {
    await installSharedE2ESession(page, request);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/decision");
    await expect(page.getByRole("heading", { level: 1, name: "Decision" })).toBeVisible();
    expect(await hasHorizontalOverflow(page)).toBeFalsy();
    const nav = page.getByTestId("mobile-bottom-navigation");
    await expect(nav.getByRole("link", { name: "Dashboard" })).toHaveAttribute("href", "/");
    await expect(nav.getByRole("link", { name: "Agent" })).toHaveAttribute("href", "/agent");
    await expect(nav.getByRole("link", { name: "Strategies" })).toHaveAttribute("href", "/strategies");
    await expect(nav.getByRole("link", { name: "Trades" })).toHaveAttribute("href", "/journal");
    await expect(nav.getByRole("link", { name: "Settings" })).toHaveAttribute("href", "/settings");
    await expect(nav.getByRole("link", { name: "Settings" })).toHaveAttribute("aria-current", "page");
    await expect(page.getByRole("banner").getByTestId("kill-switch-button")).toBeVisible();
    await expect(
      page.getByTestId("decision-safety-rail").getByTestId("kill-switch-button"),
    ).toBeVisible();
  });
});
