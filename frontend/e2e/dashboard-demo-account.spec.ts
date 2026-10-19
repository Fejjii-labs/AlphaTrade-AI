import { expect, test } from "@playwright/test";

import { DEMO_REFRESH_INTERVAL_MS } from "../src/components/dashboard/demoAccountRefresh";

// Browser fixtures prove presentation and request behavior; they are not venue evidence.
for (const width of [1280, 390]) {
  test(`configured demo account is primary and refresh preserves evidence at ${width}px`, async ({
    page, baseURL,
  }) => {
    let reads = 0;
    let refreshes = 0;
    let authenticated = false;
    let failRefresh = false;
    let clearPositions = false;
    let fixtureNow = Date.now();
    await page.clock.install({ time: new Date(fixtureNow) });
    const snapshot = {
      venue: "BLOFIN_DEMO",
      read_only: true,
      status: "ok",
      can_refresh: true,
      snapshot_id: "browser-fixture",
      synced_at: new Date().toISOString(),
      expires_at: new Date(Date.now() + 300_000).toISOString(),
      total_equity_usd: "1001.50",
      refresh_error: null,
      last_attempt_at: null,
      balances: [{ asset: "USDT", total: "1000.25", available: "900.125", equity: "1002.25" }],
      positions: [
        {
          symbol: "BTCUSDT",
          side: "long",
          contracts: "0.1",
          base_asset: "BTC",
          quote_asset: "USDT",
          base_quantity: "0.0001",
          entry_price: "82894",
          mark_price: "82895",
          unrealized_pnl: null,
          leverage: "1",
        },
      ],
      balances_truncated: false,
      positions_truncated: false,
      position_count: 1,
      message: "Fixture native demo account snapshot.",
      performance: { status: "partial", currency: "USDT", gross_pnl: "0.007656", fees: "0.0099", funding: null, net_pnl: null, verified_closed_trades: 1, unresolved_trades: 1, manual_test_trades: 2, strategy_closed_trades: 0, coverage: "Partial fixture history: only exact linked account activity. Outside venue activity unavailable." },
    };
    await page
      .context()
      .addCookies([
        {
          name: "alphatrade_session",
          value: "1",
          url: baseURL!,
        },
      ]);
    await page.addInitScript(() =>
      sessionStorage.setItem(
        "alphatrade_access_token",
        "browser-fixture-token",
      ),
    );
    await page.route("**/*", async (route) => {
      const url = new URL(route.request().url());
      if (url.origin === new URL(baseURL!).origin) return route.continue();
      const path = url.pathname;
      let body: unknown;
      if (path === "/health") {
        body = {
          execution_mode: "paper",
          real_trading_enabled: false,
          must_verify_email: false,
        };
      } else if (path === "/risk/kill-switch") {
        body = { organization_id: "fixture-org", active: true, global_active: true, execution_blocked: true, reason: "Fixture historical reconciliation hold", scope: "global", version: 1 };
      } else if (path === "/auth/me") {
        expect(route.request().headers().authorization).toBe("Bearer browser-fixture-token");
        authenticated = true;
        body = {
          user: {
            id: "fixture-user",
            email: "fixture@example.com",
            email_verified: true,
            role: "owner",
          },
          organization: { id: "fixture-org", name: "Browser fixture" },
        };
      } else if (path === "/dashboard/demo-account") {
        expect(route.request().method()).toBe("GET");
        expect(route.request().headers().authorization).toBe("Bearer browser-fixture-token");
        reads += 1;
        body = snapshot;
      } else if (path === "/dashboard/demo-account/refresh") {
        expect(route.request().method()).toBe("POST");
        expect(route.request().headers().authorization).toBe("Bearer browser-fixture-token");
        refreshes += 1;
        if (failRefresh) return route.fulfill({
          status: 503, json: { detail: "Fixture venue unavailable" },
          headers: { "Access-Control-Allow-Origin": "*" },
        });
        body = {
          ...snapshot,
          ...(clearPositions ? { positions: [], position_count: 0 } : {}),
          synced_at: new Date(fixtureNow).toISOString(),
          expires_at: new Date(fixtureNow + 300_000).toISOString(),
        };
      } else if (path === "/dashboard/summary") {
        body = {
          safety: { execution_mode: "paper", real_trading_enabled: false },
          open_paper_trades_summary: { total_count: 0, items: [] },
        };
      } else if (path === "/performance/portfolio") {
        body = {
          account: { current_equity: "500.50" },
          metrics: { trade_count: 0, net_pnl: "12.50" },
          breakdowns: { by_strategy: [] },
        };
      } else if (path === "/providers/status") {
        body = { providers: [] };
      } else {
        return route.fulfill({
          status: 503,
          json: { detail: "Browser fixture source unavailable" },
          headers: { "Access-Control-Allow-Origin": "*" },
        });
      }
      return route.fulfill({
        json: body,
        headers: { "Access-Control-Allow-Origin": "*" },
      });
    });
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/");
    await expect(page).toHaveURL(new URL("/", baseURL!).href);
    const card = page.getByTestId("dashboard-demo-account");
    await expect(card).toContainText("0.1 contracts");
    await expect(card).toContainText("Unrealized PnL —");
    expect(authenticated).toBe(true);
    await expect.poll(() => refreshes).toBe(1);
    expect(reads).toBeGreaterThan(0);
    await expect(card).toContainText("1,001.50 USD");
    await expect(card).toContainText("Equity: 1,002.25 USDT");
    await expect(card).toContainText("Base quantity: 0.0001 BTC");
    expect(refreshes).toBe(1);
    await page.screenshot({ path: `../docs/screenshots/blofin-repair/fixture-dashboard-${width}.png`, fullPage: true });
    const initialReads = reads;
    const savedRead = page.waitForResponse((response) =>
      new URL(response.url()).pathname === "/dashboard/demo-account" &&
      response.request().method() === "GET",
    );
    await page.getByRole("button", { name: "Refresh", exact: true }).click();
    await (await savedRead).finished();
    await expect.poll(() => reads).toBeGreaterThan(initialReads);
    // A request counter proves dispatch, not completion. Let the saved read
    // settle so single-flight can schedule its native poll before time advances.
    await expect(card.getByRole("button", { name: "Refresh demo account", exact: true })).toBeEnabled();
    expect(refreshes).toBe(1);
    // Exercise real browser timers and transport errors; values must survive.
    failRefresh = true;
    fixtureNow += DEMO_REFRESH_INTERVAL_MS;
    await page.clock.fastForward(DEMO_REFRESH_INTERVAL_MS);
    await expect(card.getByRole("alert")).toContainText("refresh failed");
    await expect(card).toContainText("Stale snapshot");
    await expect(card).toContainText("0.1 contracts");
    expect(refreshes).toBe(2);
    fixtureNow += DEMO_REFRESH_INTERVAL_MS;
    await page.clock.fastForward(DEMO_REFRESH_INTERVAL_MS);
    expect(refreshes).toBe(2); // Failure doubles the automatic retry interval.
    failRefresh = false;
    clearPositions = true;
    await card.getByRole("button", { name: "Refresh demo account" }).click();
    await expect(card).toContainText(
      "No native open positions at this snapshot.",
    );
    expect(refreshes).toBe(3);
    await expect(card).toContainText("Fresh snapshot");
    await expect(card.getByRole("alert")).toHaveCount(0);
    fixtureNow += DEMO_REFRESH_INTERVAL_MS;
    await page.clock.fastForward(DEMO_REFRESH_INTERVAL_MS);
    await expect.poll(() => refreshes).toBe(4);
    await expect(page.getByTestId("dashboard-equity")).toContainText("1,001.50 USD");
    await expect(page.getByTestId("dashboard-pnl")).toContainText("— USDT");
    await expect(page.getByTestId("dashboard-open-count")).toContainText("0");
    await expect(page.getByText("Paper portfolio", { exact: true })).toHaveCount(0);
    await page.getByLabel("Dashboard account").selectOption("simulator");
    await expect(page.getByTestId("dashboard-equity")).toContainText("500.50");
    await expect(page.getByTestId("dashboard-demo-account")).toHaveCount(0);
    await page.getByLabel("Dashboard account").selectOption("blofin");
    await expect(page.getByTestId("dashboard-equity")).toContainText("1,001.50 USD");
    const overflow = await page.evaluate(
      () =>
        document.documentElement.scrollWidth >
        document.documentElement.clientWidth + 1,
    );
    expect(overflow).toBe(false);
    await page.screenshot({
      path: `../docs/screenshots/blofin-repair/fixture-dashboard-stale-${width}.png`,
      fullPage: true,
    });
  });
}
