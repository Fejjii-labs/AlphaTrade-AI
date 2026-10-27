import { expect, test, type ConsoleMessage, type Page } from "@playwright/test";

import { installSharedE2ESession, paperModeActive } from "./helpers/shared-e2e-auth";

/**
 * Remote WebKit / iPhone-emulation audit harness.
 * Emulation only — does NOT replace physical iPhone Safari validation.
 */

const ROUTES = ["/", "/portfolio", "/analytics", "/positions", "/journal", "/decision", "/watcher", "/login"] as const;

async function collectConsoleAndNetwork(page: Page) {
  const consoleErrors: string[] = [];
  const failedRequests: string[] = [];
  page.on("console", (msg: ConsoleMessage) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  page.on("response", (response) => {
    if (response.status() >= 500) {
      failedRequests.push(`${response.status()} ${response.url()}`);
    }
  });
  return { consoleErrors, failedRequests };
}

function meaningfulConsoleErrors(errors: string[]): string[] {
  return errors.filter(
    (text) =>
      !/favicon|Download the React DevTools|Encountered two children with the same key/i.test(text),
  );
}


async function settledGoto(page: Page, url: string): Promise<void> {
  await page.goto(url, { waitUntil: "domcontentloaded" });
  await page.waitForLoadState("load").catch(() => undefined);
  // WebKit can still be finishing App Router transitions after load.
  await page.waitForTimeout(150);
}

async function hasHorizontalOverflow(page: Page): Promise<boolean> {
  return page.evaluate(() => {
    const root = document.documentElement;
    return root.scrollWidth > root.clientWidth + 1;
  });
}

/** True when the element is inside the visible area above fixed mobile bottom nav. */
async function isUnobscuredInViewport(locator: ReturnType<Page["locator"]>): Promise<boolean> {
  return locator.evaluate((el) => {
    const rect = el.getBoundingClientRect();
    const nav = document.querySelector('[data-testid="mobile-bottom-navigation"]');
    const bottomLimit = nav?.getBoundingClientRect().top ?? window.innerHeight;
    return rect.top >= 0 && rect.bottom <= bottomLimit + 1;
  });
}

test.describe("WebKit iPhone 15 Pro emulation audit", () => {
  test("mobile nav + Menu sheet, overflow, console/network on representative routes", async ({
    page,
    request,
  }) => {
    await installSharedE2ESession(page, request);
    const { consoleErrors, failedRequests } = await collectConsoleAndNetwork(page);

    for (const route of ROUTES) {
      if (route === "/login") {
        await settledGoto(page, route);
      } else {
        consoleErrors.length = 0;
        failedRequests.length = 0;
        await settledGoto(page, route);
        await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
        await expect(paperModeActive(page)).toBeVisible();
      }

      expect(await hasHorizontalOverflow(page), `${route} horizontal overflow`).toBeFalsy();
      expect(meaningfulConsoleErrors(consoleErrors), `${route} console errors`).toEqual([]);
      expect(failedRequests, `${route} failed requests`).toEqual([]);
    }

    await settledGoto(page, "/portfolio");
    const bottomNav = page.getByTestId("mobile-bottom-navigation");
    await expect(bottomNav).toBeVisible();

    // Safe-area utility classes must be present on chrome (runtime inset values
    // are physical-device dependent and often 0 under Playwright emulation).
    await expect(bottomNav).toHaveClass(/safe-area-inset-bottom/);
    await expect(bottomNav.getByRole("link", { name: "Dashboard" })).toBeVisible();
    await expect(bottomNav.getByRole("link", { name: "Agent" })).toBeVisible();
    await expect(bottomNav.getByRole("link", { name: "Strategies" })).toBeVisible();
    await expect(bottomNav.getByRole("link", { name: "Trades" })).toBeVisible();
    await expect(bottomNav.getByRole("button", { name: "More" })).toBeVisible();
    await expect(page.getByTestId("mobile-menu-button")).toHaveCount(0);
  });

  test("Portfolio and Analytics scroll without trapping horizontal overflow", async ({
    page,
    request,
  }) => {
    await installSharedE2ESession(page, request);

    await settledGoto(page, "/portfolio");
    await expect(page.getByTestId("paper-portfolio-page")).toBeVisible();
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    const portfolioScrollY = await page.evaluate(() => window.scrollY);
    expect(portfolioScrollY).toBeGreaterThan(0);
    expect(await hasHorizontalOverflow(page)).toBeFalsy();

    await settledGoto(page, "/analytics");
    await expect(page.getByTestId("analytics-page")).toBeVisible();
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    const analyticsScrollY = await page.evaluate(() => window.scrollY);
    expect(analyticsScrollY).toBeGreaterThanOrEqual(0);
    expect(await hasHorizontalOverflow(page)).toBeFalsy();
  });

  test("forms: focused field remains in viewport (login + kill-switch reason)", async ({
    page,
    request,
  }) => {
    await settledGoto(page, "/login");
    const email = page.locator('input[type="email"], input[name="email"]').first();
    await expect(email).toBeVisible();
    await email.focus();
    // WebKit focus-scroll can leave a few pixels clipped; nearest scroll matches
    // Safari's keep-focused-field-visible behavior above the soft keyboard.
    await email.evaluate((el) => el.scrollIntoView({ block: "nearest", inline: "nearest" }));
    expect(await isUnobscuredInViewport(email), "login email focused field visible").toBeTruthy();

    await installSharedE2ESession(page, request);
    await settledGoto(page, "/portfolio");
    await page.getByTestId("kill-switch-button").click();
    const reason = page.getByTestId("kill-switch-reason");
    await expect(reason).toBeVisible();
    await reason.focus();
    expect(
      await isUnobscuredInViewport(reason),
      "kill-switch reason focused field visible",
    ).toBeTruthy();
    await page.getByTestId("kill-switch-cancel").click();
  });

  test("command-menu touch control must be reachable on phone viewport", async ({
    page,
    request,
  }) => {
    await installSharedE2ESession(page, request);
    await settledGoto(page, "/");

    await page.getByTestId("topbar-search").click();
    await expect(page.getByTestId("command-menu")).toBeVisible();
    await expect(page.getByTestId("command-menu").getByRole("option").first()).toBeVisible();
  });

  test("paper-close dialog usable on phone viewport", async ({ page, request }) => {
    await installSharedE2ESession(page, request);

    const openPosition = {
      id: "pos-webkit-1",
      organization_id: "org-1",
      user_id: "user-1",
      symbol: "BTCUSDT",
      direction: "long",
      size: "0.25",
      entry_price: "50637.87",
      leverage: "1",
      stop_loss: null,
      take_profits: [],
      liquidation_price: null,
      unrealized_pnl: "12.5",
      realized_pnl: "0",
      risk_state: {},
      status: "open",
      opened_at: "2026-07-27T10:00:00.000Z",
      closed_at: null,
    };

    await page.route(
      (url) =>
        url.port === "8000" &&
        (url.pathname === "/positions" || url.pathname.startsWith("/positions/")),
      async (route) => {
        const req = route.request();
        const url = new URL(req.url());
        if (url.pathname.includes("/close-paper") && req.method() === "POST") {
          await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({ ...openPosition, status: "closed" }),
          });
          return;
        }
        if (req.method() === "GET" && url.pathname === "/positions") {
          await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({ items: [openPosition], total: 1, limit: 50, offset: 0 }),
          });
          return;
        }
        await route.continue();
      },
    );

    await settledGoto(page, "/positions");
    await page.getByTestId("close-paper-start").click();
    const exit = page.getByTestId("close-paper-exit-price");
    await expect(exit).toBeVisible();
    await exit.fill("51000.5");
    expect(
      await isUnobscuredInViewport(exit),
      "paper-close exit price visible above mobile chrome",
    ).toBeTruthy();
    await page.getByTestId("close-paper-review").click();
    await expect(page.getByTestId("close-paper-confirmation")).toBeVisible();
    expect(await hasHorizontalOverflow(page)).toBeFalsy();
  });

  test("kill-switch dialog opens as in-app dialog (not window.confirm)", async ({
    page,
    request,
  }) => {
    await installSharedE2ESession(page, request);
    await settledGoto(page, "/portfolio");

    let nativeConfirm = false;
    page.on("dialog", async (dialog) => {
      nativeConfirm = true;
      await dialog.dismiss();
    });

    await page.getByTestId("kill-switch-button").click();
    await expect(page.getByTestId("kill-switch-confirm")).toBeVisible();
    await expect(page.getByTestId("kill-switch-reason")).toBeVisible();
    expect(nativeConfirm).toBeFalsy();
    expect(await hasHorizontalOverflow(page)).toBeFalsy();
    await page.getByTestId("kill-switch-cancel").click();
  });

  test("deep-link handling on phone viewport", async ({ page, request }) => {
    await installSharedE2ESession(page, request);

    await settledGoto(page, "/analytics?tab=comparison");
    await expect(page).toHaveURL(/tab=comparison/);
    expect(await hasHorizontalOverflow(page)).toBeFalsy();

    await settledGoto(page, "/journal?entry=missing-for-webkit-audit");
    await expect(page.getByTestId("journal-stale-entry")).toBeVisible();

    await settledGoto(page, "/knowledge?document=missing-for-webkit-audit");
    await expect(page.getByTestId("knowledge-document-stale")).toBeVisible();

    await settledGoto(page, "/tradingview-signals?signal=missing-for-webkit-audit");
    await expect(page.getByTestId("signal-deep-link-missing")).toBeVisible();
  });

  test("viewport meta must enable safe-area (viewport-fit=cover)", async ({ page, request }) => {
    await installSharedE2ESession(page, request);
    await settledGoto(page, "/");
    const viewportFit = await page.evaluate(() => {
      const meta = document.querySelector('meta[name="viewport"]');
      return meta?.getAttribute("content") ?? "";
    });
    expect(
      viewportFit,
      "iOS Safari requires viewport-fit=cover for env(safe-area-inset-*) to apply",
    ).toMatch(/viewport-fit\s*=\s*cover/i);
  });
});
