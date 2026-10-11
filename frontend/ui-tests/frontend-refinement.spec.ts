import { expect, test, type Page } from "@playwright/test";
import path from "node:path";
import { agentTurnFixture, FIXTURE_UUID } from "../src/test/pilot-fixtures";
import { makeWatcherMonitoringSnapshot } from "../src/lib/watcher-monitoring-fixtures";

const versionId = "22222222-2222-4222-8222-222222222222";
const assignmentId = "33333333-3333-4333-8333-333333333333";
const now = new Date().toISOString();
const note = {
  id: FIXTURE_UUID, category: "rules", title: "Wait for confirmation",
  summary: "Wait for the candle to close before acting on a reclaim.",
  original_text: "My rule: wait for a candle close before acting on a reclaim.",
  conversation_id: FIXTURE_UUID, source_message_ids: [versionId], source_document_id: null,
  trade_id: null, tags: [], draft: null, revision: 1, undone: false, created_at: now, updated_at: now,
};
const pageOf = (items: unknown[]) => ({ items, total: items.length, limit: 50, offset: 0 });

async function fixtures(page: Page) {
  const requests: string[] = [];
  const forbidden: string[] = [];
  await page.context().addCookies([{ name: "alphatrade_session", value: "1", url: "http://127.0.0.1:3000" }]);
  await page.addInitScript(() => sessionStorage.setItem("alphatrade_access_token", "frontend-fixture-only"));
  const version = {
    strategy_id: FIXTURE_UUID, strategy_version_id: versionId, strategy_name: "Nested Continuation",
    version: 2, version_content_hash: "a".repeat(64), symbol: "BTCUSDT", timeframes: ["15m"],
    parameters: { confirmation_window: 12 },
  };
  const sources: Record<string, unknown> = {
    "/health": { status: "ok", execution_mode: "paper", real_trading_enabled: false, provider_mode: "mock", must_verify_email: false },
    "/auth/me": { user: { id: FIXTURE_UUID, email: "fixture@example.com", email_verified: true }, organization: { id: FIXTURE_UUID, name: "UI fixture" } },
    "/providers/status": { providers: [] },
    "/risk/kill-switch": { active: false, global_active: false, execution_blocked: false },
    "/conversations": pageOf([]),
    [`/conversations/${FIXTURE_UUID}`]: { id: FIXTURE_UUID, strategy_id: null },
    [`/conversations/${FIXTURE_UUID}/messages`]: pageOf([]),
    "/agent/saved": { items: [note], total: 1 },
    [`/agent/saved/${FIXTURE_UUID}`]: note,
    "/agent/voice/config": { available: false, reason: "Fixture only", sample_rate: 24000, transport: "websocket_pcm24" },
    "/knowledge/documents": pageOf([{ id: versionId, title: "Risk before entry", source_type: "risk_policy", source_uri: `https://example.com/${"reference/".repeat(25)}`, version: 1, created_at: now, updated_at: now }]),
    "/knowledge/chunks": pageOf([{ id: assignmentId, document_id: versionId, chunk_ordinal: 1, section_title: "Risk before entry", content: "Choose the invalidation level before entry.", metadata: { source_type: "risk_policy" }, created_at: now }]),
    "/journal/trades": pageOf([]),
    "/strategies": pageOf([]),
    "/strategy-brain/overview": {
      strategies: [], watched_symbols: ["BTCUSDT"], limitations: [], paper_only: true,
      setups: [
        { setup_id: FIXTURE_UUID, family: "nested_continuation", symbol: "BTCUSDT", direction: "long", timeframe: "15m", state: "awaiting_confirmation", observed_at: now },
        { setup_id: versionId, family: "nested_continuation", symbol: "ETHUSDT", direction: "short", timeframe: "1h", state: "historical", observed_at: "2020-01-01T00:00:00Z" },
      ],
    },
    "/market-watcher/monitoring": makeWatcherMonitoringSnapshot(),
    "/watcher/watchlist": { revision: 1, updated_at: now, max_enabled: 5, paper_only: true, slots: ["BTCUSDT", "ETHUSDT", "SOLUSDT", "TAOUSDT", "HYPEUSDT"].map((symbol, index) => ({ position: index + 1, symbol, enabled: true })) },
    "/watcher/watchlist/status": { configuration_revision: 1, observed_at: now, symbols: [] },
    "/watcher/assignments": { revision: 1, configured: true, symbols: ["BTCUSDT"], max_assignments: 25, monitoring_only: true, available_versions: [version], assignments: [{ ...version, id: assignmentId, monitoring_enabled: true, eligible: true, reason_code: "ready", latest: null }] },
    "/notifications/preferences": { in_app_enabled: true, webhook_enabled: false, telegram_enabled: false, min_severity: "warning", using_defaults: true },
  };
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.origin === "http://127.0.0.1:3000") return route.continue();
    if (url.origin !== "http://localhost:8000") {
      forbidden.push(`${route.request().method()} ${url.origin}${url.pathname}`);
      return route.abort();
    }
    requests.push(`${route.request().method()} ${url.pathname}`);
    if (url.pathname === "/agent/turns" && route.request().method() === "POST") return route.fulfill({ json: agentTurnFixture });
    if (url.pathname in sources) return route.fulfill({ json: sources[url.pathname] });
    return route.fulfill({ status: 503, json: { detail: "UI fixture: source unavailable" } });
  });
  return { requests, forbidden };
}

async function fits(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  const clipped = await page.locator("#main").evaluate(main => [...main.querySelectorAll("button, input, select, textarea")]
    .filter(element => { const box = element.getBoundingClientRect(); return box.width > 0 && (box.left < -1 || box.right > innerWidth + 1); })
    .map(element => element.getAttribute("aria-label") ?? element.textContent));
  expect(clipped).toEqual([]);
}

async function screenshot(page: Page, name: string) {
  if (!process.env.REFINEMENT_SCREENSHOTS) return;
  await page.addStyleTag({ content: "nextjs-portal { display: none; }" });
  await page.evaluate(() => {
    const label = document.createElement("div"); label.id = "fixture-watermark";
    label.textContent = "FRONTEND TEST FIXTURE · NOT LIVE DATA";
    Object.assign(label.style, { position: "fixed", bottom: "0", left: "0", right: "0", zIndex: "9999", background: "#142a24", color: "#fff", padding: "6px", fontSize: "12px", textAlign: "center" });
    document.body.append(label);
  });
  await page.screenshot({ path: path.resolve(`../docs/screenshots/frontend-refinement/${name}.png`), fullPage: true });
  await page.locator("#fixture-watermark").evaluate(element => element.remove());
}

for (const viewport of [
  { name: "desktop", width: 1440, height: 1000 },
  { name: "phone", width: 390, height: 844 },
  { name: "landscape", width: 844, height: 390 },
]) {
  test(`${viewport.name}: compact workspaces retain navigation and Agent controls`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const { requests, forbidden } = await fixtures(page);
    const errors: string[] = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto("/agent");
    await page.addStyleTag({ content: "nextjs-portal { display: none; }" });
    const menu = page.getByTestId("header-status-menu");
    await expect(menu).toBeVisible();
    await expect(page.getByRole("button", { name: "Search pages and destinations" })).not.toBeVisible();
    await menu.locator("summary").click();
    await page.getByRole("button", { name: "Search pages and destinations" }).click();
    await expect(menu).not.toHaveAttribute("open", "");
    await expect(page.getByTestId("command-menu")).toBeVisible();
    await page.keyboard.press("Escape");
    await menu.locator("summary").click();
    await page.keyboard.press("Escape");
    await expect(menu.locator("summary")).toBeFocused();
    await menu.locator("summary").click();
    await page.getByLabel("Message", { exact: true }).click();
    await expect(menu).not.toHaveAttribute("open", "");
    await page.getByLabel("Message", { exact: true }).fill("Remember to wait for confirmation.");
    await expect(page.getByRole("button", { name: "Talk to Agent" })).toBeEnabled();
    await page.getByRole("button", { name: "Talk to Agent" }).click();
    await expect(page.getByText("Interactive voice is unavailable. Continue typing.")).toBeVisible();
    await expect(page.getByLabel("Message", { exact: true })).toHaveValue("Remember to wait for confirmation.");
    await page.getByRole("button", { name: "Attach document", exact: true }).click();
    await expect(page.getByLabel("Attach document", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Close attachment" }).click();
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await expect(page.getByText(agentTurnFixture.reply, { exact: true })).toBeVisible();
    await fits(page);
    expect(await page.getByTestId("agent-thread").evaluate(el => getComputedStyle(el).overflowY)).not.toMatch(/auto|scroll/);
    // Keep the explicit unavailable voice state visible in this fixture capture.
    await screenshot(page, `agent-${viewport.name}`);

    await page.goto("/journal?tab=knowledge&category=rules&q=confirmation");
    await expect(page.getByRole("heading", { name: note.title })).toBeVisible();
    await expect(page.getByLabel("Knowledge category")).toContainText("RulesStrategiesNews & AnalysisLessons");
    await fits(page);
    await screenshot(page, `knowledge-${viewport.name}`);
    await page.getByRole("link", { name: "Open", exact: true }).click();
    await expect(page.getByRole("link", { name: "Back to Knowledge" })).toHaveAttribute("href", "/journal?tab=knowledge&category=rules&q=confirmation");
    await page.getByRole("link", { name: "Back to Knowledge" }).click();
    await expect(page.getByLabel("Search entries")).toHaveValue("confirmation");
    await page.goto("/journal?tab=knowledge&saved=missing");
    await expect(page.getByText("Saved entry unavailable.")).toBeVisible();
    await expect(page.getByRole("link", { name: "Back to Knowledge" })).toBeVisible();
    await page.goto(`/knowledge?document_id=${versionId}`);
    await expect(page.getByText("Choose the invalidation level before entry.")).toBeVisible();
    await expect(page.getByRole("link", { name: "Back to Knowledge" })).toBeVisible();
    await fits(page);

    await page.goto("/strategies");
    const family = page.getByTestId("strategy-family").first();
    await expect(family.locator("summary")).toHaveText("Recent detections (1)");
    await expect(family.getByText("BTCUSDT · long · 15m", { exact: true })).not.toBeVisible();
    await family.locator("summary").click();
    await expect(family.getByText("BTCUSDT · long · 15m", { exact: true })).toBeVisible();
    await expect(family.getByText("ETHUSDT", { exact: false })).toHaveCount(0);
    await expect(family.getByRole("link", { name: "Browse detections" })).toHaveAttribute("href", "/strategies/detections?family=nested_continuation");
    await fits(page);
    await screenshot(page, `strategies-${viewport.name}`);

    await page.goto("/settings#markets");
    const assignments = page.getByTestId("monitoring-assignments");
    await expect(assignments.getByLabel("Saved strategy version")).toBeVisible();
    await expect(assignments.getByRole("checkbox")).toBeChecked();
    await assignments.getByRole("button", { name: "Details", exact: true }).click();
    const detail = page.getByTestId("monitoring-assignment-details");
    await expect(detail.getByText(`Assignment: ${assignmentId}`, { exact: false })).not.toBeVisible();
    await detail.locator("summary").click();
    await expect(detail.getByText(`Assignment: ${assignmentId}`, { exact: false })).toBeVisible();
    await page.getByRole("button", { name: "Back to monitoring" }).click();
    await assignments.getByRole("checkbox").uncheck();
    await expect(assignments.getByRole("button", { name: "Save monitoring" })).toBeEnabled();
    await fits(page);
    await screenshot(page, `settings-${viewport.name}`);
    await page.locator("#notifications > summary").click();
    await expect(page.getByText("Telegram delivery", { exact: true })).toBeVisible();
    await page.locator("#account-system > summary").click();
    await expect(page.getByRole("link", { name: "Exchange connections" })).toBeVisible();
    await fits(page);
    expect(errors).toEqual([]);
    expect(forbidden).toEqual([]);
    expect(requests.filter(request => !request.startsWith("GET "))).toEqual(["POST /agent/turns"]);
  });
}
