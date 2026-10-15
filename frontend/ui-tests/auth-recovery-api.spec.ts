import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs/promises";
import path from "node:path";
import type { components } from "../src/lib/api/generated/types";

type Draft = components["schemas"]["ExperimentVersion"];
type Screening = components["schemas"]["TrendPulseScreeningDetail"];
type Fixture = { email: string; password: string; strategy_id: string; trigger_end: string; other: { email: string; password: string } };
const recoveryPrefixes = ["alphatrade:experiment-draft:", "alphatrade:screening:"];

test.describe.configure({ mode: "serial" });
test.skip(!process.env.EXPERIMENT_BROWSER_AUTH_FILE, "Requires explicit disposable local PostgreSQL/login fixtures.");

async function localFixture(page: Page) {
  const base = process.env.PLAYWRIGHT_API_URL!;
  const frontend = process.env.PLAYWRIGHT_BASE_URL!;
  const file = process.env.EXPERIMENT_BROWSER_AUTH_FILE!;
  expect(base && frontend && file, "Supply both loopback URLs and the private seeded auth file.").toBeTruthy();
  expect(["127.0.0.1", "localhost"]).toContain(new URL(base).hostname);
  expect(["127.0.0.1", "localhost"]).toContain(new URL(frontend).hostname);
  expect(path.resolve(file).startsWith("/tmp/")).toBe(true);
  expect((await fs.stat(file)).mode & 0o777).toBe(0o600);
  const fixture = JSON.parse(await fs.readFile(file, "utf8")) as Fixture;
  expect(fixture.email, "Reseed with real synthetic login credentials.").toMatch(/^browser-[a-f\d]+@example\.com$/);
  expect(fixture.password.length).toBeGreaterThanOrEqual(12);
  expect(fixture.strategy_id).toBeTruthy();
  const healthResponse = await page.request.get(`${base}/health`);
  expect(healthResponse.headers()["x-synthetic-fixture-process"], "Use the existing disarmed research browser harness.").toBeTruthy();
  const health = await healthResponse.json();
  expect(health.execution_mode).toBe("paper");
  expect(health.real_trading_enabled).toBe(false);
  return { base, fixture };
}

async function login(page: Page, base: string, credentials: Pick<Fixture, "email" | "password">) {
  await page.goto("/login");
  await page.getByLabel("Email", { exact: true }).fill(credentials.email);
  await page.getByLabel("Password", { exact: true }).fill(credentials.password);
  const response = page.waitForResponse(result => result.url() === `${base}/auth/login` && result.request().method() === "POST");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  const authenticated = await response;
  expect(authenticated.ok()).toBe(true);
  // Use only the token issued by the actual form submission for read-only assertions.
  // No token, session cookie or recovery state is injected into the browser.
  const result = await authenticated.json();
  await page.waitForURL(url => url.pathname !== "/login");
  await expect(page.getByTestId("header-status-menu")).toBeVisible();
  return { Authorization: `Bearer ${result.tokens.access_token}` };
}

async function recoveryKeys(page: Page) {
  return page.evaluate(prefixes => Object.keys(sessionStorage).filter(key => prefixes.some(prefix => key.startsWith(prefix))), recoveryPrefixes);
}

async function prepareDraft(page: Page, strategyId: string, name: string) {
  await page.goto(`/strategy-lab/${strategyId}`);
  const panel = page.getByTestId("experiment-draft");
  await expect(panel.getByText("Prepare research draft")).toBeVisible();
  await panel.getByText("Prepare research draft").click();
  await panel.getByLabel("Experiment name").fill(name);
  for (const [label, value] of [["Risk per trade", "10"], ["Position notional", "500"], ["Total exposure", "1000"], ["Daily loss", "50"], ["Weekly loss", "100"], ["Drawdown", "100"], ["Cost allowance", "1"]]) {
    await panel.getByLabel(`${label} (USDT)`).fill(value);
  }
  return panel;
}

async function logoutAwayFromPanels(page: Page, base: string) {
  await page.goto("/journal");
  await expect(page.getByTestId("experiment-draft")).toHaveCount(0);
  await expect(page.getByTestId("screenings")).toHaveCount(0);
  expect((await recoveryKeys(page)).length).toBeGreaterThan(0);
  const menu = page.getByTestId("header-status-menu");
  await menu.locator("summary").click();
  const response = page.waitForResponse(result => result.url() === `${base}/auth/logout` && result.request().method() === "POST");
  await menu.getByRole("button", { name: "Log out", exact: true }).click();
  expect((await response).ok()).toBe(true);
  await expect(page).toHaveURL(/\/login(?:\?|$)/);
  await expect.poll(() => recoveryKeys(page)).toEqual([]);
  expect(await page.evaluate(() => sessionStorage.getItem("alphatrade_access_token"))).toBeNull();
}

test("real login keeps exact draft and screening identity through reload and explicit recovery", async ({ page }) => {
  test.setTimeout(150_000);
  const { base, fixture } = await localFixture(page);
  const headers = await login(page, base, fixture);
  const name = `Auth recovery ${crypto.randomUUID().slice(0, 8)}`;
  const listing = async () => (await page.request.get(`${base}/experiments?limit=100`, { headers })).json();
  const before = await listing();
  const draftPanel = await prepareDraft(page, fixture.strategy_id, name);
  let draftCalls = 0;
  let originalDraft = "";
  let committedDraft!: Draft;
  await page.route(`${base}/experiments`, async route => {
    if (route.request().method() !== "POST") return route.continue();
    draftCalls++;
    const body = route.request().postData()!;
    if (draftCalls === 1) originalDraft = body;
    else expect(body).toBe(originalDraft);
    const actual = await route.fetch();
    expect(actual.status()).toBe(201);
    const result = await actual.json() as Draft;
    if (draftCalls === 1) committedDraft = result;
    else expect(result).toEqual(committedDraft);
    if (draftCalls === 1) await route.fulfill({ response: actual, body: '{"unexpected":true}' });
    else await route.fulfill({ response: actual });
  });
  await draftPanel.getByRole("button", { name: "Create experiment draft" }).click();
  await expect(draftPanel.getByRole("button", { name: "Recover draft" })).toBeEnabled();
  expect(draftCalls).toBe(1);
  expect(JSON.parse(originalDraft).idempotency_key).toBeTruthy();
  expect(committedDraft.runtime_activated).toBe(false);
  expect(committedDraft.performance).toBeNull();
  const pendingDraftKeys = await recoveryKeys(page);
  expect(pendingDraftKeys).toHaveLength(1);
  await page.reload();
  const reloadedDraft = page.getByTestId("experiment-draft");
  await expect(reloadedDraft.getByRole("button", { name: "Recover draft" })).toBeEnabled();
  expect(await recoveryKeys(page)).toEqual(pendingDraftKeys);
  expect(draftCalls).toBe(1);
  const afterCommit = await listing();
  expect(afterCommit.total).toBe(before.total + 1);
  expect(afterCommit.items.filter((item: { name: string }) => item.name === name)).toHaveLength(1);
  await reloadedDraft.getByRole("button", { name: "Recover draft" }).click();
  await expect(reloadedDraft.getByRole("link", { name: "Open experiment" })).toBeVisible();
  expect(draftCalls).toBe(2);
  expect((await listing()).total).toBe(afterCommit.total);
  await page.unroute(`${base}/experiments`);
  await reloadedDraft.getByRole("link", { name: "Open experiment" }).click();

  const screeningPanel = page.getByTestId("screenings");
  await expect(screeningPanel.getByText("No screenings recorded.")).toBeVisible();
  await screeningPanel.getByText("Screen a closed trigger").click();
  await screeningPanel.getByLabel("Trigger close (UTC)").fill(fixture.trigger_end.slice(0, 16));
  const screeningURL = `${base}/experiments/${committedDraft.experiment_id}/versions/${committedDraft.id}/trendpulse-screenings`;
  let screeningCalls = 0;
  let originalScreening = "";
  let committedScreening!: Screening;
  await page.route(screeningURL, async route => {
    if (route.request().method() !== "POST") return route.continue();
    screeningCalls++;
    const body = route.request().postData()!;
    if (screeningCalls === 1) originalScreening = body;
    else expect(body).toBe(originalScreening);
    const actual = await route.fetch();
    expect(actual.ok()).toBe(true);
    const result = await actual.json() as Screening;
    if (screeningCalls === 1) committedScreening = result;
    else expect(result).toEqual(committedScreening);
    if (screeningCalls === 1) await route.abort("failed");
    else await route.fulfill({ response: actual });
  });
  await screeningPanel.getByRole("button", { name: "Screen trigger" }).click();
  await expect(screeningPanel.getByRole("button", { name: "Recover screening" })).toBeEnabled();
  expect(screeningCalls).toBe(1);
  expect(JSON.parse(originalScreening).request_id).toBe(committedScreening.request_id);
  expect(committedScreening.receipt_provenance).toBe("synthetic_fixture");
  expect(committedScreening.execution_authorized).toBe(false);
  expect(committedScreening.sample_eligible).toBe(false);
  expect(committedScreening.performance).toBeNull();
  const pendingScreeningKeys = await recoveryKeys(page);
  expect(pendingScreeningKeys).toHaveLength(1);
  await page.reload();
  await expect(screeningPanel.getByRole("button", { name: "Recover screening" })).toBeEnabled();
  expect(await recoveryKeys(page)).toEqual(pendingScreeningKeys);
  expect(screeningCalls).toBe(1);
  const historyBefore = await (await page.request.get(screeningURL, { headers })).json();
  expect(historyBefore.total).toBe(1);
  expect(historyBefore.items[0].id).toBe(committedScreening.id);
  await screeningPanel.getByRole("button", { name: "Recover screening" }).click();
  await expect(screeningPanel.getByText("Receipts & evidence")).toBeVisible();
  expect(screeningCalls).toBe(2);
  expect((await (await page.request.get(screeningURL, { headers })).json()).total).toBe(1);
  expect(await (await page.request.get(`${base}/trendpulse-screenings/${committedScreening.id}`, { headers })).json()).toEqual(committedScreening);
  await expect(screeningPanel.getByText(/Performance unavailable/)).toBeVisible();
  expect(await recoveryKeys(page)).toEqual([]);
});

test("real logout off the panels removes unresolved drafts and screenings before same or different login", async ({ page }) => {
  test.setTimeout(150_000);
  const { base, fixture } = await localFixture(page);
  await login(page, base, fixture);
  const panel = await prepareDraft(page, fixture.strategy_id, `Logout recovery ${crypto.randomUUID().slice(0, 8)}`);
  let draftCalls = 0;
  let committed!: Draft;
  await page.route(`${base}/experiments`, async route => {
    if (route.request().method() !== "POST") return route.continue();
    draftCalls++;
    const actual = await route.fetch();
    expect(actual.status()).toBe(201);
    committed = await actual.json() as Draft;
    await route.fulfill({ response: actual, body: '{"unexpected":true}' });
  });
  await panel.getByRole("button", { name: "Create experiment draft" }).click();
  await expect(panel.getByRole("button", { name: "Recover draft" })).toBeEnabled();
  const target = `/strategies?experiment=${committed.experiment_id}`;
  await page.goto(target);
  const screenings = page.getByTestId("screenings");
  await expect(screenings.getByText("No screenings recorded.")).toBeVisible();
  await screenings.getByText("Screen a closed trigger").click();
  await screenings.getByLabel("Trigger close (UTC)").fill(fixture.trigger_end.slice(0, 16));
  const screeningURL = `${base}/experiments/${committed.experiment_id}/versions/${committed.id}/trendpulse-screenings`;
  let screeningCalls = 0;
  await page.route(screeningURL, async route => {
    if (route.request().method() !== "POST") return route.continue();
    screeningCalls++;
    const actual = await route.fetch();
    expect(actual.ok()).toBe(true);
    await route.fulfill({ response: actual, body: '{"unexpected":true}' });
  });
  await screenings.getByRole("button", { name: "Screen trigger" }).click();
  await expect(screenings.getByRole("button", { name: "Recover screening" })).toBeEnabled();
  const unresolved = await recoveryKeys(page);
  expect(unresolved).toHaveLength(2);
  for (const prefix of recoveryPrefixes) expect(unresolved.filter(key => key.startsWith(prefix))).toHaveLength(1);
  await logoutAwayFromPanels(page, base);
  await login(page, base, fixture);
  await page.goto(`/strategy-lab/${fixture.strategy_id}`);
  await expect(page.getByText("Prepare research draft")).toBeVisible();
  await expect(page.getByRole("button", { name: "Recover draft" })).toHaveCount(0);
  await page.goto(target);
  await expect(screenings.getByRole("button", { name: "Details", exact: true })).toBeVisible();
  await expect(screenings.getByRole("button", { name: "Recover screening" })).toHaveCount(0);
  expect(await recoveryKeys(page)).toEqual([]);
  expect(draftCalls).toBe(1);
  expect(screeningCalls).toBe(1);
  await page.unroute(`${base}/experiments`);
  await page.unroute(screeningURL);

  // Both accounts are fresh disposable fixtures with persisted conservative safety
  // state; account switching uses the actual login form, never injected tokens.
  await page.goto("/journal");
  const menu = page.getByTestId("header-status-menu");
  await menu.locator("summary").click();
  await menu.getByRole("button", { name: "Log out", exact: true }).click();
  await expect(page).toHaveURL(/\/login(?:\?|$)/);
  const otherHeaders = await login(page, base, fixture.other);
  await page.goto("/strategies");
  await expect(page.getByTestId("experiments").getByText("No experiments yet.", { exact: false })).toBeVisible();
  expect(await recoveryKeys(page)).toEqual([]);
  await expect(page.getByRole("button", { name: /Recover (draft|screening)/ })).toHaveCount(0);
  expect((await page.request.get(`${base}/experiments/${committed.experiment_id}`, { headers: otherHeaders })).status()).toBe(404);
  expect((await page.request.get(screeningURL, { headers: otherHeaders })).status()).toBe(404);
});
