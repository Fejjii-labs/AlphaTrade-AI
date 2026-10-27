import { expect, test, type Page } from "@playwright/test";
import { installSmokeSession } from "./helpers/staging-smoke-auth";
import type { ManualDemoAttempt } from "../src/lib/api/manual-demo";

// Fixture acceptance only. Every API request is intercepted; no exchange IO.
const command = "00000000-0000-4000-8000-000000000001";
const blockedCommand = "00000000-0000-4000-8000-000000000002";
const original: ManualDemoAttempt = {
  command_id: command,
  account_id: "demo-account",
  account_name: "Selected demo account",
  venue: "BLOFIN_DEMO",
  origin: "manual_demo_test",
  attempted_at: "2026-10-08T12:56:20Z",
  submitted_at: "2026-10-08T12:56:28Z",
  symbol: "BTC-USDT",
  side: "BUY",
  requested_contracts: "0.1",
  base_quantity: "0.0001",
  stop: "82000",
  target: "83000",
  content_hash: "a".repeat(64),
  submission_outcome: "ALLOW",
  blocked_reason: null,
  detail_url: `/execution/manual-demo/${command}`,
  evidence: {
    origin: "manual demo test",
    revision_id: "immutable-plan",
    command_id: command,
    client_order_id: "exact-client",
    venue_order_id: "recorded-native-order",
    status: "reconciliation_unavailable_operator_hold",
    filled_quantity: "0.1",
    remaining_quantity: "0",
    average_fill_price: "82894",
    fees: "-0.0049",
    protection: "unverified",
    journal_trade_id: "exact-journal",
    execution_status: "filled",
    position_status: "unknown",
    account_status: "unknown",
    missing_evidence: [
      "Current account and protection evidence remains unknown; do not resubmit.",
    ],
    reconciliation_freshness: "latest_read_failed",
    recovery_status: "unresolved",
    recovery_reason: "Refresh this exact command.",
    can_reconcile: true,
    can_cancel: false,
    can_resolve: false,
  },
};
const blocked: ManualDemoAttempt = {
  ...original,
  command_id: blockedCommand,
  requested_contracts: "1",
  base_quantity: "0.001",
  submitted_at: null,
  submission_outcome: "BLOCKED",
  blocked_reason: "demo_account_already_claimed",
  detail_url: `/execution/manual-demo/${blockedCommand}`,
  evidence: {
    ...original.evidence,
    command_id: blockedCommand,
    execution_status: "blocked_before_submission",
    venue_order_id: null,
    filled_quantity: "0",
    journal_trade_id: null,
    fees: null,
    average_fill_price: null,
    remaining_quantity: "1",
  },
};

async function fixture(page: Page) {
  await installSmokeSession(page, "manual-demo-fixture-session");
  const reads: string[] = [];
  const writes: string[] = [];
  const apiOrigin = new URL(
    process.env.PLAYWRIGHT_API_URL ?? "http://localhost:8000",
  ).origin;
  await page.route(
    (url) => url.origin === apiOrigin,
    async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (route.request().method() !== "GET") writes.push(path);
      else reads.push(path);
      if (path === "/auth/me")
        return route.fulfill({
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
      if (path === "/execution/manual-demo/commands")
        return route.fulfill({
          json: { items: [blocked, original], total: 2, limit: 5, offset: 0 },
        });
      if (path === `/execution/manual-demo/commands/${command}`)
        return route.fulfill({ json: original });
      if (path === `/execution/manual-demo/commands/${blockedCommand}`)
        return route.fulfill({ json: blocked });
      if (path === `/execution/manual-demo/${command}/reconcile`)
        return route.fulfill({ json: original.evidence });
      return route.fulfill({
        status: 503,
        json: {
          error: {
            code: "fixture_unavailable",
            message: "Unrelated fixture read unavailable",
          },
        },
      });
    },
  );
  return { reads, writes };
}

test("history recovers original after reload and a separate attempt; refresh pins identities", async ({
  page,
}) => {
  const io = await fixture(page);
  await page.goto("/journal");
  await page.getByRole("button", { name: "New demo order" }).click();
  const history = page.getByRole("region", {
    name: "Recent manual demo activity",
  });
  const originalLink = history.getByRole("link", {
    name: /0.1 contracts \(0.0001 BTC\)/,
  });
  await expect(originalLink).toBeVisible();
  await expect(
    history.getByText("Blocked: demo account already claimed"),
  ).toBeVisible();
  await originalLink.click();
  await expect(page).toHaveURL(original.detail_url);
  await expect(
    page.getByText("0.1 requested contracts = 0.0001 BTC"),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByText("0.1 requested contracts = 0.0001 BTC"),
  ).toBeVisible();
  await page.getByText("Stored identities and history").click();
  await expect(
    page.getByText("Native order: recorded-native-order"),
  ).toBeVisible();
  await expect(page.getByText("Journal: exact-journal")).toBeVisible();
  for (let i = 0; i < 2; i++) {
    await page
      .getByRole("button", { name: "Refresh native evidence for this attempt" })
      .click();
    await expect(
      page.getByRole("button", {
        name: "Refresh native evidence for this attempt",
      }),
    ).toBeEnabled();
    await expect(
      page.getByText("Native order: recorded-native-order"),
    ).toBeVisible();
    await expect(page.getByText("Journal: exact-journal")).toBeVisible();
  }
  await page.goto(blocked.detail_url);
  await expect(
    page.getByText("Blocked before submission: demo account already claimed"),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: /Cancel|Resolve verified lifecycle/ }),
  ).toHaveCount(0);
  await page.goto(original.detail_url);
  await expect(
    page.getByText("0.1 requested contracts = 0.0001 BTC"),
  ).toBeVisible();
  expect(io.writes).toEqual([
    `/execution/manual-demo/${command}/reconcile`,
    `/execution/manual-demo/${command}/reconcile`,
  ]);
});

test("saved detail survives logout redirect and a new authenticated browser session", async ({
  browser,
}) => {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto(original.detail_url);
  await expect(page).toHaveURL(/\/login\?next=/);
  const retainedPath = new URL(page.url()).searchParams.get("next");
  expect(retainedPath).toBe(original.detail_url);
  const io = await fixture(page);
  await page.goto(retainedPath!);
  await expect(
    page.getByText("0.1 requested contracts = 0.0001 BTC"),
  ).toBeVisible();
  await context.close();
  const fresh = await browser.newContext();
  const newPage = await fresh.newPage();
  const newIo = await fixture(newPage);
  await newPage.goto(original.detail_url);
  await expect(
    newPage.getByText("0.1 requested contracts = 0.0001 BTC"),
  ).toBeVisible();
  expect(io.writes).toEqual([]);
  expect(newIo.writes).toEqual([]);
  await fresh.close();
});
