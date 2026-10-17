import { agentTurnFixture } from "../src/test/pilot-fixtures";
import { expect, test, type Page } from "@playwright/test";
import { installSmokeSession } from "./helpers/staging-smoke-auth";

// Synthetic browser fixtures only: no native exchange or authenticated account verification.
const command = "00000000-0000-4000-8000-000000000001";
const tradeId = "00000000-0000-4000-8000-000000000002";
const attempt = {
  command_id: command, account_id: "fixture-account", account_name: "Fixture BloFin demo", venue: "BLOFIN_DEMO", origin: "manual_demo_test",
  attempted_at: "2026-10-08T12:56:20Z", submitted_at: "2026-10-08T12:56:23Z", symbol: "BTCUSDT", side: "BUY", requested_contracts: "0.1", base_quantity: "0.0001", stop: "82000", target: "83000", content_hash: "fixture-evidence-hash", submission_outcome: "ALLOW", blocked_reason: null,
  evidence: { origin: "manual demo test", revision_id: "fixture-plan", command_id: command, client_order_id: "fixture-client", venue_order_id: "fixture-native-order", status: "filled", filled_quantity: "0.1", remaining_quantity: "0E-8", average_fill_price: "82234.40", fees: "0.00493406", entry_fees: "0.00493406", protection: "unverified", journal_trade_id: tradeId, missing_evidence: ["Exact exit identity remains unverified. Current account flatness does not prove closure."], execution_status: "filled", position_status: "unknown", account_status: "flat", historical_protection: "configured", triggered_protection: "unverified", exit_quantity: "0", exit_price: null, exit_fees: null, gross_pnl: null, funding: null, net_pnl: null, reconciliation_freshness: "latest_read_failed", observed_at: "2026-10-09T08:00:00Z", can_reconcile: true, can_resolve: false, protection_history: [{ tpsl_id: "fixture-protection", state: "canceled" }] },
};
async function fixture(page: Page) {
  await installSmokeSession(page, "blofin-repair-fixture-token");
  const writes: string[] = [];
  const observations: { id: string; observation: string; category: string; created_at: string }[] = [];
  const origin = new URL(process.env.PLAYWRIGHT_API_URL ?? "http://localhost:8000").origin;
  await page.route((url) => url.origin === origin, async (route) => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    if (method !== "GET") writes.push(path);
    if (path === "/risk/kill-switch") return route.fulfill({ json: { organization_id: "fixture-org", active: true, global_active: true, execution_blocked: true, reason: "Fixture historical reconciliation hold", scope: "global", version: 1 } });
    if (path === "/providers/status") return route.fulfill({ json: { providers: [] } });
    if (path === "/health") return route.fulfill({ json: { execution_mode: "paper", real_trading_enabled: false, must_verify_email: false } });
    if (path === "/auth/me") return route.fulfill({ json: { user: { id: "fixture-user", email: "fixture@example.com", email_verified: true, role: "owner", is_active: true }, organization: { id: "fixture-org", name: "Fixture", is_active: true } } });
    if (path === `/execution/manual-demo/commands/${command}`) return route.fulfill({ json: attempt });
    if (path === `/execution/manual-demo/${command}/reconcile`) return route.fulfill({ json: attempt.evidence });
    if (path === `/journal/trades/${tradeId}`) return route.fulfill({ json: { trade: { id: tradeId, symbol: "BTCUSDT", direction: "long", status: "open", result: "open", source: "manual_demo_test", exchange: "BLOFIN_DEMO", size: "0.0001", entry_price: "82234.40", entry_time: "2026-10-08T12:56:27Z", exit_price: null, fees: "0.00493406", funding: null, gross_pnl: null, net_pnl: null, exit_time: null }, manual_demo: attempt, evidence: [], rule_checks: [], observations } });
    if (path === `/journal/trades/${tradeId}/observations`) {
      const body = route.request().postDataJSON();
      expect(body).toEqual({ category: "behavioral", observation: "Keep the planned stop and review verified fills.", emotion_tags: [] });
      const note = { id: "fixture-reflection", observation: body.observation, category: body.category, created_at: "2026-10-09T09:00:00Z" };
      observations.push(note);
      return route.fulfill({ json: note, status: 201 });
    }
    if (path === "/agent/turns") return route.fulfill({ json: { ...agentTurnFixture, reply: "BTCUSDT long · BloFin demo. Entry verified: 0.1 contracts at 82,234.40 USDT. Exit remains unverified. Open this attempt’s Journal detail.", recorded_evidence: `Command ${command}; order fixture-native-order; no verified closure.`, connections: [{ artifact_kind: "journal_entry", provenance: "trade_outcome", relation: "recorded trade lineage", title: "Journal", record_id: tradeId }] } });
    return route.fulfill({ status: 404, json: { detail: "Fixture record unavailable in this organization" } });
  });
  return writes;
}
for (const width of [1280, 390]) {
  test(`exact Journal, reflection and concise Agent at ${width}px`, async ({ page }) => {
    const writes = await fixture(page);
    await page.setViewportSize({ width, height: 900 });
    await page.goto(`/execution/manual-demo/${command}`);
    await expect(page.getByText("0.1 requested contracts = 0.0001 BTC")).toBeVisible();
    const activeNav = page.locator('a[data-destination][aria-current="page"]:visible');
    await expect(activeNav).toHaveCount(1);
    await expect(activeNav).toHaveAttribute("data-destination", "settings");
    await expect(page.getByText("Stored identities and history").locator("..")).not.toHaveAttribute("open");
    await expect(page.getByRole("button", { name: "Resolve verified lifecycle" })).toHaveCount(0);
    await page.getByRole("button", { name: "Ask Agent about this exact attempt" }).click();
    const explanation = page.getByRole("region", { name: "Agent explanation" });
    await expect(explanation).toContainText("Entry verified: 0.1 contracts");
    await expect(explanation).not.toContainText("Recorded facts");
    await expect(explanation.locator("details")).not.toHaveAttribute("open");
    await expect(explanation.getByRole("link", { name: "Open Journal detail" })).toHaveAttribute("href", `/journal?trade_id=${tradeId}`);
    await expect(page.getByTestId("manual-demo-detail")).not.toContainText("0E-8");
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)).toBe(false);
    await page.screenshot({ path: `../docs/screenshots/blofin-repair/fixture-manual-agent-${width}.png`, fullPage: true });
    await page.getByRole("link", { name: "Open this attempt’s Journal projection" }).click();
    await expect(page.getByTestId("journal-trade-detail")).toBeVisible();
    await expect(activeNav).toHaveCount(1);
    await expect(activeNav).toHaveAttribute("data-destination", "journal");
    await expect(page.getByText("BLOFIN_DEMO")).toBeVisible();
    await expect(page.getByText("0.00493406 USDT")).toBeVisible();
    await expect(page.getByText("0.0001 BTC")).toBeVisible();
    await expect(page.getByText(/Canonical journal trade deep link/)).toHaveCount(0);
    await page.getByLabel("What would you repeat or change?").fill("Keep the planned stop and review verified fills.");
    await page.getByRole("button", { name: "Attach reflection" }).click();
    await expect(page.getByText("Reflection attached to this trade.")).toBeVisible();
    await expect(page.getByText("Keep the planned stop and review verified fills.")).toBeVisible();
    await expect(page.getByText("82,234.40 USDT")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)).toBe(false);
    await page.screenshot({ path: `../docs/screenshots/blofin-repair/fixture-journal-${width}.png`, fullPage: true });
    expect(writes).toEqual(["/agent/turns", `/journal/trades/${tradeId}/observations`]);
    await page.goto("/journal?trade=foreign-trade");
    await expect(page.getByTestId("journal-trade-detail")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Attach reflection" })).toHaveCount(0);
  });
}
