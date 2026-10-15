import { agentTurnFixture } from "../src/test/pilot-fixtures";
import { expect, test, type Page } from "@playwright/test";
import { installSmokeSession } from "./helpers/staging-smoke-auth";
import { makeWatcherMonitoringSnapshot } from "../src/lib/watcher-monitoring-fixtures";

// All data and mutations in this spec are synthetic, including Saved receipts.
const tradeId = "00000000-0000-4000-8000-000000000020";
const note = {
  id: "44444444-4444-4444-8444-444444444444",
  category: "rules",
  title: "Wait for the close",
  summary: "I wait for a closed confirmation before entry.",
  original_text: "My rule: wait for a closed confirmation before entry.",
  conversation_id: "11111111-1111-4111-8111-111111111111",
  source_message_ids: ["22222222-2222-4222-8222-222222222222"],
  source_document_id: null,
  trade_id: null,
  tags: ["BTCUSDT"],
  draft: null,
  revision: 1,
  undone: false,
  created_at: "2026-10-09T12:00:00Z",
  updated_at: "2026-10-09T12:00:00Z",
};
const draft = {
  ...note,
  id: "55555555-5555-4555-8555-555555555555",
  category: "strategies",
  title: "BTC SFP confirmation",
  summary:
    "Proposed SFP entry after a reclaim and closed confirmation. Exit rules remain unspecified.",
  draft: {
    family: "SFP",
    market: "BTCUSDT",
    direction: "long",
    timeframe: "1h",
    entry_rules: ["Reclaim the swept level", "Wait for confirmation on close"],
    exit_rules: [],
    invalidation: [],
    missing_fields: ["exit rules"],
  },
};
const trade = {
  id: tradeId,
  execution_lifecycle_id: "fixture-lifecycle",
  symbol: "BTCUSDT",
  direction: "long",
  status: "open",
  result: "open",
  source: "manual_demo_test",
  exchange: "BLOFIN_DEMO",
  size: "0.0001",
  entry_price: "82234.40",
  entry_time: "2026-10-09T11:00:00Z",
  exit_price: null,
  fees: "0.00493406",
  funding: null,
  gross_pnl: null,
  net_pnl: null,
  exit_time: null,
};
const snapshot = {
  venue: "BLOFIN_DEMO",
  read_only: true,
  status: "ok",
  can_refresh: true,
  snapshot_id: "fixture-snapshot",
  synced_at: "2026-10-09T12:00:00Z",
  expires_at: "2099-01-01T00:00:00Z",
  total_equity_usd: "1001.50",
  refresh_error: null,
  last_attempt_at: null,
  balances: [
    {
      asset: "USDT",
      total: "1000.25",
      available: "900.125",
      equity: "1002.25",
    },
  ],
  positions: [],
  balances_truncated: false,
  positions_truncated: false,
  position_count: 0,
  message: "Synthetic account snapshot for layout review.",
  performance: {
    status: "partial",
    currency: "USDT",
    gross_pnl: "0.007656",
    fees: "0.0099",
    funding: null,
    net_pnl: null,
    verified_closed_trades: 1,
    unresolved_trades: 1,
    manual_test_trades: 1,
    strategy_closed_trades: 0,
    coverage:
      "Partial fixture history. Complete account realized returns remain unavailable.",
  },
};
async function fixtures(page: Page) {
  await installSmokeSession(page, "workspace-redesign-fixture-token");
  let stored = { ...note };
  const paused = true;
  const writes: string[] = [];
  const apiOrigin = new URL(
    process.env.PLAYWRIGHT_API_URL ?? "http://localhost:8000",
  ).origin;
  await page.route(
    (url) => url.origin === apiOrigin,
    async (route) => {
      const url = new URL(route.request().url());
      const path = url.pathname;
      const method = route.request().method();
      const send = (json: unknown, status = 200) =>
        route.fulfill({
          json,
          status,
          headers: {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Authorization, Content-Type",
            "Access-Control-Allow-Methods": "GET, POST, PATCH, OPTIONS",
          },
        });
      if (method === "OPTIONS") return send({});
      if (method !== "GET") writes.push(path);
      if (path === "/auth/me")
        return send({
          user: {
            id: "fixture-user",
            email: "fixture@example.com",
            email_verified: true,
            role: "owner",
            is_active: true,
          },
          organization: {
            id: "fixture-org",
            name: "Synthetic review workspace",
            is_active: true,
          },
        });
      if (path === "/health")
        return send({
          status: "ok",
          execution_mode: "paper",
          real_trading_enabled: false,
          must_verify_email: false,
        });
      if (path === "/risk/kill-switch")
        return send({
          organization_id: "fixture-org",
          active: paused,
          global_active: paused,
          execution_blocked: paused,
          scope: "global",
          reason: "Synthetic pause fixture",
          version: 1,
        });
      if (path === "/providers/status") return send({ providers: [] });
      if (path === "/dashboard/demo-account") return send(snapshot);
      if (path === "/execution/manual-demo/commands")
        return send({ items: [], total: 0 });
      if (path === "/execution/accounts/paper")
        return send({ account: null, can_register: true });
      if (path === "/market-watcher/monitoring")
        return send(makeWatcherMonitoringSnapshot());
      if (path === "/watcher/watchlist")
        return send({
          revision: 1,
          slots: [
            { position: 1, symbol: "BTCUSDT", enabled: true },
            { position: 2, symbol: "ETHUSDT", enabled: false },
          ],
          max_enabled: 5,
          paper_only: true,
          updated_at: "2026-10-09T12:00:00Z",
        });
      if (path === "/watcher/watchlist/status")
        return send({
          configuration_revision: 1,
          observed_at: "2026-10-09T12:00:00Z",
          stale_after_seconds: 90,
          paper_only: true,
          real_trading_enabled: false,
          symbols: [],
        });
      if (path === "/notifications/preferences")
        return send({
          in_app_enabled: true,
          webhook_enabled: false,
          telegram_enabled: false,
          min_severity: "warning",
        });
      if (path === "/alerts/delivery-status")
        return send({ delivery_enabled: false, channels: [] });
      if (path === "/risk/settings")
        return send({
          daily_loss_limit: "75",
          max_trades_per_day: 4,
          max_risk_per_trade_percent: "0.75",
          default_account_balance: "1000",
          timezone: "UTC",
          green_day_protection_enabled: true,
          one_loss_stop_enabled: false,
          overtrading_guard_enabled: true,
        });
      if (path === "/strategies")
        return send({ items: [], total: 0, limit: 50, offset: 0 });
      if (path === "/strategy-brain/overview")
        return send({
          strategies: [
            {
              strategy_id: "fixture-sfp",
              version_id: "fixture-v1",
              version: 1,
              status: "draft",
              spec: {
                kind: "swing_failure_pattern/v1",
                symbol: "BTCUSDT",
                direction: "long",
                trigger_timeframe: "1h",
              },
            },
          ],
          setups: [],
        });
      if (path === "/dashboard/attention")
        return send({
          schema_version: "AttentionQueue/v1",
          generated_at: "2026-10-09T12:00:00Z",
          items: [],
          limitations: ["Synthetic data only"],
        });
      if (path === "/dashboard/summary")
        return send({
          safety: { execution_mode: "paper", real_trading_enabled: false },
        });
      if (path === "/journal/trades")
        return send({ items: [trade], total: 1, limit: 50, offset: 0 });
      if (path === `/journal/trades/${tradeId}`)
        return send({
          trade,
          manual_demo: {
            command_id: "fixture-command",
            account_name: "Synthetic demo",
            evidence: {
              filled_quantity: "0.1",
              average_fill_price: "82234.40",
              fees: "0.00493406",
              execution_status: "filled",
              position_status: "unknown",
              account_status: "flat",
              protection: "unverified",
              missing_evidence: [],
            },
          },
          evidence: [],
          rule_checks: [],
          observations: [],
        });
      if (path === "/knowledge/documents")
        return send({
          items: [
            {
              id: "fixture-document",
              title: "Confirmation playbook",
              source_type: "trading_playbook",
              version: 1,
              created_at: "2026-10-09",
              updated_at: "2026-10-09",
              ingestion_metadata: null,
            },
          ],
          total: 1,
          limit: 50,
          offset: 0,
        });
      if (path === "/agent/saved") {
        const category = url.searchParams.get("category");
        const all = stored.undone ? [draft] : [stored, draft];
        const items = all.filter((e) =>
          category
            ? e.category === category
            : url.searchParams.get("view") !== "journal",
        );
        return send({ items, total: items.length });
      }
      if (path === "/agent/saved/44444444-4444-4444-8444-444444444444") {
        if (method === "PATCH") {
          const body = route.request().postDataJSON();
          stored = {
            ...stored,
            revision: stored.revision + 1,
            ...(body.undo
              ? { undone: true }
              : {
                  category: body.category ?? stored.category,
                  summary: body.summary ?? stored.summary,
                }),
          };
        }
        return send(stored);
      }
      if (path === "/conversations")
        return send({
          items: [
            {
              id: "11111111-1111-4111-8111-111111111111",
              title: "Confirmation discussion",
              updated_at: "2026-10-09T12:00:00Z",
            },
          ],
          total: 1,
          limit: 30,
          offset: 0,
        });
      if (path === "/conversations/11111111-1111-4111-8111-111111111111/messages")
        return send({
          items: [
            {
              id: "22222222-2222-4222-8222-222222222222",
              role: "user",
              content: note.original_text,
              created_at: note.created_at,
            },
            {
              id: "33333333-3333-4333-8333-333333333333",
              role: "assistant",
              content: "You want confirmation on close before entry.",
              created_at: note.created_at,
              payload: {
                interactive_agent: {
                  recorded_evidence: "User-supplied note; unverified.",
                  sources: [],
                  capture: {
                    saved_entries: [stored],
                    status: "saved",
                    source_message_id: "22222222-2222-4222-8222-222222222222",
                  },
                },
              },
            },
          ],
          total: 2,
          limit: 100,
          offset: 0,
        });
      if (path === "/agent/turns")
        return send({
          ...agentTurnFixture,
          conversation_id: "11111111-1111-4111-8111-111111111111",
          assistant_message_id: "33333333-3333-4333-8333-333333333333",
          user_message_id: "22222222-2222-4222-8222-222222222222",
          reply: "You want confirmation on close before entry.",
          recorded_evidence: "User-supplied note; unverified.",
          proposals: [],
          saved_entries: [stored],
          capture_status: "saved",
          capture_source_message_id: "22222222-2222-4222-8222-222222222222",
          operation: "read",
          capability: "general_conversation",
          authority_mutated: false,
          execution_attempted: false,
          real_trading_enabled: false,
        });
      return send(
        { detail: "This synthetic fixture has no data for this source." },
        503,
      );
    },
  );
  return writes;
}
for (const width of [1280, 390]) {
  test(`five destinations, receipts, return paths and portal dialogs at ${width}px`, async ({
    page,
  }) => {
    const writes = await fixtures(page);
    await page.setViewportSize({ width, height: 900 });
    const screenshot = async (name: string) => {
      // Expand the capture viewport so fixed mobile navigation sits at the image bottom.
      // Interaction checks remain at 390x900; this only affects full-page artifacts.
      if (width < 1024)
        await page.setViewportSize({
          width,
          height: await page.evaluate(() =>
            Math.max(900, document.documentElement.scrollHeight),
          ),
        });
      await page.screenshot({
        path: `../docs/screenshots/workspace-redesign/fixture-${name}-${width}.png`,
        fullPage: true,
      });
      await page.setViewportSize({ width, height: 900 });
    };
    const noOverflow = async () =>
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
      ).toBe(true);
    await page.goto("/");
    await expect(page.getByTestId("dashboard-equity")).toContainText(
      "1,001.50 USD",
    );
    await expect(
      page.locator(
        'nav[aria-label="Primary destinations"]:visible a, nav[aria-label="Primary mobile"]:visible a',
      ),
    ).toHaveCount(5);
    await expect(page.getByTestId("dashboard-win-rate")).toContainText("—");
    await screenshot("dashboard");
    await noOverflow();
    const pause = page.getByTestId("kill-switch-button");
    await pause.click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    expect(
      await dialog.evaluate(
        (el) => el.parentElement?.parentElement === document.body,
      ),
    ).toBe(true);
    const bounds = await dialog.boundingBox();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
    expect(bounds!.y).toBeGreaterThanOrEqual(0);
    expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(900);
    await screenshot("pause-dialog");
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(pause).toBeFocused();
    await page.goto("/agent");
    await expect(
      page.getByRole("button", { name: "History", exact: true }),
    ).toHaveAttribute("aria-expanded", "false");
    await page.getByRole("button", { name: "History", exact: true }).click();
    if (width < 1024) {
      await page.keyboard.press("Escape");
      await expect(
        page.getByRole("button", { name: "History", exact: true }),
      ).toBeFocused();
      await page.getByRole("button", { name: "History", exact: true }).click();
    }
    await page.getByRole("button", { name: "Confirmation discussion" }).click();
    await expect(page.getByTestId("saved-receipt")).toContainText(
      "Saved to Rules",
    );
    await page.getByLabel("Message", { exact: true }).fill(note.original_text);
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await expect(page.getByLabel("Message", { exact: true })).toHaveValue("");
    await screenshot("agent");
    await noOverflow();
    await page.getByRole("link", { name: "Open", exact: true }).first().click();
    await expect(
      page.getByRole("heading", { name: "Wait for the close" }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Original conversation" }),
    ).toHaveAttribute("href", "/agent?conversation=11111111-1111-4111-8111-111111111111");
    await page.goto("/journal?tab=knowledge&category=rules&q=close");
    await expect(page.getByLabel("Knowledge category")).toHaveValue("rules");
    await screenshot("knowledge");
    await noOverflow();
    await page.getByRole("link", { name: "Open", exact: true }).click();
    await page.getByRole("link", { name: /Back to Journal/ }).click();
    await expect(page).toHaveURL(/category=rules&q=close/);
    await page.goto("/journal");
    await expect(
      page.getByRole("link", { name: "Open exact trade" }),
    ).toBeVisible();
    await screenshot("journal-list");
    await page.getByRole("link", { name: "Open exact trade" }).click();
    await expect(page.getByTestId("journal-trade-detail")).toContainText(
      "82,234.40 USDT",
    );
    await screenshot("journal");
    await noOverflow();
    await page.goto("/strategies");
    await expect(page.getByTestId("strategy-family")).toHaveCount(2);
    await expect(page.getByText("BTC SFP confirmation")).toBeVisible();
    await screenshot("strategies");
    await noOverflow();
    await page
      .getByText("SFP", { exact: true })
      .locator("..")
      .getByRole("link", { name: "Open", exact: true })
      .click();
    await expect(page.getByText("BTCUSDT · long · 1h")).toBeVisible();
    await page.getByRole("link", { name: /Back to Strategies/ }).click();
    await page.getByRole("link", { name: "Recent detections →" }).click();
    await expect(
      page.getByText("Market observations · Last 7 days"),
    ).toBeVisible();
    await page.goto("/settings");
    await expect(
      page.locator('[data-testid="settings-workspace"] > details'),
    ).toHaveCount(3);
    await page.getByText("Account & System", { exact: true }).click();
    await expect(page.getByRole("link", { name: "Diagnostics" })).toBeVisible();
    await screenshot("settings");
    await noOverflow();
    expect(writes).toEqual(["/agent/turns"]);
  });
}
