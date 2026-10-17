import { expect, test } from "@playwright/test";

import { getSharedE2EAccessToken, installSharedE2ESession } from "./helpers/shared-e2e-auth";

const API_URL = process.env.PLAYWRIGHT_API_URL ?? "http://localhost:8000";

const FIRST_SLICE_PREVIEW = [
  "kind: bearish_liquidity_sweep_cvd_sell_imbalance_at_4h_resistance/v1",
  "name: Bearish Liquidity Sweep with CVD Divergence and Aggressive Sell Imbalance at 4h Resistance",
  "symbol: BTCUSDT",
  "trigger_timeframe: 15m",
  "context_timeframe: 4h",
  "direction: short",
  "requires_manual_4h_resistance: true",
  "requires_confirmed_swing: true",
  "trigger_atr.feature_type: WILDER_ATR",
  "trigger_atr.feature_version: wilder-atr/v1",
  "trigger_atr.period: 14",
  "context_atr.feature_type: WILDER_ATR",
  "context_atr.feature_version: wilder-atr/v1",
  "context_atr.period: 14",
  "resistance_distance_atr_threshold: 0.50",
  "sweep_threshold_atr: 0.25",
  "close_below_swing: true",
  "bearish_candle_close_below_open: true",
  "volume_lookback_bars: 20",
  "volume_ratio_threshold: 1.50",
  "requires_cvd_divergence: true",
  "aggressive_sell_imbalance_threshold: -0.10",
  "required_finality: true",
  "required_freshness: true",
  "required_no_gap: true",
  "invalidation.atr_multiple: 0.10",
  "invalidation.tick_multiple: 2",
  "expiry_final_bars: 2",
  "htf_resistance_context",
  "ltf_liquidity_sweep",
  "htf_min_offset: 0",
  "htf_max_offset: 0",
  "ltf_min_offset: 0",
  "ltf_max_offset: 2",
  "overlap_policy: disallow",
  "reset_semantics: none",
  "invalidation_semantics: none",
  "finality_requirement: final_only",
  "liquidity sweep cvd bearish imbalance",
].join("\n");

const SAMPLE_CARD = {
  strategy_name: "Conversation E2E Pullback",
  market_type: "crypto_perp",
  asset_universe: ["BTCUSDT"],
  timeframes: ["4h", "1h"],
  entry_conditions: ["Pullback to EMA cluster"],
  confirmation_conditions: ["RSI reset above 40"],
  invalidation: ["Close below swing low"],
  stop_loss: ["Below invalidation swing"],
  take_profit_plan: ["TP1 at prior high"],
  runner_plan: ["Trail after TP1"],
  position_sizing: ["Max 1% account risk"],
  add_rules: ["No adds until TP1"],
  no_trade_rules: ["Skip if funding extreme"],
  backtest_rules: ["Placeholder — not run"],
  success_criteria: ["Win rate > 45% in paper"],
  validation_status: "draft",
};

test.describe("Strategy Lab conversation", () => {
  test("panel persists a discussion across reload without mutating authority", async ({
    page,
    request,
  }) => {
    const token = await getSharedE2EAccessToken(request);
    const headers = { Authorization: `Bearer ${token}` };
    const created = await request.post(`${API_URL}/strategies`, {
      headers,
      data: {
        name: "Conversation E2E Pullback",
        setup_type: "htf_trend_pullback",
        card: SAMPLE_CARD,
      },
    });
    expect(created.ok(), `create strategy HTTP ${created.status()}`).toBeTruthy();
    const strategy = (await created.json()) as { id: string };
    const marker = `Durable conversation ${Date.now()}`;

    await installSharedE2ESession(page, request);
    await page.goto(`/agent?strategy_id=${strategy.id}`);
    await expect(page.getByRole("link", { name: "Back to strategy" })).toBeVisible();
    await page.getByLabel("Message", { exact: true }).fill(marker);
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await expect(page.getByTestId("agent-message").filter({ hasText: marker })).toBeVisible({ timeout: 60_000 });
    await page.reload();
    await expect(page.getByTestId("agent-message").filter({ hasText: marker })).toBeVisible();

    const conversations = await request.get(`${API_URL}/conversations`, {
      headers,
      params: { strategy_id: strategy.id },
    });
    expect(conversations.ok()).toBeTruthy();
    const listed = (await conversations.json()) as { items: Array<{ id: string }>; total: number };
    expect(listed.total).toBeGreaterThanOrEqual(1);

    const messages = await request.get(`${API_URL}/conversations/${listed.items[0].id}/messages`, {
      headers,
    });
    expect(messages.ok()).toBeTruthy();
    const body = (await messages.json()) as { total: number };
    expect(body.total).toBeGreaterThanOrEqual(1);

    const versions = await request.get(`${API_URL}/strategies/${strategy.id}/versions`, {
      headers,
    });
    expect(versions.ok()).toBeTruthy();
    const versionPage = (await versions.json()) as { total: number };
    expect(versionPage.total).toBe(1);
  });

  test("preview confirm compile approve stays on the explicit ladder", async ({ page, request }) => {
    const token = await getSharedE2EAccessToken(request);
    const headers = { Authorization: `Bearer ${token}` };
    await installSharedE2ESession(page, request);
    await page.goto("/strategy-lab/new");
    await page.getByText("Strategy options", { exact: true }).click();
    await page.getByLabel("Strategy setup type").selectOption("liquidity_sweep_reversal");
    await page.getByRole("button", { name: "Attach document" }).click();
    await page.getByLabel("Attach document", { exact: true }).setInputFiles({
      name: "sweep-rules.txt", mimeType: "text/plain", buffer: Buffer.from(FIRST_SLICE_PREVIEW),
    });
    await page.getByRole("button", { name: "Preview attachment" }).click();
    await expect(page.getByLabel("Attachment preview")).toHaveValue(FIRST_SLICE_PREVIEW);
    await page.getByLabel("Message", { exact: true }).fill("Review these imported strategy rules.");
    await page.getByRole("button", { name: "Review strategy draft" }).click();
    const review = page.getByTestId("agent-strategy-draft");
    await expect(review).toBeVisible({ timeout: 60_000 });
    const conversationId = new URL(page.url()).searchParams.get("conversation");
    const proposals = await request.get(`${API_URL}/conversations/${conversationId}/proposals`, { headers });
    expect((await proposals.json()).items[0].resulting_version_id).toBeNull();
    await review.getByRole("button", { name: "Confirm and save strategy version" }).click();
    await expect(review.getByRole("link", { name: "Open saved strategy" })).toBeVisible();
    const savedHref = await review.getByRole("link", { name: "Open saved strategy" }).getAttribute("href");
    const strategy = { id: savedHref!.split("/").at(-1) };
    await page.reload();
    await expect(review.getByRole("link", { name: "Open saved strategy" })).toBeVisible();
    const saved = await request.get(`${API_URL}/strategies/${strategy.id}`, { headers });
    expect((await saved.json()).setup_type).toBe("liquidity_sweep_reversal");
    const versions = await request.get(`${API_URL}/strategies/${strategy.id}/versions`, { headers });
    expect((await versions.json()).total).toBe(2);
    await review.getByRole("button", { name: "Compile saved version" }).click();
    await expect(page.getByTestId("agent-strategy-compile-status")).toContainText(/executable/i, { timeout: 60_000 });
    await review.getByRole("button", { name: "Approve compiled policy" }).click();
    await expect(page.getByTestId("agent-strategy-lifecycle")).toContainText(/approved/i, { timeout: 60_000 });

  });
});
