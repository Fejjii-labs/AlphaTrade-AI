import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs/promises";
import path from "node:path";

type Fixture = { email: string; password: string; conversation_id: string };
async function login(page: Page) {
  const api = process.env.PLAYWRIGHT_API_URL!;
  const authFile = process.env.EXPERIMENT_BROWSER_AUTH_FILE!;
  for (const url of [api, process.env.PLAYWRIGHT_BASE_URL!])
    expect(["127.0.0.1", "localhost"]).toContain(new URL(url).hostname);
  expect(path.resolve(authFile).startsWith("/tmp/")).toBe(true);
  expect((await fs.stat(authFile)).mode & 0o777).toBe(0o600);
  const auth = JSON.parse(await fs.readFile(authFile, "utf8")) as Fixture;
  const health = await page.request.get(`${api}/health`);
  expect(health.headers()["x-synthetic-fixture-process"]).toBeTruthy();
  expect((await health.json()).real_trading_enabled).toBe(false);
  await page.goto("/login");
  await page.getByLabel("Email", { exact: true }).fill(auth.email);
  await page.getByLabel("Password", { exact: true }).fill(auth.password);
  const response = page.waitForResponse(r => r.url() === `${api}/auth/login` && r.request().method() === "POST");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  expect((await response).ok()).toBe(true);
  await page.waitForURL(url => url.pathname !== "/login");
  await page.goto(`/agent?conversation=${auth.conversation_id}`);
  return api;
}
async function composerIsReachable(page: Page, mobile: boolean) {
  const textarea = page.getByLabel("Message", { exact: true });
  const send = page.getByRole("button", { name: "Send", exact: true });
  const bottomBoundary = mobile ? 72 : 0;
  for (const locator of [textarea, send]) {
    await expect.poll(async () => {
      const box = await locator.boundingBox();
      return !!box && box.y >= 64 && box.y + box.height <= (page.viewportSize()!.height - bottomBoundary);
    }).toBe(true);
  }
}
for (const [name, viewport] of [
  ["desktop", { width: 1440, height: 900 }],
  ["mobile", { width: 390, height: 844 }],
] as const) {
  test(`${name} long conversation follows latest and preserves deliberate older reading`, async ({ page }) => {
    test.setTimeout(180_000);
    await page.setViewportSize(viewport);
    const api = await login(page);
    await expect(page.getByTestId("agent-message").filter({ hasText: "Synthetic viewport message 129." })).toBeVisible();
    await composerIsReachable(page, name === "mobile");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    // Current history is not silently truncated to the API's first page.
    expect(await page.getByTestId("agent-message").count()).toBeGreaterThanOrEqual(100);
    expect(await page.getByText("Synthetic viewport message 000.", { exact: false }).count()).toBe(0);
    // Real server commits the next turn, but the browser acknowledgment is delayed
    // while the user chooses to read older messages. No fake transcript or UI response.
    let committed!: () => void;
    let release!: () => void;
    const didCommit = new Promise<void>(resolve => { committed = resolve; });
    const released = new Promise<void>(resolve => { release = resolve; });
    let calls = 0;
    await page.route(`${api}/agent/turns`, async route => {
      calls++;
      const response = await route.fetch();
      expect(response.ok()).toBe(true);
      committed();
      await released;
      await route.fulfill({ response });
    });
    await page.getByLabel("Message", { exact: true }).fill(`Synthetic ${name} viewport continuation; no execution request.`);
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await didCommit;
    await page.mouse.wheel(0, -1800);
    await expect(page.getByRole("button", { name: "Latest messages ↓", exact: true })).toBeVisible();
    // Wait for the deliberate scroll itself, not an arbitrary time delay.
    const anchor = page.getByTestId("agent-message").nth(80);
    await expect.poll(async () => (await anchor.boundingBox())?.y ?? 0).toBeLessThan(viewport.height);
    const before = await anchor.boundingBox();
    release();
    await expect(page.getByLabel("Message", { exact: true })).toHaveValue("");
    await expect.poll(async () => Math.abs((await anchor.boundingBox())!.y - before!.y)).toBeLessThan(8);
    expect(calls).toBe(1);
    await page.getByRole("button", { name: "Latest messages ↓", exact: true }).click();
    await composerIsReachable(page, name === "mobile");
    await page.unroute(`${api}/agent/turns`);
    const next = page.waitForResponse(r => r.url() === `${api}/agent/turns` && r.request().method() === "POST");
    await page.getByLabel("Message", { exact: true }).fill(`Synthetic ${name} pinned continuation.`);
    await page.getByRole("button", { name: "Send", exact: true }).click();
    expect((await next).ok()).toBe(true);
    await expect(page.getByLabel("Message", { exact: true })).toHaveValue("");
    await composerIsReachable(page, name === "mobile");
    // Loading an earlier page must keep an already-visible old message in place.
    await page.mouse.wheel(0, -100000);
    const earlier = page.getByRole("button", { name: "Earlier messages", exact: true });
    await expect(earlier).toBeInViewport();
    const firstId = await page.getByTestId("agent-message").first().getAttribute("data-message-id");
    const first = page.locator(`[data-message-id="${firstId}"]`);
    const firstBefore = await first.boundingBox();
    await earlier.click();
    await expect(page.getByTestId("agent-message").filter({ hasText: "Synthetic viewport message 000." })).toHaveCount(1);
    await expect.poll(async () => Math.abs((await first.boundingBox())!.y - firstBefore!.y)).toBeLessThan(8);
    await page.getByRole("button", { name: "Latest messages ↓", exact: true }).click();
    await composerIsReachable(page, name === "mobile");
    await page.screenshot({ path: `../docs/screenshots/experiments/conversation-${name}.png` });
  });
}
test("mobile compact experiments and Knowledge filters keep provenance and hide irrelevant pages", async ({ page }) => {
  test.setTimeout(180_000);
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page);
  await page.goto("/strategies");
  const cards = page.getByTestId("experiment-card");
  await expect(cards).toHaveCount(3);
  for (const card of await cards.all()) {
    await expect(card.getByText("Performance unavailable", { exact: true })).toBeVisible();
    await expect(card.getByText("Simulation", { exact: true })).toBeVisible();
    await expect(card.locator("details")).not.toHaveAttribute("open");
    expect((await card.boundingBox())!.height).toBeLessThan(270);
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: "../docs/screenshots/experiments/compact-experiments-mobile.png" });
  await page.goto("/journal?tab=knowledge");
  const filters = page.getByTestId("knowledge-filters");
  await expect(filters.getByLabel("Knowledge category")).toBeVisible();
  await expect(filters.getByRole("button", { name: "Refresh", exact: true })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Saved notes pages" })).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "Documents pages" })).toHaveCount(0);
  expect((await filters.boundingBox())!.height).toBeLessThan(115);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: "../docs/screenshots/experiments/compact-knowledge-mobile.png" });
});
