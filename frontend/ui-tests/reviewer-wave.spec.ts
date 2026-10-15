import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { agentTurnFixture, FIXTURE_UUID } from "../src/test/pilot-fixtures";

async function fixtures(page: Page, handle?: (route: import("@playwright/test").Route) => Promise<boolean>) {
  await page.context().addCookies([{ name: "alphatrade_session", value: "1", url: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3000" }]);
  await page.addInitScript(() => sessionStorage.setItem("alphatrade_access_token", "frontend-fixture-only"));
  await page.route(`${process.env.PLAYWRIGHT_API_URL ?? "http://localhost:8000"}/**`, async route => {
    if (handle && await handle(route)) return;
    const source: Record<string, unknown> = {
      "/health": { status: "ok", execution_mode: "paper", real_trading_enabled: false,
        provider_mode: "mock", must_verify_email: false },
      "/auth/me": { user: { id: FIXTURE_UUID, email: "fixture@example.com", email_verified: true },
        organization: { id: FIXTURE_UUID, name: "Frontend fixture" } },
      "/providers/status": { providers: [] },
      "/risk/kill-switch": { active: false, global_active: false, execution_blocked: false },
      "/conversations": { items: [], total: 0, limit: 30, offset: 0 },
      [`/strategies/${FIXTURE_UUID}`]: { id: FIXTURE_UUID, name: "Authorized fixture strategy" },
    };
    const pathname = new URL(route.request().url()).pathname;
    if (pathname in source) await route.fulfill({ json: source[pathname] });
    else await route.fulfill({ status: 503, json: { detail: "Fixture: source unavailable" } });
  });
}

for (const source of ["/settings/usage", "/usage", "/billing"]) {
  test(`${source} resolves to the actual canonical billing URL and preserves query`, async ({ page }) => {
    await fixtures(page);
    await page.goto(`${source}?keep=fixture&window=30`);
    await expect(page).toHaveURL(new RegExp(`/settings/billing\\?keep=fixture&window=30${source.includes('usage') ? '#usage' : ''}$`));
    await expect(page.locator("#usage")).toBeAttached();
  });
}

test("manual strategy authoring routes open Agent without sending; authorized context is retained", async ({ page }) => {
  const mutations: string[] = [];
  await fixtures(page, async route => {
    if (route.request().method() !== "GET") mutations.push(route.request().url());
    return false;
  });
  await page.goto("/strategy-lab/new?keep=fixture");
  await expect(page).toHaveURL(/\/agent\?.*intent=strategy/);
  expect(new URL(page.url()).searchParams.get("keep")).toBe("fixture");
  await expect(page.getByTestId("agent-workspace")).toBeVisible();
  await page.goto(`/strategy-lab/${FIXTURE_UUID}/edit?keep=fixture`);
  await expect(page).toHaveURL(/\/agent\?.*strategy_id=/);
  expect(new URL(page.url()).searchParams.get("keep")).toBe("fixture");
  await expect(page.getByTestId("agent-strategy-context")).toContainText("Authorized fixture strategy");
  await expect(page.getByRole("link", { name: "Back to strategy" })).toHaveAttribute("href", `/strategy-lab/${FIXTURE_UUID}`);
  expect(mutations).toEqual([]);
});

test("acknowledged answer is visible and composer is usable while five-second history is pending", async ({ page }, testInfo) => {
  const counts = { turns: 0, history: 0 };
  let responseAt = 0;
  let historyComplete = false;
  let releaseHistory!: () => void;
  const historyGate = new Promise<void>(resolve => { releaseHistory = resolve; });
  await fixtures(page, async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === "/agent/turns") {
      counts.turns++;
      await new Promise(resolve => setTimeout(resolve, 200));
      responseAt = performance.now();
      await route.fulfill({ json: agentTurnFixture });
      return true;
    }
    if (pathname.endsWith("/messages")) {
      counts.history++;
      await historyGate;
      historyComplete = true;
      await route.fulfill({ json: { items: [], total: 0, limit: 100, offset: 0 } });
      return true;
    }
    return false;
  });
  await page.goto("/agent");
  await page.getByLabel("Message", { exact: true }).fill("Review my recorded rules");
  const started = performance.now();
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByTestId("agent-thread")).toContainText(agentTurnFixture.reply);
  const visibleAt = performance.now();
  await expect(page.getByLabel("Message", { exact: true })).toHaveValue("");
  await page.getByLabel("Message", { exact: true }).fill("Another intentional turn");
  await expect(page.getByRole("button", { name: "Send", exact: true })).toBeEnabled();
  expect(historyComplete).toBe(false);
  expect(visibleAt - responseAt).toBeLessThan(2000);
  await page.screenshot({ path: path.resolve("../docs/reviewer_wave/screenshots/agent-acknowledged.png") });
  // Deterministic stale history response follows the delayed fixture; it must not erase the turn.
  await new Promise(resolve => setTimeout(resolve, Math.max(0, 5000 - (performance.now() - responseAt))));
  releaseHistory();
  await expect(page.getByTestId("agent-message")).toHaveCount(2);
  await expect.poll(() => historyComplete).toBe(true);
  await expect(page.getByTestId("agent-thread")).toContainText(agentTurnFixture.reply);
  await testInfo.attach("latency-and-request-counts", { body: JSON.stringify({
    fixture: "200ms acknowledgment, 5000ms stale history", counts,
    click_to_visible_ms: Math.round(visibleAt - started), acknowledgment_to_visible_ms: Math.round(visibleAt - responseAt),
  }, null, 2), contentType: "application/json" });
  expect(counts).toEqual({ turns: 1, history: 1 });
});

test("unauthorized strategy handoff fails visibly and cannot send", async ({ page }) => {
  await fixtures(page);
  await page.goto("/agent?strategy_id=not-owned");
  await expect(page.getByTestId("agent-strategy-context")).toContainText("Strategy unavailable");
  await page.getByLabel("Message", { exact: true }).fill("Discuss this plan");
  await expect(page.getByRole("button", { name: "Send", exact: true })).toBeDisabled();
});

test("Validation prefetch is narrow and completed widgets remain visible beside pending and failed sources", async ({ page }, testInfo) => {
  const counts = { summary: 0, performance: 0, ranking: 0, quality: 0 };
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let slowDone = false;
  const summary = { organization_id: FIXTURE_UUID, date_range: {}, min_sample: 5,
    funnel: { alerts: 0, drafts: 0, candidates: 0, run_plans: 0, run_sessions: 1,
      completed_sessions: 1, cancelled_sessions: 0, results: 1 },
    total_sessions: 1, completed_sessions: 1, cancelled_sessions: 0, results_count: 1,
    outcome_distribution: [{ outcome: "success", count: 1, rate: 1 }], rates: {},
    observations: { total_observations: 0, by_kind: {} }, lessons_count: 0 };
  await fixtures(page, async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === "/learning-analytics/summary") {
      counts.summary++; await route.fulfill({ json: summary }); return true;
    }
    if (pathname === "/learning-analytics/setup-performance") {
      counts.performance++; await gate; slowDone = true;
      await route.fulfill({ json: { ...summary, dimension: "condition", groups: [] } }); return true;
    }
    if (pathname === "/learning-analytics/setup-ranking") {
      counts.ranking++; await route.fulfill({ json: { ...summary, dimension: "condition", ranked: [], note: "Fixture empty ranking" } }); return true;
    }
    if (pathname === "/strategy-quality/summary") {
      counts.quality++; await route.fulfill({ status: 422, json: { detail: "Fixture quality unavailable" } }); return true;
    }
    return false;
  });
  await page.goto("/analytics");
  await page.getByRole("tab", { name: "Validation", exact: true }).hover();
  await expect.poll(() => counts.summary).toBe(1);
  expect(counts).toEqual({ summary: 1, performance: 0, ranking: 0, quality: 0 });
  const started = performance.now();
  await page.getByRole("tab", { name: "Validation", exact: true }).click();
  await expect(page.getByTestId("validation-outcome-chart")).toBeVisible();
  const visible = performance.now() - started;
  await expect(page.getByText("Fixture quality unavailable", { exact: true })).toBeVisible();
  expect(slowDone).toBe(false);
  expect(counts).toEqual({ summary: 1, performance: 1, ranking: 1, quality: 1 });
  const firstVisitCounts = { ...counts };
  await page.getByTestId("validation-charts").screenshot({ path: path.resolve("../docs/reviewer_wave/screenshots/validation-independent.png") });
  release();
  await expect.poll(() => slowDone).toBe(true);
  await page.getByRole("tab", { name: "Behaviour", exact: true }).click();
  await page.getByRole("tab", { name: "Validation", exact: true }).click();
  await expect(page.getByTestId("validation-outcome-chart")).toBeVisible();
  await expect(page.getByText("Fixture quality unavailable", { exact: true })).toBeVisible();
  await expect.poll(() => counts.quality).toBe(2);
  expect(counts.summary).toBe(1);
  await testInfo.attach("validation-prefetch-and-independent-sources", { body: JSON.stringify({
    fixture: "cached summary, gated setup performance, quality HTTP 422",
    first_visit_counts: firstVisitCounts, after_explicit_tab_return_counts: { ...counts },
    tab_click_to_visible_summary_ms: Math.round(visible),
  }, null, 2), contentType: "application/json" });
});
