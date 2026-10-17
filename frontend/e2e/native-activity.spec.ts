import { expect, test, type Page } from "@playwright/test";
import { activityOrganization, activityPage, nativeFill } from "../src/test/native-activity-fixtures";
import { attentionFixture, dailyReviewFixture } from "../src/test/pilot-fixtures";
import { SESSION_MARKER_COOKIE, SESSION_MARKER_VALUE } from "../src/lib/auth/boundary";

async function storedHistory(page: Page, baseURL: string) {
  await page.context().addCookies([{ name: SESSION_MARKER_COOKIE, value: SESSION_MARKER_VALUE, url: baseURL }]);
  await page.addInitScript(() => sessionStorage.setItem("alphatrade_access_token", "synthetic-native-history-token"));
  const origin = new URL(process.env.PLAYWRIGHT_API_URL ?? "http://localhost:8000").origin;
  const requests: Array<{ path: string; method: string }> = [];
  const state = { changedAccount: false };
  await page.route(url => url.origin === origin, async route => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    requests.push({ path, method: request.method() });
    const send = (json: unknown, status = 200) => route.fulfill({ json, status, headers: {
      "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "Authorization, Content-Type",
      "Access-Control-Allow-Methods": "GET, OPTIONS",
    } });
    if (request.method() === "OPTIONS") return send({});
    if (request.method() !== "GET") return send({ detail: "Read-only fixture" }, 405);
    if (path === "/auth/me") return send({
      user: { id: activityOrganization, email: "synthetic@example.test", email_verified: true,
        organization_id: activityOrganization, role: "owner", full_name: "Synthetic reader" },
      organization: { id: activityOrganization, name: "Synthetic native history", timezone: "UTC" },
    });
    if (path === "/health") return send({ status: "ok", execution_mode: "paper",
      exchange_mode: "paper_internal", real_trading_enabled: false, enable_real_trading: false,
      paper_only: true, kill_switch_active: false, provider_mode: "mock", providers: {} });
    if (path === "/dashboard/attention") return send(attentionFixture);
    if (path === "/dashboard/daily-review") return send(dailyReviewFixture);
    if (path === "/dashboard/demo-account") return send({
      venue: "BLOFIN_DEMO", read_only: true, status: "not_synced", can_refresh: false,
      account_id: "synthetic", snapshot_id: null, synced_at: null, expires_at: null,
      total_equity_usd: null, refresh_error: null, last_attempt_at: null, balances: [], positions: [],
      balances_truncated: false, positions_truncated: false, position_count: null, message: "No balance snapshot in this fixture.",
    });
    if (path === "/exchange/blofin/activity") {
      if (state.changedAccount && url.searchParams.has("cursor")) return send({ detail: "Foreign account cursor" }, 422);
      const fill = nativeFill({ origin: "alphatrade_matched", command_id: "22222222-2222-4222-8222-222222222222" });
      return send(activityPage({ account_uid: state.changedAccount ? "second-native-account" : "synthetic-native-account",
        items: state.changedAccount ? [] : url.searchParams.has("cursor") ? [fill, nativeFill({ native_id: "fill-2", trade_id: "fill-2" })] : [fill, fill],
        next_cursor: state.changedAccount || url.searchParams.has("cursor") ? null : "synthetic-continuation", freshness: "stale" }));
    }
    if (path === "/agent/saved") return send({ items: [], total: 0 });
    if (path === "/journal/trades") return send({ items: [{
      id: "internal-simulated", symbol: "SIMULATED", timeframe: "1h", direction: "long", status: "closed", result: "win", source: "paper_execution", exchange: "INTERNAL",
    }], total: 1, limit: 50, offset: 0 });
    return send({ detail: "Unrelated fixture source unavailable" }, 503);
  });
  return { state, requests };
}

test("compact native Dashboard keeps exact units and fill counts at 320px", async ({ page, baseURL }) => {
  await page.setViewportSize({ width: 320, height: 720 });
  const fixture = await storedHistory(page, baseURL!);
  await page.goto("/");
  const activity = page.getByTestId("native-activity");
  await expect(activity.getByTestId("native-activity-row")).toHaveCount(1);
  await expect(activity.getByTestId("native-activity-statistics")).toContainText("Native fills shown1");
  await expect(activity).toContainText("Stale at read");
  await expect(activity).toContainText("incomplete coverage");
  const summary = activity.getByTestId("native-activity-row").locator("summary");
  await expect(summary).toContainText("0.123456789123456789 contracts");
  await expect(summary).toContainText("67000.123456789123456789");
  await summary.click();
  await expect(activity.getByText("-0.000123456789123456789 (currency unknown)")).toBeVisible();
  await expect(activity.getByRole("link", { name: "View matched command" })).toBeVisible();
  await activity.getByRole("button", { name: "Back to activity" }).click();
  await expect(activity.getByRole("link", { name: "View matched command" })).not.toBeVisible();
  await expect(summary).toBeFocused();
  for (const button of await activity.getByRole("button").all()) {
    if (await button.isVisible()) expect((await button.boundingBox())?.height).toBeGreaterThanOrEqual(44);
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect(fixture.requests.every(request => ["GET", "OPTIONS"].includes(request.method))).toBe(true);
});

test("actual Journal mounts native history without statistics or simulated records", async ({ page, baseURL }) => {
  await storedHistory(page, baseURL!);
  await page.goto("/journal");
  const activity = page.getByTestId("native-activity");
  await expect(activity.getByTestId("native-activity-row")).toHaveCount(1);
  await expect(page.getByTestId("native-activity-statistics")).toHaveCount(0);
  await expect(page.getByText("SIMULATED", { exact: true })).toHaveCount(0);
  await activity.getByRole("button", { name: "Load older activity" }).click();
  await expect(activity.getByTestId("native-activity-row")).toHaveCount(2);
});

test("a changed native account clears old history and its cursor before a fresh read", async ({ page, baseURL }) => {
  const fixture = await storedHistory(page, baseURL!);
  await page.goto("/journal");
  const activity = page.getByTestId("native-activity");
  await expect(activity.getByTestId("native-activity-row")).toHaveCount(1);
  const readCount = () => fixture.requests.filter(request => request.path === "/exchange/blofin/activity").length;
  const initialReads = readCount();
  fixture.state.changedAccount = true;
  await activity.getByRole("button", { name: "Load older activity" }).click();
  await expect(activity.getByRole("alert")).toBeVisible();
  await expect(activity.getByTestId("native-activity-row")).toHaveCount(0);
  await expect(activity.getByRole("button", { name: "Load older activity" })).toHaveCount(0);
  expect(readCount()).toBe(initialReads + 1);
  await activity.getByRole("button", { name: "Refresh activity" }).click();
  await expect(activity).toContainText("No stored fills");
  expect(readCount()).toBe(initialReads + 2);
});
