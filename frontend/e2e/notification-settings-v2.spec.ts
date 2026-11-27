import { expect, test, type Page } from "@playwright/test";
import { installSmokeSession } from "./helpers/staging-smoke-auth";
import { telegramPolicyFixture } from "../src/components/settings/telegram-policy.fixture";
import {
  SESSION_MARKER_COOKIE,
  SESSION_MARKER_VALUE,
} from "../src/lib/auth/boundary";

test.use({ actionTimeout: 15_000 });

const FIXTURE_ACCESS_TOKEN = "notification-settings-fixture-session";
/** Setup alerts muted: event types emptied, the other saved filters kept. */
const mutedPolicy = { ...telegramPolicyFixture, event_types: [], minimum_quality: 50 };

async function installNotificationFixture(page: Page, supported = true) {
  await installSmokeSession(page, FIXTURE_ACCESS_TOKEN);
  let prefs = {
    in_app_enabled: true,
    telegram_enabled: false,
    webhook_enabled: false,
    min_severity: "info",
    telegram_policy: supported ? mutedPolicy : undefined,
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
      /\/(health|providers\/status|dashboard\/attention$|risk\/|market-watcher\/|strategies\/|watcher\/watchlist(?:\/status)?$|watcher\/assignments$|notifications\/filter-options$)/.test(
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
  await expect(page).toHaveURL(/\/settings\/alerts$/);
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
    page.getByRole("link", { name: "Alerts", exact: true }),
  ).toHaveAttribute("aria-current", "page");
}

for (const viewport of [
  { width: 390, height: 844 },
  { width: 320, height: 740 },
]) {
  test(`setup alerts switch persists at mobile width ${viewport.width}`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    const writes = await installNotificationFixture(page);
    await page.goto("/settings#notifications");
    const form = page.getByTestId("setup-alerts-form");
    await expect(form).toBeVisible();
    await expect(page.getByTestId("settings-telegram-state")).toContainText(
      "Not connected",
    );
    await expect(page.getByTestId("always-sent")).toContainText("Daily summary");
    await expectNotificationSession(page);
    await expect(
      page.getByRole("region", { name: "Recent manual demo activity" }),
    ).toHaveCount(0);
    const nav = page.getByTestId("mobile-bottom-navigation");
    await expect(nav).toBeVisible();
    await expect(nav.getByRole("link")).toHaveCount(4);
    await expect(nav.getByRole("button", { name: "More" })).toHaveAttribute("data-active", "true");
    const setup = form.getByRole("switch", { name: "Setup alerts" });
    await expect(setup).toHaveAttribute("aria-checked", "false");
    await setup.click();
    await form.getByRole("button", { name: "Save changes" }).click();
    await expect(
      page.getByRole("status").filter({ hasText: "Alert settings saved" }),
    ).toBeVisible();
    expect(writes).toHaveLength(1);
    expect(writes[0]).toMatchObject({
      telegram_enabled: true,
      telegram_policy: {
        ...mutedPolicy,
        event_types: null,
        minimum_quality: null,
        minimum_severity: "INFO",
        severities: null,
        setup_stages: null,
        forming_alerts: true,
        daily_review_event: true,
      },
    });
    await page.reload();
    await expect(
      page.getByTestId("setup-alerts-form").getByRole("switch", { name: "Setup alerts" }),
    ).toHaveAttribute("aria-checked", "true");
    await expectNotificationSession(page);
    expect(
      await page.evaluate(
        () =>
          document.documentElement.scrollWidth >
          document.documentElement.clientWidth + 1,
      ),
    ).toBe(false);
    await page.screenshot({
      path: `test-results/notifications-mobile-${viewport.width}.png`,
      fullPage: true,
    });
    await page.getByRole("link", { name: "Account", exact: true }).click();
    await expect(page).toHaveURL(/\/settings$/);
    const account = page.getByTestId("settings-workspace");
    await expect(
      account.getByText("fixture@example.com", { exact: true }),
    ).toBeVisible();
    await expect(account.getByText("Fixture", { exact: true })).toBeVisible();
  });
}

test("missing setup alert settings show a plain label and no form", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const writes = await installNotificationFixture(page, false);
  await page.goto("/settings#notifications");
  await expect(
    page.getByText("Setup alert settings are not available right now."),
  ).toBeVisible();
  await expect(page.getByTestId("setup-alerts-form")).toHaveCount(0);
  await expectNotificationSession(page);
  expect(writes).toEqual([]);
});
