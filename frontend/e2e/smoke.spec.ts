import { expect, test } from "@playwright/test";

const API_URL = process.env.PLAYWRIGHT_API_URL ?? "http://127.0.0.1:8000";

function uniqueEmail() {
  return `e2e-${Date.now()}@example.com`;
}

test.describe("AlphaTrade MVP API workflow", () => {
  test("full happy path via Playwright request", async ({ request }) => {
    const email = uniqueEmail();
    const password = "secure-password-1";
    const uniqueLesson = `E2E journal lesson ${Date.now()}`;

    const health = await request.get(`${API_URL}/health`);
    expect(health.ok()).toBeTruthy();
    const healthBody = await health.json();
    expect(healthBody.execution_mode).toBe("paper");
    expect(healthBody.real_trading_enabled).toBe(false);

    const providers = await request.get(`${API_URL}/providers/status`);
    expect(providers.ok()).toBeTruthy();
    const providerBody = await providers.json();
    expect(providerBody.providers?.length).toBeGreaterThan(0);

    const register = await request.post(`${API_URL}/auth/register`, {
      data: { email, password, organization_name: `E2E Org ${Date.now()}` },
    });
    expect(register.status()).toBe(201);
    const auth = await register.json();
    const headers = { Authorization: `Bearer ${auth.tokens.access_token}` };

    const me = await request.get(`${API_URL}/auth/me`, { headers });
    expect(me.ok()).toBeTruthy();
    const meBody = await me.json();

    const watchlist = await request.post(`${API_URL}/market/watchlist`, {
      headers,
      data: {
        organization_id: meBody.organization.id,
        user_id: meBody.user.id,
        symbol: "BTCUSDT",
        exchange: "mock",
        timeframes: ["1h"],
        strategy_ids: ["htf_trend_pullback"],
        enabled: true,
      },
    });
    expect(watchlist.ok()).toBeTruthy();

    const chat = await request.post(`${API_URL}/chat/message`, {
      headers,
      data: { message: "Analyze BTC pullback setup", symbol: "BTCUSDT", timeframe: "1h" },
    });
    expect(chat.ok()).toBeTruthy();
    const chatBody = await chat.json();
    expect(chatBody.conversation_id).toBeTruthy();

    const conversationMessages = await request.get(
      `${API_URL}/conversations/${chatBody.conversation_id}/messages`,
      { headers },
    );
    expect(conversationMessages.ok()).toBeTruthy();
    const conversationBody = await conversationMessages.json();
    expect(conversationBody.total).toBeGreaterThanOrEqual(2);

    const proposals = await request.get(`${API_URL}/proposals`, { headers });
    expect(proposals.ok()).toBeTruthy();

    if (chatBody.proposal_id) {
      const proposalDetail = await request.get(`${API_URL}/proposals/${chatBody.proposal_id}`, {
        headers,
      });
      expect(proposalDetail.ok()).toBeTruthy();
    }

    const approvals = await request.get(`${API_URL}/approvals`, { headers });
    expect(approvals.ok()).toBeTruthy();
    const approvalItems = (await approvals.json()).items ?? [];
    if (approvalItems.length > 0) {
      const approvalDetail = await request.get(`${API_URL}/approvals/${approvalItems[0].id}`, {
        headers,
      });
      expect(approvalDetail.ok()).toBeTruthy();
    }

    const journal = await request.post(`${API_URL}/journal/entries`, {
      headers,
      data: {
        symbol: "BTCUSDT",
        timeframe: "1h",
        direction: "long",
        entry_rationale: "E2E pullback test",
        lessons: uniqueLesson,
      },
    });
    expect(journal.ok()).toBeTruthy();
    const journalRecord = await journal.json();

    await expect.poll(async () => {
      const listed = await request.get(`${API_URL}/knowledge/documents`, {
        headers, params: { source_type: "trade_journal", limit: 100 },
      });
      const documents = (await listed.json()).items;
      return documents.some((document: { source_uri?: string; ingestion_metadata?: { indexing?: { vector_index_status?: string } } }) =>
        document.source_uri?.includes(journalRecord.id) && document.ingestion_metadata?.indexing?.vector_index_status === "ready");
    }, { timeout: 15_000, intervals: [100, 250, 500] }).toBe(true);
    const search = await request.post(`${API_URL}/knowledge/search`, {
      headers,
      data: { query: uniqueLesson, top_k: 5, source_types: ["trade_journal"] },
    });
    expect(search.ok()).toBeTruthy();
    const searchBody = await search.json();
    expect(searchBody.chunks.length).toBeGreaterThan(0);

    const usage = await request.get(`${API_URL}/usage/summary`, { headers });
    expect(usage.ok()).toBeTruthy();

    const audit = await request.get(`${API_URL}/audit/events`, { headers });
    expect(audit.ok()).toBeTruthy();

    const refreshToken = auth.tokens.refresh_token as string | undefined;
    const logout = await request.post(`${API_URL}/auth/logout`, {
      headers,
      data: refreshToken ? { refresh_token: refreshToken } : {},
    });
    expect(logout.ok()).toBeTruthy();

    const blocked = await request.get(`${API_URL}/proposals`);
    expect(blocked.status()).toBe(401);
  });
});

test.describe("AlphaTrade UI safety smoke", () => {
  test("public routes, safety badges, and auth redirect", async ({ page }) => {
    await page.goto("/register");
    await expect(page.getByRole("heading", { name: /create account/i })).toBeVisible();
    await expect(page.getByText(/real trading disabled/i)).toBeVisible();

    await page.goto("/login");
    await expect(page.getByRole("heading", { name: /sign in/i })).toBeVisible();

    await page.goto("/");
    await expect(page).toHaveURL(/login/);
  });

  test("no real trading CTA on login", async ({ page }) => {
    await page.goto("/login");
    await expect(page.getByRole("button", { name: /place real order/i })).toHaveCount(0);
    await expect(page.getByRole("button", { name: /execute live/i })).toHaveCount(0);
  });
});

test.describe("Browser happy path (local optional)", () => {
  test.skip(
    !process.env.PLAYWRIGHT_BROWSER_E2E,
    "Set PLAYWRIGHT_BROWSER_E2E=1 to run full browser tour locally",
  );

  test("register, dashboard, workspace, logout", async ({ page }) => {
    const email = uniqueEmail();
    const password = "secure-password-1";

    await page.goto("/register");
    await page.getByLabel(/email/i).fill(email);
    await page.locator("#password").fill(password);
    await page.getByLabel(/organization/i).fill("E2E Browser Org");
    await page.getByRole("button", { name: /create account/i }).click();
    await expect(page).toHaveURL("/");

    await expect(page.getByRole("status", { name: "Execution status" }).getByText("PAPER", { exact: true })).toBeVisible();
    await expect(page.getByText(/real off/i).first()).toBeVisible();

    await page.goto("/workspace");
    await expect(page.getByRole("heading", { name: /ai trading workspace/i })).toBeVisible();
    await expect(page.getByText(/real off/i).first()).toBeVisible();

    await page.getByTestId("header-status-menu").locator("summary").click();
    await page.getByRole("button", { name: /log out/i }).click();
    await expect(page).toHaveURL(/login/);
    await page.goto("/journal");
    await expect(page).toHaveURL(/login/);
  });
});
