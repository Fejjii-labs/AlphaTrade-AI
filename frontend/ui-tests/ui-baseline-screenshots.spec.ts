/**
 * Phase 0 baseline / phase 1 after shots.
 * Offline fixtures only. Run:
 *   UI_BASELINE_PHASE=before PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/google-chrome \
 *     npx playwright test --config playwright.ui-baseline.config.ts
 */
import path from "node:path";

import { expect, test, type Page } from "@playwright/test";

import { makeWatcherMonitoringSnapshot } from "../src/lib/watcher-monitoring-fixtures";

const PHASE = process.env.UI_BASELINE_PHASE === "after" ? "after" : "before";
const OUT_DIR = path.resolve(__dirname, `../../docs/ui-baseline/${PHASE}`);
const timestamp = "2026-10-01T09:00:00Z";

const SCREENS = [
  { route: "/", heading: "Dashboard", file: "dashboard" },
  { route: "/agent", heading: "Agent", file: "agent" },
  { route: "/journal", heading: "Journal & Knowledge", file: "journal" },
  { route: "/strategies", heading: "Strategies", file: "strategies" },
  { route: "/settings", heading: "Settings", file: "settings" },
] as const;

const VIEWPORTS = [
  { name: "1440", width: 1440, height: 900 },
  { name: "375", width: 375, height: 812 },
] as const;

function pageOf(items: unknown[]) {
  return { items, total: items.length, limit: 50, offset: 0 };
}

async function installFixtures(page: Page) {
  await page.context().addCookies([
    { name: "alphatrade_session", value: "1", url: "http://127.0.0.1:3010" },
  ]);
  await page.addInitScript(() => {
    sessionStorage.setItem("alphatrade_access_token", "ui-baseline-fixture-only");
  });
  const safety = {
    execution_mode: "paper",
    real_trading_enabled: false,
    paper_only: true,
  };
  const fixtures: Record<string, unknown> = {
    "/health": {
      ...safety,
      status: "ok",
      provider_mode: "mock",
      exchange_mode: "paper_internal",
      must_verify_email: false,
    },
    "/auth/me": {
      user: { id: "fixture-user", email: "fixture@example.com", email_verified: true },
      organization: { id: "fixture-org", name: "Frontend fixture" },
    },
    "/providers/status": { providers: [] },
    "/risk/kill-switch": { active: false, global_active: false, execution_blocked: false },
    "/risk/settings": {
      max_daily_loss_pct: "2",
      max_risk_per_trade_pct: "0.5",
      max_open_positions: 3,
    },
    "/performance/portfolio": {
      safety,
      account: { current_equity: "10240.12", as_of: timestamp },
      metrics: { trade_count: 3, win_rate: 0.54, net_pnl: "84.20", expectancy: "0.31" },
      breakdowns: { by_strategy: [] },
    },
    "/positions": pageOf([
      {
        id: "fixture-position",
        symbol: "BTCUSDT",
        direction: "long",
        entry_price: "67500",
        unrealized_pnl: "60",
      },
    ]),
    "/journal/entries": pageOf([]),
    "/journal/trades": pageOf([
      {
        id: "fixture-trade",
        symbol: "ETHUSDT",
        direction: "short",
        status: "closed",
        source: "paper",
        entry_time: timestamp,
      },
    ]),
    "/journal/statistics": {
      group_by: "strategy",
      overall: { trade_count: 3, win_rate: 0.54, net_pnl_total: "84.20", expectancy: "0.31" },
      buckets: [],
      total_buckets: 0,
      max_rows: 5000,
      truncated: false,
    },
    "/dashboard/summary": {
      safety,
      daily_discipline: {
        date: "2026-10-01",
        timezone: "UTC",
        paper_trades_opened_today: 1,
        paper_trades_closed_today: 1,
        realized_pnl_today_paper: "84.20",
        remaining_trades_allowed: null,
        discipline_status: "ok",
        recommended_action: "Review the closed paper trade before the next setup.",
        loss_lock_active: false,
        green_day_protection_active: false,
        overtrading_warning_active: false,
        reasons: [],
        limitations: [],
      },
    },
    "/market-watcher/monitoring": makeWatcherMonitoringSnapshot(),
    "/canonical/market-status": { availability: "unavailable", symbol: "BTCUSDT" },
    "/alerts": pageOf([]),
    "/conversations": pageOf([
      { id: "fixture-conversation", title: "Review the paper trade", updated_at: timestamp },
    ]),
    "/agent/saved": pageOf([
      {
        id: "fixture-note",
        title: "Wait for the level to hold",
        summary: "Recorded lesson from a paper trade. Not a live signal.",
        category: "journal",
        revision: 1,
        conversation_id: "fixture-conversation",
        source_message_ids: [],
        source_document_id: null,
        trade_id: null,
        tags: [],
        original_text: "Wait for the level to hold.",
        created_at: timestamp,
        updated_at: timestamp,
        draft: null,
        undone: false,
      },
    ]),
    "/strategies": pageOf([]),
    "/strategy-brain/overview": {
      strategies: [],
      setups: [],
      watched_symbols: [],
      limitations: [],
      paper_only: true,
    },
    "/experiments": pageOf([]),
    "/execution/accounts/paper": { account: null, can_register: true },
    "/notifications/preferences": {
      telegram_enabled: false,
      in_app_enabled: true,
      webhook_enabled: false,
      quiet_hours_enabled: false,
      enabled_alert_types: null,
      min_severity: "medium",
      using_defaults: true,
    },
    "/alerts/delivery-status": {
      telegram_enabled: false,
      channel_statuses: [],
    },
    "/watcher/watchlist": {
      revision: 1,
      slots: [
        { position: 1, symbol: "BTCUSDT", enabled: true },
        { position: 2, symbol: "ETHUSDT", enabled: true },
      ],
    },
    "/watcher/watchlist/status": {
      configuration_revision: 1,
      stale_after_seconds: 90,
      symbols: [],
    },
    "/watcher/assignments": {
      revision: 1,
      symbols: ["BTCUSDT", "ETHUSDT"],
      assignments: [],
      available_versions: [],
    },
  };

  await page.route("http://localhost:8000/**", async (route) => {
    const url = new URL(route.request().url());
    const known = fixtures[url.pathname];
    if (known !== undefined) {
      await route.fulfill({ json: known });
      return;
    }
    if (route.request().method() === "GET") {
      await route.fulfill({ json: pageOf([]) });
      return;
    }
    await route.fulfill({ status: 404, json: { detail: "UI baseline fixture" } });
  });
}

for (const viewport of VIEWPORTS) {
  test(`capture ${PHASE} ${viewport.name}`, async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await installFixtures(page);
    for (const screen of SCREENS) {
      await page.goto(screen.route, { waitUntil: "domcontentloaded" });
      await page.addStyleTag({ content: "nextjs-portal { display: none !important; }" });
      await expect(
        page.getByRole("heading", { level: 1, name: screen.heading, exact: true }),
      ).toBeVisible();
      await expect(
        page.getByRole("status", { name: "Execution status" }).getByText("PAPER", { exact: true }),
      ).toBeVisible();
      await expect(page.getByTestId("kill-switch-button")).toBeVisible();
      await page.evaluate(() => document.fonts.ready);
      await page.screenshot({
        path: path.join(OUT_DIR, `${viewport.name}-${screen.file}.png`),
        fullPage: false,
        animations: "disabled",
        caret: "hide",
      });
    }
    expect(errors, errors.join("\n")).toEqual([]);
  });
}
