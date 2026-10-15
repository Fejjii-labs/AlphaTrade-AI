import { test, expect, type Page } from "@playwright/test";
import fs from "node:fs/promises";
import path from "node:path";

type Fixture = { email: string; password: string; other: { email: string; password: string } };
const prefix = "alphatrade:watcher-assignments:";

async function fixture(page: Page) {
  const api = process.env.PLAYWRIGHT_API_URL!;
  const frontend = process.env.PLAYWRIGHT_BASE_URL!;
  const file = process.env.VOICE_MONITORING_BROWSER_AUTH_FILE!;
  expect(api && frontend && file, "Provide both loopback URLs and the private synthetic login file.").toBeTruthy();
  for (const value of [api, frontend]) expect(["127.0.0.1", "localhost"]).toContain(new URL(value).hostname);
  expect(path.resolve(file).startsWith("/tmp/")).toBe(true);
  expect((await fs.stat(file)).mode & 0o777).toBe(0o600);
  const auth = JSON.parse(await fs.readFile(file, "utf8")) as Fixture;
  const health = await (await page.request.get(`${api}/health`)).json();
  expect(health.execution_mode).toBe("paper");
  expect(health.real_trading_enabled).toBe(false);
  return { api, auth };
}

async function login(page: Page, api: string, auth: { email: string; password: string }) {
  await page.goto("/login");
  await page.getByLabel("Email", { exact: true }).fill(auth.email);
  await page.getByLabel("Password", { exact: true }).fill(auth.password);
  const response = page.waitForResponse(r => r.url() === `${api}/auth/login` && r.request().method() === "POST");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  const result = await response;
  expect(result.ok()).toBe(true);
  const body = await result.json();
  await page.waitForURL(url => url.pathname !== "/login");
  await expect(page.getByTestId("header-status-menu")).toBeVisible();
  return { Authorization: `Bearer ${body.tokens.access_token}` };
}

async function openMonitoring(page: Page) {
  await page.goto("/settings#markets");
  const panel = page.getByTestId("monitoring-assignments");
  await expect(panel.getByLabel("Saved strategy version")).toBeVisible();
  await expect(panel.getByText("Loading monitoring…")).toHaveCount(0);
  return panel;
}

async function recoveryKeys(page: Page) {
  return page.evaluate(prefix => Object.keys(sessionStorage).filter(key => key.startsWith(prefix)), prefix);
}

test("desktop assignments save, reload and toggle independently against the actual API", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const { api, auth } = await fixture(page);
  const headers = await login(page, api, auth);
  const panel = await openMonitoring(page);
  const options = await panel.getByLabel("Saved strategy version").locator("option").evaluateAll(elements => elements.map(el => ({ value: (el as HTMLOptionElement).value, text: el.textContent })).filter(el => el.value && el.text?.includes("Synthetic monitor") && el.text.includes("v2")));
  expect(options.length).toBeGreaterThanOrEqual(2);
  for (const option of options.slice(0, 2)) {
    await panel.getByLabel("Saved strategy version").selectOption(option.value);
    await panel.getByRole("button", { name: "Add", exact: true }).click();
  }
  await panel.getByRole("button", { name: "Save monitoring", exact: true }).click();
  await expect(panel.getByText("Monitoring saved.")).toBeVisible();
  await expect(panel.getByTestId("monitoring-assignment")).toHaveCount(2);
  const read = async () => (await page.request.get(`${api}/watcher/assignments`, { headers })).json();
  const saved = await read();
  expect(saved.monitoring_only).toBe(true);
  expect(saved.assignments.every((row: { monitoring_enabled: boolean; eligible: boolean }) => row.monitoring_enabled && row.eligible)).toBe(true);
  await page.reload();
  await expect(panel.getByTestId("monitoring-assignment")).toHaveCount(2);
  await panel.getByRole("checkbox").first().uncheck();
  await panel.getByRole("button", { name: "Save monitoring", exact: true }).click();
  await expect(panel.getByText("Monitoring saved.")).toBeVisible();
  const toggled = await read();
  expect(toggled.assignments.filter((row: { monitoring_enabled: boolean }) => row.monitoring_enabled)).toHaveLength(1);
  expect(toggled.assignments.map((row: { id: string }) => row.id).sort()).toEqual(saved.assignments.map((row: { id: string }) => row.id).sort());
  await expect(panel.getByText(/monitoring disabled/)).toBeVisible();
  await panel.getByRole("button", { name: "Details", exact: true }).first().click();
  await expect(page.getByTestId("monitoring-assignment-details").getByText(/Saved parameters are immutable/)).toBeVisible();
  await page.getByRole("button", { name: "Back to monitoring", exact: true }).click();
  await expect(panel).toBeVisible();
  await fs.mkdir("/tmp/alphatrade-voice-monitoring-screenshots", { recursive: true });
  await page.screenshot({ path: "/tmp/alphatrade-voice-monitoring-screenshots/monitoring-desktop.png", fullPage: true });
});

test("mobile ambiguous save reloads the exact identity and explicit recovery deduplicates", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const { api, auth } = await fixture(page);
  const headers = await login(page, api, auth);
  const panel = await openMonitoring(page);
  const toggle = panel.getByRole("checkbox").last();
  await toggle.setChecked(!(await toggle.isChecked()));
  let calls = 0, original = "";
  await page.route(`${api}/watcher/assignments`, async route => {
    if (route.request().method() !== "PUT") return route.continue();
    calls++;
    const body = route.request().postData()!;
    if (calls === 1) original = body;
    else expect(body).toBe(original);
    const actual = await route.fetch();
    expect(actual.ok()).toBe(true);
    await route.fulfill({ response: actual, ...(calls === 1 ? { body: '{"unexpected":true}' } : {}) });
  });
  await panel.getByRole("button", { name: "Save monitoring", exact: true }).click();
  await expect(panel.getByRole("button", { name: "Recover saved request" })).toBeEnabled();
  expect(calls).toBe(1);
  const keys = await recoveryKeys(page);
  expect(keys).toHaveLength(1);
  const committed = await (await page.request.get(`${api}/watcher/assignments`, { headers })).json();
  await page.reload();
  await expect(panel.getByRole("button", { name: "Recover saved request" })).toBeEnabled();
  expect(await recoveryKeys(page)).toEqual(keys);
  expect(calls).toBe(1);
  await panel.getByRole("button", { name: "Recover saved request" }).click();
  await expect(panel.getByText("Monitoring saved.")).toBeVisible();
  expect(calls).toBe(2);
  const recovered = await (await page.request.get(`${api}/watcher/assignments`, { headers })).json();
  expect(recovered).toEqual(committed);
  expect(await recoveryKeys(page)).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: "/tmp/alphatrade-voice-monitoring-screenshots/monitoring-mobile.png", fullPage: true });
});

test("off-panel real logout clears ambiguous assignment recovery before foreign-account login", async ({ page }) => {
  const { api, auth } = await fixture(page);
  await login(page, api, auth);
  const panel = await openMonitoring(page);
  const toggle = panel.getByRole("checkbox").first();
  await toggle.setChecked(!(await toggle.isChecked()));
  let calls = 0;
  await page.route(`${api}/watcher/assignments`, async route => {
    if (route.request().method() !== "PUT") return route.continue();
    calls++;
    const actual = await route.fetch();
    expect(actual.ok()).toBe(true);
    await route.fulfill({ response: actual, body: '{"unexpected":true}' });
  });
  await panel.getByRole("button", { name: "Save monitoring", exact: true }).click();
  await expect(panel.getByRole("button", { name: "Recover saved request" })).toBeVisible();
  await page.goto("/journal");
  await expect(panel).toHaveCount(0);
  expect(await recoveryKeys(page)).toHaveLength(1);
  const menu = page.getByTestId("header-status-menu");
  await menu.locator("summary").click();
  await menu.getByRole("button", { name: "Log out", exact: true }).click();
  await expect(page).toHaveURL(/\/login/);
  expect(await recoveryKeys(page)).toEqual([]);
  const headers = await login(page, api, auth.other);
  const fresh = await openMonitoring(page);
  await expect(fresh.getByTestId("monitoring-assignment")).toHaveCount(0);
  await expect(fresh.getByRole("button", { name: "Recover saved request" })).toHaveCount(0);
  const read = await (await page.request.get(`${api}/watcher/assignments`, { headers })).json();
  expect(read.assignments).toEqual([]);
  expect(calls).toBe(1);
});

test("unbound interactive provider remains honest while actual text reply persists", async ({ page }) => {
  const { api, auth } = await fixture(page);
  const headers = await login(page, api, auth);
  await page.goto("/agent");
  const config = page.waitForResponse(r => r.url() === `${api}/agent/voice/config`);
  await page.getByRole("button", { name: "Talk to Agent", exact: true }).click();
  expect((await (await config).json()).available).toBe(false);
  await expect(page.getByText("Interactive voice is unavailable. Continue typing.")).toBeVisible();
  const message = `Synthetic typed continuity ${crypto.randomUUID()}`;
  await page.getByRole("textbox", { name: "Message", exact: true }).fill(message);
  const turn = page.waitForResponse(r => r.url() === `${api}/agent/turns` && r.request().method() === "POST");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  const result = await turn;
  expect(result.ok()).toBe(true);
  const body = await result.json();
  const visibleReply = body.reply.split("\n\nRecorded facts (not a confirmation):\n")[0];
  await expect(page.getByText(visibleReply, { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText(message, { exact: true })).toBeVisible();
  await expect(page.getByText(visibleReply, { exact: true })).toBeVisible();
  const history = await (await page.request.get(`${api}/conversations/${body.conversation_id}/messages`, { headers })).json();
  expect(history.items.some((row: { id: string; content: string }) => row.id === body.assistant_message_id && row.content === body.reply)).toBe(true);
  await page.screenshot({ path: "/tmp/alphatrade-voice-monitoring-screenshots/agent-typed-fallback.png", fullPage: true });
});
