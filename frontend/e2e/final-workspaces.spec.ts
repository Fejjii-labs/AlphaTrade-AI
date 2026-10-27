import { expect, test } from "@playwright/test";
import { installSharedE2ESession, paperModeActive } from "./helpers/shared-e2e-auth";

const workspaces = [
  ["Dashboard", "/"], ["Agent", "/agent"], ["Trades", "/journal"],
  ["Strategies", "/strategies"], ["Knowledge", "/knowledge"],
  ["Settings", "/settings"], ["Watcher", "/watcher"], ["Signals", "/tradingview-signals"],
];

for (const viewport of [
  { name: "desktop", width: 1440, height: 1000 },
  { name: "phone", width: 390, height: 844 },
  { name: "phone-landscape", width: 844, height: 390 },
]) {
  test(`${viewport.name}: primary workspaces load against the local paper API`, async ({ page, request }) => {
    const health = await request.get(`${process.env.PLAYWRIGHT_API_URL ?? "http://localhost:8000"}/health`);
    expect(await health.json()).toMatchObject({ execution_mode: "paper", real_trading_enabled: false, exchange_mode: "paper_internal", telegram_network_permitted: false });
    await page.setViewportSize(viewport);
    await installSharedE2ESession(page, request);
    const errors: string[] = [];
    const failures: string[] = [];
    const writes: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("response", (response) => { if (response.status() >= 500) failures.push(`${response.status()} ${new URL(response.url()).pathname}`); });
    await page.route("**/*", (route) => {
      const url = new URL(route.request().url());
      if (!new Set(["localhost", "127.0.0.1"]).has(url.hostname)) return route.abort();
      if (url.port === "8000" && route.request().method() !== "GET") {
        writes.push(`${route.request().method()} ${url.pathname}`);
        return route.abort();
      }
      return route.continue();
    });
    for (const [name, href] of workspaces) {
      await page.goto(href);
      await expect(page.locator("#main").getByRole("heading", { level: 1 }).first(), name).toBeVisible();
      await expect(page.locator("#main").getByTestId("loading-state")).toHaveCount(0);
      await expect(page.locator("#main").getByTestId("error-state")).toHaveCount(0);
      await expect(paperModeActive(page)).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${name} fits`).toBe(true);
      const nav = viewport.width >= 1024 ? page.getByRole("navigation", { name: "Primary destinations" }) : page.getByTestId("mobile-bottom-navigation");
      await expect(nav).toBeVisible();
      await expect(nav.getByRole("link")).toHaveCount(viewport.width >= 1024 ? 7 : 4);
    }
    expect(writes).toEqual([]);
    expect(failures).toEqual([]);
    expect(errors).toEqual([]);
  });
}
