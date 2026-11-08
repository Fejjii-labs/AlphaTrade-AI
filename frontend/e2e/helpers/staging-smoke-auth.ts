import { APIRequestContext, APIResponse, Page, expect } from "@playwright/test";

import {
  SESSION_MARKER_COOKIE,
  SESSION_MARKER_VALUE,
} from "../../src/lib/auth/boundary";

const PLACEHOLDER_PASSWORD_MARKERS = [
  "paste_password_here",
  "your_real_password",
  "replace_with",
  "your-chosen-demo-password",
  "your_chosen",
  "changeme",
  "change-me",
  "password_here",
  "insert_password",
  "xxx",
] as const;

/**
 * Install an authenticated browser session for smoke specs: the sessionStorage
 * access token plus the frontend-origin session marker cookie required by the
 * AT-017 edge middleware. Call before the first page.goto().
 */
export async function installSmokeSession(page: Page, accessToken: string): Promise<void> {
  const base = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3000";
  await page.context().addCookies([
    { name: SESSION_MARKER_COOKIE, value: SESSION_MARKER_VALUE, url: base },
  ]);
  await page.addInitScript((token: string) => {
    sessionStorage.setItem("alphatrade_access_token", token);
  }, accessToken);
}

export function resolveSmokeEmail(): string {
  return process.env.SMOKE_EMAIL ?? `portfolio-smoke-${Date.now()}@example.com`;
}

export function resolveSmokePassword(): string {
  const fromSmoke = process.env.SMOKE_PASSWORD?.trim();
  if (fromSmoke) {
    return fromSmoke;
  }
  const legacy = process.env.STAGING_BOOTSTRAP_PASSWORD?.trim();
  if (legacy) {
    return legacy;
  }
  // No committed default (Fable audit M20): staging smoke needs the env var.
  throw new Error("Set SMOKE_PASSWORD (or STAGING_BOOTSTRAP_PASSWORD) for the staging smoke account.");
}

export function resolveSmokeOrgName(): string {
  return process.env.SMOKE_ORG ?? `Portfolio Smoke ${Date.now()}`;
}

export function assertSmokePasswordNotPlaceholder(password: string): void {
  const lowered = password.trim().toLowerCase();
  expect(
    lowered.length,
    "SMOKE_PASSWORD must not be empty",
  ).toBeGreaterThan(0);
  for (const marker of PLACEHOLDER_PASSWORD_MARKERS) {
    expect(
      lowered.includes(marker),
      `SMOKE_PASSWORD looks like a placeholder (${marker}); set a real value`,
    ).toBeFalsy();
  }
}

export async function formatSafeAuthFailure(response: APIResponse): Promise<string> {
  const status = response.status();
  let detail = "unknown";

  try {
    const body = (await response.json()) as {
      error?: { code?: string; message?: string };
      detail?: string;
    };
    detail =
      body.error?.code ??
      body.error?.message?.slice(0, 120) ??
      body.detail?.slice(0, 120) ??
      "unknown";
  } catch {
    detail = "non_json_body";
  }

  detail = detail.replace(/Bearer\s+\S+/gi, "Bearer <redacted>");
  detail = detail.replace(/token[s]?\s*[:=]\s*\S+/gi, "token=<redacted>");
  return `HTTP ${status}: ${detail}`;
}

/** Register a fresh staging smoke account (matches scripts/portfolio-smoke.sh). */
export async function registerSmokeAccount(
  request: APIRequestContext,
  apiUrl: string,
  email: string,
  password: string,
  organizationName: string,
): Promise<string> {
  const register = await request.post(`${apiUrl}/auth/register`, {
    data: { email, password, organization_name: organizationName },
  });

  if (!register.ok()) {
    const detail = await formatSafeAuthFailure(register);
    throw new Error(`POST /auth/register failed — ${detail}`);
  }

  const auth = (await register.json()) as { tokens: { access_token: string } };
  return auth.tokens.access_token;
}

/**
 * Obtain an access token using the portfolio-smoke flow: register, then login.
 * Falls back to the register token when login is blocked for unverified email.
 */
export async function obtainPortfolioSmokeAccessToken(
  request: APIRequestContext,
  apiUrl: string,
): Promise<{ accessToken: string; loginStatus: number | null }> {
  const email = resolveSmokeEmail();
  const password = resolveSmokePassword();
  const organizationName = resolveSmokeOrgName();
  assertSmokePasswordNotPlaceholder(password);

  const registerToken = await registerSmokeAccount(
    request,
    apiUrl,
    email,
    password,
    organizationName,
  );

  const login = await request.post(`${apiUrl}/auth/login`, {
    data: { email, password },
  });

  if (login.ok()) {
    const auth = (await login.json()) as { tokens: { access_token: string } };
    return { accessToken: auth.tokens.access_token, loginStatus: login.status() };
  }

  const loginDetail = await formatSafeAuthFailure(login);
  console.log(`POST /auth/login — ${loginDetail}`);

  if (login.status() === 401 && loginDetail.toLowerCase().includes("not verified")) {
    console.log("Using register session (login blocked for unverified email).");
    return { accessToken: registerToken, loginStatus: login.status() };
  }

  throw new Error(`POST /auth/login failed — ${loginDetail}`);
}
