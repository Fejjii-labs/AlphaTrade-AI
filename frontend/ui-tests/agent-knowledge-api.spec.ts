import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs/promises";
import path from "node:path";
import type { SavedEntry } from "../src/lib/api/saved-entries";

const stateFile = "/tmp/alphatrade-next-postgres/delivery-browser-state.json";
type Fixture = { email: string; password: string; other: { email: string; password: string } };
type Turn = { conversation_id: string; reply: string; saved_entries: SavedEntry[]; capture_status: string };

async function fixture(page: Page) {
  const base = process.env.PLAYWRIGHT_API_URL!;
  const frontend = process.env.PLAYWRIGHT_BASE_URL!;
  const authFile = process.env.EXPERIMENT_BROWSER_AUTH_FILE!;
  expect(base && frontend && authFile).toBeTruthy();
  for (const url of [base, frontend]) expect(["127.0.0.1", "localhost"]).toContain(new URL(url).hostname);
  expect(path.resolve(authFile).startsWith("/tmp/")).toBe(true);
  expect((await fs.stat(authFile)).mode & 0o777).toBe(0o600);
  const auth = JSON.parse(await fs.readFile(authFile, "utf8")) as Fixture;
  const response = await page.request.get(`${base}/health`);
  const processIdentity = response.headers()["x-synthetic-fixture-process"];
  expect(processIdentity).toBeTruthy();
  const health = await response.json();
  expect(health.execution_mode).toBe("paper");
  expect(health.real_trading_enabled).toBe(false);
  return { base, auth, processIdentity };
}

async function login(page: Page, base: string, auth: Pick<Fixture, "email" | "password">) {
  await page.goto("/login");
  await page.getByLabel("Email", { exact: true }).fill(auth.email);
  await page.getByLabel("Password", { exact: true }).fill(auth.password);
  const pending = page.waitForResponse(r => r.url() === `${base}/auth/login` && r.request().method() === "POST");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  const response = await pending;
  expect(response.ok()).toBe(true);
  const result = await response.json();
  await page.waitForURL(url => url.pathname !== "/login");
  await expect(page.getByTestId("header-status-menu")).toBeVisible();
  return { Authorization: `Bearer ${result.tokens.access_token}` };
}

async function send(page: Page, base: string, content: string) {
  await page.getByLabel("Message", { exact: true }).fill(content);
  const pending = page.waitForResponse(r => r.url() === `${base}/agent/turns` && r.request().method() === "POST");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  const response = await pending;
  expect(response.ok()).toBe(true);
  const result = await response.json() as Turn;
  expect(result.capture_status).toBe("saved");
  await expect(page.getByTestId("agent-message").filter({ hasText: result.reply.split("\n\nRecorded facts", 1)[0] }).first()).toBeVisible();
  return result;
}

test("actual login saves mixed category notes and explicitly recovers one malformed committed turn", async ({ page }) => {
  test.setTimeout(180_000);
  const { base, auth, processIdentity } = await fixture(page);
  const headers = await login(page, base, auth);
  const list = async () => (await page.request.get(`${base}/agent/saved?limit=100`, { headers })).json();
  const before = await list();
  await page.goto("/agent");
  await expect(page.getByLabel("Message", { exact: true })).toBeVisible();
  let calls = 0;
  let originalBody = "";
  let originalKey = "";
  let original!: Turn;
  await page.route(`${base}/agent/turns`, async route => {
    calls++;
    const body = route.request().postData()!;
    const key = route.request().headers()["idempotency-key"];
    if (calls === 1) { originalBody = body; originalKey = key; }
    else { expect(body).toBe(originalBody); expect(key).toBe(originalKey); }
    const actual = await route.fetch();
    expect(actual.ok()).toBe(true);
    const result = await actual.json() as Turn;
    if (calls === 1) original = result;
    else expect(result).toEqual(original);
    if (calls === 1) await route.fulfill({ response: actual, body: '{"unexpected":true}' });
    else await route.fulfill({ response: actual });
  });
  await page.getByLabel("Message", { exact: true }).fill([
    "[journal] Synthetic review: I waited for confirmation before this hypothetical entry.",
    "[news_analysis] Synthetic outlook: my uncertain forecast is not verified exchange evidence.",
    "[lessons] Synthetic takeaway: confirm assumptions before acting.",
  ].join("\n"));
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByRole("button", { name: "Recover original request" })).toBeEnabled();
  expect(original.capture_status).toBe("saved");
  expect(original.saved_entries.map(e => e.category).sort()).toEqual(["journal", "lessons", "news_analysis"]);
  expect((await list()).total).toBe(before.total + 3);
  expect(calls).toBe(1);
  await page.reload();
  await expect(page.getByRole("button", { name: "Recover original request" })).toBeEnabled();
  expect(calls).toBe(1);
  await page.getByRole("button", { name: "Recover original request" }).click();
  await expect(page.getByTestId("agent-turn-recovery")).toHaveCount(0);
  expect(calls).toBe(2);
  expect((await list()).total).toBe(before.total + 3);
  await page.unroute(`${base}/agent/turns`);
  const second = await send(page, base, [
    "[rules] Synthetic operating principle: protect the existing approval boundary.",
    "[strategies] Synthetic strategy idea: observe a closed trigger; no approval or execution.",
  ].join("\n"));
  expect(second.saved_entries.map(e => e.category).sort()).toEqual(["rules", "strategies"]);
  const entries = [...original.saved_entries, ...second.saved_entries];
  expect((await list()).total).toBe(before.total + 5);
  for (const entry of second.saved_entries) {
    const expected = `/journal?tab=knowledge&category=${entry.category}&saved=${entry.id}`;
    await expect(page.locator(`[data-testid="saved-receipt"] a[href="${expected}"]`)).toBeVisible();
  }
  await page.reload();
  await expect(page.getByTestId("saved-receipt").first()).toBeVisible();
  await fs.writeFile(stateFile, JSON.stringify({ entries, originalBody, originalKey, original, second, processIdentity, total: before.total + 5 }), { mode: 0o600 });
});

test("fresh API process preserves owned summaries and compact category navigation", async ({ page }) => {
  test.setTimeout(180_000);
  const { base, auth, processIdentity } = await fixture(page);
  const state = JSON.parse(await fs.readFile(stateFile, "utf8")) as { entries: SavedEntry[]; processIdentity: string; original: Turn; originalBody: string; originalKey: string; total: number };
  expect(processIdentity).not.toBe(state.processIdentity);
  const headers = await login(page, base, auth);
  const otherLogin = await page.request.post(`${base}/auth/login`, { data: auth.other });
  expect(otherLogin.ok()).toBe(true);
  const other = { Authorization: `Bearer ${(await otherLogin.json()).tokens.access_token}` };
  for (const entry of state.entries) {
    const owned = await page.request.get(`${base}/agent/saved/${entry.id}`, { headers });
    expect(owned.ok()).toBe(true);
    expect(await owned.json()).toEqual(entry);
    expect((await page.request.get(`${base}/agent/saved/${entry.id}`, { headers: other })).status()).toBe(404);
    const target = `/journal?${entry.category === "journal" ? "" : `tab=knowledge&category=${entry.category}&`}saved=${entry.id}`;
    await page.goto(target);
    await expect(page.getByRole("heading", { name: entry.title, exact: true })).toBeVisible();
    await expect(page.getByText(entry.summary, { exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "Original conversation", exact: true })).toBeVisible();
    const back = page.getByRole("link", { name: /^← Back to (Journal|Knowledge)$/ }).last();
    await expect(back).toBeVisible();
    await back.click();
    await expect(page).not.toHaveURL(/[?&]saved=/);
  }
  const replay = await page.request.post(`${base}/agent/turns`, { headers: { ...headers, "Idempotency-Key": state.originalKey }, data: JSON.parse(state.originalBody) });
  expect(replay.ok()).toBe(true);
  expect(await replay.json()).toEqual(state.original);
  expect((await (await page.request.get(`${base}/agent/saved?limit=100`, { headers })).json()).total).toBe(state.total);
  await page.goto("/strategies");
  const notes = page.getByTestId("captured-strategy-notes");
  await expect(notes).toBeVisible();
  const strategy = state.entries.find(e => e.category === "strategies")!;
  await expect(notes.getByText(strategy.title, { exact: true })).toBeVisible();
  await notes.getByText(strategy.title, { exact: true }).click();
  await expect(notes.getByRole("link", { name: "Open saved note" })).toHaveAttribute("href", `/journal?tab=knowledge&category=strategies&saved=${strategy.id}`);
  await page.goto("/strategies#risk");
  await expect.poll(() => page.getByText("Risk policy", { exact: true }).evaluate(el => el.closest("details")?.open)).toBe(true);
  await page.goto("/settings");
  await expect(page.getByRole("heading", { name: "Settings", exact: true })).toHaveCount(1);
  await expect(page.getByText("Market Monitoring", { exact: true })).toBeVisible();
  await expect(page.getByText("Notifications", { exact: true }).last()).toBeVisible();
  await expect(page.getByText("Account & System", { exact: true })).toBeVisible();
  await expect(page.getByText("Risk policy", { exact: true })).toHaveCount(0);
  await page.goto("/journal?tab=knowledge&category=lessons");
  await expect(page.getByLabel("Knowledge category")).toHaveValue("lessons");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/strategies");
  await expect(page.getByTestId("captured-strategy-notes")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  const menu = page.getByTestId("header-status-menu");
  await menu.locator("summary").click();
  await menu.getByRole("button", { name: "Log out", exact: true }).click();
  await expect(page).toHaveURL(/\/login/);
  await login(page, base, auth.other);
  await page.goto("/strategies");
  await expect(page.getByTestId("captured-strategy-notes")).toBeVisible();
  await expect(page.getByText(strategy.title, { exact: true })).toHaveCount(0);
});

test("compact screenshots wait for owned records and account changes finish loading", async ({ page }) => {
  test.setTimeout(180_000);
  const { base, auth } = await fixture(page);
  const state = JSON.parse(await fs.readFile(stateFile, "utf8")) as { entries: SavedEntry[] };
  await login(page, base, auth);
  const lesson = state.entries.find(entry => entry.category === "lessons")!;
  const strategy = state.entries.find(entry => entry.category === "strategies")!;
  await page.goto("/journal?tab=knowledge&category=lessons");
  await expect(page.getByText(lesson.title, { exact: true })).toBeVisible();
  await expect(page.getByText("Loading entries…", { exact: true })).toHaveCount(0);
  await page.screenshot({ path: path.resolve("../docs/screenshots/experiments/delivery-knowledge.png"), fullPage: true });
  await page.goto("/strategies");
  await expect(page.getByText(strategy.title, { exact: true })).toBeVisible();
  await expect(page.getByText("Loading experiments…", { exact: true })).toHaveCount(0);
  await page.getByText(strategy.title, { exact: true }).click();
  await expect(page.getByText(strategy.summary, { exact: true })).toBeVisible();
  await page.screenshot({ path: path.resolve("../docs/screenshots/experiments/delivery-strategies.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: path.resolve("../docs/screenshots/experiments/delivery-strategies-mobile.png"), fullPage: true });
  const menu = page.getByTestId("header-status-menu");
  await menu.locator("summary").click();
  await menu.getByRole("button", { name: "Log out", exact: true }).click();
  await expect(page).toHaveURL(/\/login/);
  await login(page, base, auth.other);
  await page.goto("/strategies");
  await expect(page.getByText("No captured strategy notes yet.", { exact: true })).toBeVisible();
  await expect(page.getByText(strategy.title, { exact: true })).toHaveCount(0);
});
