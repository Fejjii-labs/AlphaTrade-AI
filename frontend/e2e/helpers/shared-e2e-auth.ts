import { APIRequestContext, Page, expect } from "@playwright/test";

import { installSmokeSession } from "./staging-smoke-auth";
import { freshE2EPassword } from "./e2e-password";

const API_URL = process.env.PLAYWRIGHT_API_URL ?? "http://localhost:8000";

/**
 * One registration per Playwright worker.
 * Auth register is limited to 10/hour/IP; the suite exceeds that if every
 * spec registers independently.
 */
let cachedAccessToken: string | null = null;

export async function getSharedE2EAccessToken(request: APIRequestContext): Promise<string> {
  if (cachedAccessToken) {
    return cachedAccessToken;
  }

  const email = `e2e-shared-${Date.now()}@example.com`;
  const password = freshE2EPassword();
  const register = await request.post(`${API_URL}/auth/register`, {
    data: {
      email,
      password,
      organization_name: `E2E Shared Org ${Date.now()}`,
    },
  });
  expect(register.ok(), `shared e2e register failed: HTTP ${register.status()}`).toBeTruthy();
  const auth = (await register.json()) as { tokens: { access_token: string } };
  cachedAccessToken = auth.tokens.access_token;
  return cachedAccessToken;
}

export async function installSharedE2ESession(
  page: Page,
  request: APIRequestContext,
): Promise<string> {
  const token = await getSharedE2EAccessToken(request);
  await installSmokeSession(page, token);
  return token;
}

/** Prefer the accessible paper-mode label; avoid matching hidden duplicate chips. */
export function paperModeActive(page: Page) {
  return page
    .getByRole("status", { name: "Execution status" })
    .getByText("PAPER", { exact: true });
}
