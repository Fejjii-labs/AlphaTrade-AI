import { sessionGeneration } from "@/lib/auth/session-events";
import { appConfig } from "@/lib/config";
import { sanitizeNextPath } from "@/lib/auth/boundary";
import { clearTokens, getAccessToken, getRefreshToken, setTokens, usesCookieRefresh } from "@/lib/auth/session";

export class ApiError extends Error {
  status: number;
  body: unknown;

  constructor(message: string, status: number, body: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

function buildUrl(path: string, query?: Record<string, string | number | boolean | undefined | null>) {
  const url = new URL(path, appConfig.apiBaseUrl);
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null && value !== "") {
        url.searchParams.set(key, String(value));
      }
    }
  }
  return url.toString();
}

function fetchCredentials(): RequestCredentials {
  return usesCookieRefresh() ? "include" : "same-origin";
}

function parseErrorMessage(body: unknown, fallback: string): string {
  if (typeof body === "object" && body && "error" in body) {
    const err = (body as { error?: { message?: string } }).error;
    if (err?.message) return err.message;
  }
  if (typeof body === "object" && body && "detail" in body) {
    return String((body as { detail: unknown }).detail);
  }
  return fallback;
}

async function requestRefreshedTokens(): Promise<boolean> {
  const generation = sessionGeneration();
  const refreshToken = getRefreshToken();
  const body = refreshToken ? JSON.stringify({ refresh_token: refreshToken }) : JSON.stringify({});
  const response = await fetch(buildUrl("/auth/refresh"), {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body,
    credentials: fetchCredentials(),
    cache: "no-store",
  });
  if (generation !== sessionGeneration()) return false;
  if (!response.ok) return false;
  const payload = (await response.json()) as {
    access_token: string;
    refresh_token?: string;
    expires_in?: unknown;
  };
  if (generation !== sessionGeneration()) return false;
  setTokens(payload.access_token, payload.refresh_token);
  noteIssuedAccessToken(payload.access_token, payload.expires_in);
  return true;
}

// Single-flight guard: concurrent 401s share one refresh call so token rotation
// (which invalidates the old refresh token) cannot race against itself.
let refreshInFlight: { generation: number; promise: Promise<boolean> } | null = null;

async function refreshAccessToken(): Promise<boolean> {
  const generation = sessionGeneration();
  if (!refreshInFlight || refreshInFlight.generation !== generation) {
    const flight = { generation, promise: Promise.resolve(false) };
    flight.promise = requestRefreshedTokens().finally(() => {
      if (refreshInFlight === flight) refreshInFlight = null;
    });
    refreshInFlight = flight;
  }
  return refreshInFlight.promise;
}

// Access tokens are short-lived (15 min). Background pollers such as the
// kill-switch posture refresh used to send the expired token, take a 401 and
// only then refresh, so every expiry produced one 401 per open tab. Refresh
// proactively when the JWT `exp` is near. The 401 retry path stays as the
// fallback. The payload is decoded only to read `exp`; it is never trusted
// for authorization (the backend verifies the signature).
const ACCESS_TOKEN_REFRESH_SKEW_MS = 30_000;

export function accessTokenExpiresAtMs(token: string): number | null {
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  try {
    const b64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const padded = b64 + "=".repeat((4 - (b64.length % 4)) % 4);
    const payload = JSON.parse(atob(padded)) as { exp?: unknown };
    return typeof payload.exp === "number" && Number.isFinite(payload.exp) ? payload.exp * 1000 : null;
  } catch {
    return null;
  }
}

// Griller C1: comparing the server's JWT `exp` with the local clock storms
// /auth/refresh when the client clock runs ahead. For tokens we received from
// /auth/refresh, schedule from the server's `expires_in` against local receipt
// time instead (skew-free). A token issued less than MIN_TOKEN_AGE_MS ago is
// never proactively refreshed again, whatever its `exp` says.
const MIN_TOKEN_AGE_MS = 60_000;
let issuedToken: { token: string; receivedAtMs: number; deadlineMs: number | null } | null = null;

export function noteIssuedAccessToken(
  token: string,
  expiresIn: unknown,
  receivedAtMs = Date.now(),
): void {
  const seconds = typeof expiresIn === "number" && Number.isFinite(expiresIn) && expiresIn > 0 ? expiresIn : null;
  issuedToken = { token, receivedAtMs, deadlineMs: seconds === null ? null : receivedAtMs + seconds * 1000 };
}

export function accessTokenNeedsRefresh(token: string | null, nowMs = Date.now()): boolean {
  if (!token) return false;
  if (issuedToken && issuedToken.token === token) {
    if (nowMs - issuedToken.receivedAtMs < MIN_TOKEN_AGE_MS) return false;
    if (issuedToken.deadlineMs !== null) {
      return issuedToken.deadlineMs - nowMs <= ACCESS_TOKEN_REFRESH_SKEW_MS;
    }
  }
  const expiresAt = accessTokenExpiresAtMs(token);
  return expiresAt !== null && expiresAt - nowMs <= ACCESS_TOKEN_REFRESH_SKEW_MS;
}

function sessionExpiredLoginPath(): string {
  const { pathname, search } = window.location;
  if (pathname.startsWith("/login")) return "/login";
  const next = sanitizeNextPath(`${pathname}${search}`);
  return next !== "/" ? `/login?next=${encodeURIComponent(next)}` : "/login";
}

export async function apiFetch<T>(
  path: string,
  options: RequestInit & {
    query?: Record<string, string | number | boolean | undefined | null>;
    auth?: boolean;
    retryOnUnauthorized?: boolean;
  } = {},
): Promise<T> {
  const {
    query,
    headers,
    auth = !path.startsWith("/auth/"),
    retryOnUnauthorized = true,
    ...rest
  } = options;

  const generation = sessionGeneration();
  const checkContext = () => {
    rest.signal?.throwIfAborted();
    if (auth && generation !== sessionGeneration()) {
      throw new DOMException("Session changed", "AbortError");
    }
  };
  checkContext();
  const requestHeaders: Record<string, string> = {
    Accept: "application/json",
    ...(rest.body && !(typeof FormData !== "undefined" && rest.body instanceof FormData)
      ? { "Content-Type": "application/json" }
      : {}),
    ...(headers as Record<string, string> | undefined),
  };

  if (auth && retryOnUnauthorized && accessTokenNeedsRefresh(getAccessToken())) {
    // Best effort: on failure fall through and let the 401 path decide.
    await refreshAccessToken();
    checkContext();
  }

  if (auth) {
    const token = getAccessToken();
    if (token) requestHeaders.Authorization = `Bearer ${token}`;
  }

  const response = await fetch(buildUrl(path, query), {
    ...rest,
    headers: requestHeaders,
    credentials: fetchCredentials(),
    cache: "no-store",
  });

  checkContext();
  if (response.status === 401 && auth && retryOnUnauthorized) {
    const refreshed = await refreshAccessToken();
    checkContext();
    if (refreshed) {
      return apiFetch<T>(path, { ...options, retryOnUnauthorized: false });
    }
    if (typeof window !== "undefined") {
      clearTokens();
      window.location.assign(sessionExpiredLoginPath());
    }
    throw new ApiError("Session expired", 401, null);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const text = await response.text();
  checkContext();
  let body: unknown;
  try { body = text ? JSON.parse(text) : null; }
  catch { throw new ApiError("API returned an invalid response.", response.status, null); }

  if (!response.ok) {
    throw new ApiError(parseErrorMessage(body, response.statusText), response.status, body);
  }

  return body as T;
}
