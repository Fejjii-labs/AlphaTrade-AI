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
