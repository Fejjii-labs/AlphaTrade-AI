import { expect, test, type Page } from "@playwright/test";
import { installSmokeSession } from "./helpers/staging-smoke-auth";
import { telegramPolicyFixture } from "../src/components/settings/telegram-policy.fixture";
import {
  SESSION_MARKER_COOKIE,
  SESSION_MARKER_VALUE,
} from "../src/lib/auth/boundary";

test.use({ actionTimeout: 15_000 });

const FIXTURE_ACCESS_TOKEN = "notification-settings-fixture-session";

async function installNotificationFixture(page: Page, supported = true) {
  await installSmokeSession(page, FIXTURE_ACCESS_TOKEN);
  let prefs = {
    in_app_enabled: true,
    telegram_enabled: true,
    webhook_enabled: false,
    min_severity: "info",
    telegram_policy: supported ? telegramPolicyFixture : undefined,
  };
  const writes: Record<string, unknown>[] = [];
  await page.route("**/notifications/preferences", async (route) => {
    if (route.request().method() === "PATCH") {
      const patch = route.request().postDataJSON();
      writes.push(patch);
      prefs = { ...prefs, ...patch };
    }
    await route.fulfill({ json: prefs });
  });
  await page.route("**/alerts/delivery-status", (route) =>
    route.fulfill({
      json: {
        delivery_enabled: false,
        telegram_enabled: false,
        webhook_enabled: false,
        email_enabled: false,
        push_enabled: false,
        effective_external_enabled: false,
        webhook_configured: false,
        channels: [],
        paper_only: true,
        channel_statuses: [
          {
            channel: "telegram",
            env_enabled: false,
            user_enabled: true,
            configured: true,
            available: false,
            status_label: "disabled",
          },
        ],
      },
    }),
  );
  await page.route("**/auth/me", async (route) => {
    expect(route.request().headers().authorization).toBe(
      `Bearer ${FIXTURE_ACCESS_TOKEN}`,
    );
    await route.fulfill({
      json: {
        user: {
          id: "fixture-user",
          email: "fixture@example.com",
          email_verified: true,
          role: "admin",
          is_active: true,
        },
        organization: {
          id: "fixture-org",
          name: "Fixture",
          slug: "fixture",
          is_active: true,
        },
      },
    });
  });
  await page.route((url) => url.pathname === "/execution/accounts/paper", async (route) => {
    if (route.request().method() !== "GET") {
      await route.fallback();
      return;
    }
    expect(route.request().headers().authorization).toBe(
      `Bearer ${FIXTURE_ACCESS_TOKEN}`,
    );
    await route.fulfill({
      status: 503,
      json: { detail: "Unavailable in notification fixture" },
    });
  });
  // Retained manual-history routes must also keep this fixture session usable.
  // Keep its synthetic token inside this fixture: a real backend 401 correctly
  // expires the session, whereas an unrelated read outage must keep it usable.
  await page.route((url) => url.pathname === "/execution/manual-demo/commands", async (route) => {
    expect(route.request().method()).toBe("GET");
    expect(route.request().headers().authorization).toBe(
      `Bearer ${FIXTURE_ACCESS_TOKEN}`,
    );
    await route.fulfill({
      status: 503,
      json: { detail: "Manual demo history unavailable in notification fixture" },
    });
  });
  // Unrelated Settings reads are deliberately unavailable, never fabricated.
  // Watchlist reads must not reach the backend with the synthetic fixture token:
  // a real 401 would correctly clear the session and redirect to /login.
  await page.route(
    (url) =>
      /\/(health|providers\/status|risk\/|market-watcher\/|strategies\/|watcher\/watchlist(?:\/status)?$|watcher\/assignments$)/.test(
        url.pathname,
      ),
    (route) =>
      route.fulfill({
        status: 503,
        json: { detail: "Unavailable in notification fixture" },
      }),
  );
  return writes;
}

async function expectNotificationSession(page: Page) {
  await expect(page).toHaveURL(/\/settings\/notifications$/);
  const marker = (await page.context().cookies(page.url())).find(
    (cookie) => cookie.name === SESSION_MARKER_COOKIE,
  );
  expect(marker).toMatchObject({
    value: SESSION_MARKER_VALUE,
    domain: new URL(page.url()).hostname,
    path: "/",
    httpOnly: false,
    sameSite: "Lax",
  });
  expect(
    await page.evaluate(() => sessionStorage.getItem("alphatrade_access_token")),
  ).toBe(FIXTURE_ACCESS_TOKEN);
  await expect(
    page.getByRole("link", { name: "Notifications", exact: true }),
  ).toHaveAttribute("aria-current", "page");
}

for (const viewport of [
  { width: 390, height: 844 },
  { width: 320, height: 740 },
]) {
  test(`notification policy persists at mobile width ${viewport.width}`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    const writes = await installNotificationFixture(page);
    await page.goto("/settings#notifications");
    const form = page.getByTestId("telegram-policy-v2");
    await expect(form).toBeVisible();
    await expect(page.getByTestId("settings-telegram-state")).toContainText(
      "disabled",
    );
    await expectNotificationSession(page);
    await expect(
      page.getByRole("region", { name: "Recent manual demo activity" }),
    ).toHaveCount(0);
    await expect(
      form.getByLabel("Enable Telegram notification policy"),
    ).toBeChecked();
    await expect(
      form.getByText(/SFP lifecycle notifications use the shared/),
    ).toBeVisible();
    const nav = page.getByTestId("mobile-bottom-navigation");
    await expect(nav).toBeVisible();
    await expect(nav.getByRole("link")).toHaveCount(5);
    await form
      .getByLabel("Watched symbols", { exact: true })
      .selectOption("none");
    await form.getByLabel("Minimum quality threshold").fill("0");
    await form.getByLabel("Cooldown (seconds)").fill("300");
    await form.getByText("Event preferences", { exact: true }).click();
    await form.getByLabel("Paper trade opened").uncheck();
    await form.getByText("Quiet hours", { exact: true }).click();
    await form.getByLabel("Quiet hours timezone").fill("UTC");
    await form.getByRole("button", { name: "Save Telegram policy" }).click();
    await expect(
      page.getByRole("status").filter({ hasText: "Telegram policy saved" }),
    ).toBeVisible();
    expect(writes).toEqual([
      {
        telegram_enabled: true,
        telegram_policy: {
          ...telegramPolicyFixture,
          symbol_subscriptions: [],
          minimum_quality: 0,
          cooldown_seconds: 300,
          paper_trade_opened: false,
          quiet_hours: {
            ...telegramPolicyFixture.quiet_hours,
            timezone: "UTC",
          },
        },
      },
    ]);
    await page.reload();
    await expect(
      form.getByLabel("Watched symbols", { exact: true }),
    ).toHaveValue("none");
    await expect(form.getByLabel("Cooldown (seconds)")).toHaveValue("300");
    await expectNotificationSession(page);
    await expect(
      page.getByRole("region", { name: "Recent manual demo activity" }),
    ).toHaveCount(0);
    expect(
      await page.evaluate(
        () =>
          document.documentElement.scrollWidth >
          document.documentElement.clientWidth + 1,
      ),
    ).toBe(false);
    await page.screenshot({
      path: `test-results/notifications-v2-mobile-${viewport.width}.png`,
      fullPage: true,
    });
    await page.getByRole("link", { name: "Watcher", exact: true }).click();
    await expect(page).toHaveURL(/\/settings\/watcher$/);
    await expect(
      page.getByText("Watchlist configuration unavailable. Reload to try again."),
    ).toBeVisible();
    await page.getByRole("link", { name: "Account", exact: true }).click();
    await expect(page).toHaveURL(/\/settings$/);
    const account = page.getByTestId("settings-workspace");
    await expect(
      account.getByText("fixture@example.com", { exact: true }),
    ).toBeVisible();
    await expect(account.getByText("Fixture", { exact: true })).toBeVisible();
  });
}

test("unsupported Policy V2 has an unavailable label and no policy form", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const writes = await installNotificationFixture(page, false);
  await page.goto("/settings#notifications");
  await expect(
    page.getByText(/Telegram Policy V2 settings: Unavailable/),
  ).toBeVisible();
  await expect(page.getByTestId("telegram-policy-v2")).toHaveCount(0);
  await expectNotificationSession(page);
  expect(writes).toEqual([]);
});
