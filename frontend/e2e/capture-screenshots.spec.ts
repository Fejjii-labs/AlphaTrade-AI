/**
 * Capture portfolio screenshots for docs/screenshots/.
 * Run: npm run capture:screenshots
 */
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import path from "node:path";
import { freshE2EPassword } from "./helpers/e2e-password";

const API_URL = process.env.PLAYWRIGHT_API_URL ?? "http://localhost:8000";
const SCREENSHOT_DIR = path.resolve(__dirname, "../../docs/screenshots");
const VIEWPORT = { width: 1280, height: 900 };

async function shot(page: Page, filename: string) {
  await page.setViewportSize(VIEWPORT);
  await page.waitForTimeout(500);
  await page.screenshot({
    path: path.join(SCREENSHOT_DIR, filename),
    fullPage: true,
    animations: "disabled",
  });
}

async function registerAndOpenDashboard(page: Page, email: string, password: string) {
  await page.goto("/register");
  await page.getByLabel(/organization/i).fill("AlphaTrade Demo");
  await page.getByLabel(/email/i).fill(email);
  await page.locator("#password").fill(password);
  await page.getByRole("button", { name: /create account/i }).click();
  await expect(page).toHaveURL("/", { timeout: 30_000 });
  await expect(page.getByRole("heading", { name: /^dashboard$/i })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("status", { name: "Execution status" }).getByText("PAPER", { exact: true })).toBeVisible();
}

async function bearerHeaders(page: Page) {
  const token = await page.evaluate(() => sessionStorage.getItem("alphatrade_access_token"));
  expect(token).toBeTruthy();
  return { Authorization: `Bearer ${token}` };
}

async function seedDemoData(
  request: APIRequestContext,
  headers: Record<string, string>,
  uniqueLesson: string,
) {
  const chat = await request.post(`${API_URL}/chat/message`, {
    headers,
    data: {
      message: "Plan trade BTC pullback [test_low_confidence]",
      symbol: "BTCUSDT",
      timeframe: "1h",
    },
  });
  expect(chat.ok()).toBeTruthy();
  const chatBody = await chat.json();

  const proposalsRes = await request.get(`${API_URL}/proposals`, { headers });
  expect(proposalsRes.ok()).toBeTruthy();
  const proposalItems = (await proposalsRes.json()).items ?? [];
  expect(proposalItems.length).toBeGreaterThan(0);
  const proposalId = (chatBody.proposal_id ?? proposalItems[0].id) as string;

  const approvalsRes = await request.get(`${API_URL}/approvals`, { headers });
  expect(approvalsRes.ok()).toBeTruthy();
  const approvalItems = (await approvalsRes.json()).items ?? [];

  let approvalId: string;
  if (approvalItems.length > 0) {
    approvalId = approvalItems[0].id as string;
  } else {
    const workflow = await request.get(`${API_URL}/proposals/${proposalId}/workflow`, { headers });
    expect(workflow.ok()).toBeTruthy();
    const workflowBody = await workflow.json();
    expect(workflowBody.approval?.id).toBeTruthy();
    approvalId = workflowBody.approval.id as string;
  }

  const journalRes = await request.post(`${API_URL}/journal/entries`, {
    headers,
    data: {
      symbol: "BTCUSDT",
      timeframe: "1h",
      direction: "long",
      entry_rationale: "Demo pullback review",
      lessons: uniqueLesson,
      emotions: ["focused"],
      mistakes: ["none"],
    },
  });
  expect(journalRes.ok()).toBeTruthy();

  return { proposalId, approvalId, uniqueLesson };
}

test.describe("Portfolio screenshot capture", () => {
  test("capture demo screenshots", async ({ page, request }) => {
    test.setTimeout(180_000);

    const password = freshE2EPassword();
    const email = `demo-screenshots-${Date.now()}@example.com`;
    const uniqueLesson = `Pullback discipline lesson ${Date.now()}`;

    const health = await request.get(`${API_URL}/health`);
    expect(health.ok()).toBeTruthy();
    const healthBody = await health.json();
    expect(healthBody.execution_mode).toBe("paper");
    expect(healthBody.real_trading_enabled).toBe(false);

    await registerAndOpenDashboard(page, email, password);
    const headers = await bearerHeaders(page);
    const { proposalId, approvalId, uniqueLesson: seededLesson } = await seedDemoData(
      request,
      headers,
      uniqueLesson,
    );

    await page.reload();
    await expect(page.getByRole("heading", { name: /^dashboard$/i })).toBeVisible();
    await shot(page, "dashboard.png");

    await page.getByRole("heading", { name: /provider status/i }).scrollIntoViewIfNeeded();
    await page.locator("section").filter({ hasText: "Provider status" }).screenshot({
      path: path.join(SCREENSHOT_DIR, "provider_status.png"),
      animations: "disabled",
    });

    await page.goto("/market");
    await expect(page.getByRole("heading", { name: /market monitor/i })).toBeVisible();
    await expect(page.getByText(/last price/i).first()).toBeVisible({ timeout: 15_000 });
    await page.getByRole("button", { name: /^analyze$/i }).click();
    await expect(page.getByText(/strategy signals/i)).toBeVisible({ timeout: 15_000 });
    await shot(page, "market_monitor.png");

    await page.goto("/workspace");
    await expect(page.getByRole("heading", { name: /ai trading workspace/i })).toBeVisible();
    await page.getByLabel(/message/i).fill("Analyze BTC pullback on 1h with risk context");
    await page.getByRole("button", { name: /send message/i }).click();
    await expect(page.getByText(/deterministic analysis|summary/i).first()).toBeVisible({
      timeout: 30_000,
    });
    await shot(page, "ai_workspace.png");

    await page.goto(`/proposals?id=${proposalId}`);
    await expect(page.getByRole("heading", { name: /trade proposals/i })).toBeVisible();
    await expect(page.getByText(/entry|stop|risk/i).first()).toBeVisible({ timeout: 15_000 });
    await shot(page, "proposal_detail.png");

    await page.goto(`/approvals?id=${approvalId}`);
    await expect(page.getByRole("heading", { name: /^approvals$/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /approve for paper review/i }).first()).toBeVisible({
      timeout: 15_000,
    });
    await shot(page, "approval_detail.png");

    const approve = await request.post(`${API_URL}/approvals/${approvalId}/approve`, {
      headers,
      data: { reason: "Demo portfolio approval" },
    });
    expect(approve.ok()).toBeTruthy();

    const proposalRes = await request.get(`${API_URL}/proposals/${proposalId}`, { headers });
    expect(proposalRes.ok()).toBeTruthy();
    const proposal = await proposalRes.json();

    const paperOrder = await request.post(`${API_URL}/execution/paper`, {
      headers,
      data: {
        proposal_id: proposalId,
        approval_id: approvalId,
        symbol: proposal.symbol,
        side: proposal.direction === "short" ? "sell" : "buy",
        type: "market",
        size: proposal.position_size,
        idempotency_key: `screenshot-demo-${Date.now()}`,
      },
    });
    expect(paperOrder.ok()).toBeTruthy();

    const positionsRes = await request.get(`${API_URL}/positions`, { headers });
    expect(positionsRes.ok()).toBeTruthy();
    const positionsBody = await positionsRes.json();
    expect(positionsBody.items?.length).toBeGreaterThan(0);

    await page.goto("/positions");
    await expect(page.getByRole("heading", { name: "Positions", exact: true })).toBeVisible();
    await expect(page.getByText(/BTCUSDT|paper|simulated/i).first()).toBeVisible({ timeout: 15_000 });
    await shot(page, "paper_position.png");

    await page.goto("/journal");
    await expect(page.getByRole("heading", { name: "Journal", exact: true })).toBeVisible();
    const journalRes = await request.get(`${API_URL}/journal/entries`, { headers });
    expect(journalRes.ok()).toBeTruthy();
    expect((await journalRes.json()).items?.length).toBeGreaterThan(0);
    await expect(page.getByText(seededLesson).or(page.getByText(/demo pullback/i)).first()).toBeVisible({
      timeout: 15_000,
    });
    await shot(page, "journal.png");

    await page.goto("/knowledge");
    await expect(page.getByRole("heading", { name: "Knowledge Base", exact: true })).toBeVisible();
    const searchQuery = seededLesson.slice(0, 24);
    await page.getByPlaceholder("Search query").fill(searchQuery);
    await page.getByRole("button", { name: /^search$/i }).click();
    await expect(page.getByText(/results for/i)).toBeVisible({ timeout: 20_000 });
    await expect(
      page.getByText(/trade_journal|journal|lesson|pullback|playbook/i).first(),
    ).toBeVisible({ timeout: 20_000 });
    await shot(page, "knowledge_search.png");

    await page.goto("/usage");
    await expect(page.getByRole("heading", { name: "Usage", exact: true })).toBeVisible();
    await expect(page.getByText(/quota|events|usage/i).first()).toBeVisible({ timeout: 15_000 });
    await shot(page, "usage_dashboard.png");

    await page.goto("/audit");
    await expect(page.getByRole("heading", { name: "Audit", exact: true })).toBeVisible();
    await expect(page.getByText(/paper|proposal|approval/i).first()).toBeVisible({ timeout: 15_000 });
    await shot(page, "audit_events.png");

    await page.goto("/settings");
    await expect(page.getByRole("heading", { name: "Settings", exact: true })).toBeVisible();
    await shot(page, "settings.png");
  });
});
