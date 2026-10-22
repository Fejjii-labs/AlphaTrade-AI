import { expect, test } from "@playwright/test";

const API_URL = process.env.PLAYWRIGHT_API_URL ?? "http://localhost:8000";

function uniqueEmail(): string {
  return `at017-${Date.now()}-${Math.floor(Math.random() * 10_000)}@example.com`;
}

test.describe("AT-017 edge auth boundary", () => {
  test("unauthenticated navigation to protected routes redirects at the edge", async ({
    page,
  }) => {
    await page.goto("/portfolio");
    await expect(page).toHaveURL(/\/login\?next=%2Fportfolio/);
    await expect(page.getByRole("heading", { name: /sign in/i })).toBeVisible();

    await page.goto("/proposals?id=abc");
    await expect(page).toHaveURL(/\/login\?next=%2Fproposals%3Fid%3Dabc/);

    await page.goto("/settings#notifications");
    await expect(page).toHaveURL(/\/login\?next=%2Fsettings/);
    await expect(page.getByTestId("telegram-policy-v2")).toHaveCount(0);

    await page.goto("/");
    await expect(page).toHaveURL(/\/login$/);
  });

  test("public routes stay reachable without a session", async ({ page }) => {
    for (const path of ["/login", "/register", "/forgot-password"]) {
      await page.goto(path);
      await expect(page).toHaveURL(new RegExp(`${path.replace("/", "\\/")}$`));
    }
  });

  test("security headers are served", async ({ page }) => {
    const response = await page.goto("/login");
    expect(response).not.toBeNull();
    const headers = response!.headers();
    expect(headers["content-security-policy"]).toContain("default-src 'self'");
    expect(headers["content-security-policy"]).toContain("frame-ancestors 'none'");
    expect(headers["x-content-type-options"]).toBe("nosniff");
    expect(headers["x-frame-options"]).toBe("DENY");
    expect(headers["referrer-policy"]).toBe("strict-origin-when-cross-origin");
    expect(headers["permissions-policy"]).toContain("camera=()");
  });

  test("login retains the intended destination", async ({ page, request }) => {
    const email = uniqueEmail();
    const password = "secure-password-1";
    const register = await request.post(`${API_URL}/auth/register`, {
      data: { email, password, organization_name: `AT017 Boundary ${Date.now()}` },
    });
    expect(register.ok()).toBeTruthy();

    await page.goto("/positions");
    await expect(page).toHaveURL(/\/login\?next=%2Fpositions/);

    await page.getByLabel(/email/i).fill(email);
    await page.locator("#password").fill(password);
    await page.getByRole("button", { name: /sign in/i }).click();
    await expect(page).toHaveURL(/\/positions/);
  });

  test("logout clears the session and back navigation stays blocked", async ({
    page,
    request,
  }) => {
    const email = uniqueEmail();
    const password = "secure-password-1";
    const register = await request.post(`${API_URL}/auth/register`, {
      data: { email, password, organization_name: `AT017 Logout ${Date.now()}` },
    });
    expect(register.ok()).toBeTruthy();

    await page.goto("/login");
    await page.getByLabel(/email/i).fill(email);
    await page.locator("#password").fill(password);
    await page.getByRole("button", { name: /sign in/i }).click();
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByRole("status", { name: "Execution status" }).getByText("PAPER", { exact: true })).toBeVisible();

    // Create a real history entry on a protected route before logging out.
    await page.goto("/portfolio");
    await expect(page).toHaveURL(/\/portfolio/);

    await page.getByRole("button", { name: "Status", exact: true }).click();
    await page.getByRole("button", { name: /log out/i }).click();
    await expect(page).toHaveURL(/\/login/);

    // Back navigation and direct URLs must not restore protected content.
    await page.goBack();
    await expect(page).toHaveURL(/\/login/);
    await expect(page.getByRole("status", { name: "Execution status" }).getByText("PAPER", { exact: true })).toHaveCount(0);
    await page.goto("/portfolio");
    await expect(page).toHaveURL(/\/login\?next=%2Fportfolio/);
  });

  test("stale marker cookie without tokens fails closed to login", async ({ page }) => {
    const base = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3000";
    await page.context().addCookies([{ name: "alphatrade_session", value: "1", url: base }]);
    await page.goto("/portfolio");
    // Middleware lets the marker through; the client guard must still redirect
    // because no access token exists, without rendering protected content.
    await expect(page).toHaveURL(/\/login/);
  });
});
