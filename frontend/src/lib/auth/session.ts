/**
 * Session helpers for MVP auth.
 *
 * Bearer mode (default): access + refresh tokens in sessionStorage — fine for local dev.
 * Cookie mode: refresh token in httpOnly cookie; access token in sessionStorage only.
 *
 * A non-sensitive session marker cookie (no token material) is kept on the frontend
 * origin so the edge middleware can redirect unauthenticated visitors before serving
 * protected shell HTML (AT-017). See docs/security.md.
 */

import { sessionCleared } from "./session-events";
import { SESSION_MARKER_COOKIE, SESSION_MARKER_VALUE } from "@/lib/auth/boundary";

const ACCESS_KEY = "alphatrade_access_token";
const REFRESH_KEY = "alphatrade_refresh_token";
let locallyCleared = false;

export function usesCookieRefresh(): boolean {
  if (typeof window === "undefined") {
    return process.env.NEXT_PUBLIC_AUTH_COOKIE_MODE === "true";
  }
  return process.env.NEXT_PUBLIC_AUTH_COOKIE_MODE === "true";
}

function markerCookieSuffix(): string {
  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  return `; Path=/; SameSite=Lax${secure}`;
}

function setSessionMarker(): void {
  if (typeof document === "undefined") return;
  document.cookie = `${SESSION_MARKER_COOKIE}=${SESSION_MARKER_VALUE}${markerCookieSuffix()}`;
}

function clearSessionMarker(): void {
  if (typeof document === "undefined") return;
  document.cookie = `${SESSION_MARKER_COOKIE}=; Max-Age=0${markerCookieSuffix()}`;
}

export function getAccessToken(): string | null {
  if (locallyCleared || typeof window === "undefined") return null;
  try { return sessionStorage.getItem(ACCESS_KEY) || null; } catch { return null; }
}

export function getRefreshToken(): string | null {
  if (locallyCleared || typeof window === "undefined") return null;
  if (usesCookieRefresh()) return null;
  try { return sessionStorage.getItem(REFRESH_KEY) || null; } catch { return null; }
}

export function setTokens(accessToken: string, refreshToken?: string): void {
  sessionStorage.setItem(ACCESS_KEY, accessToken);
  if (!usesCookieRefresh() && refreshToken) {
    sessionStorage.setItem(REFRESH_KEY, refreshToken);
  }
  locallyCleared = false;
  setSessionMarker();
}

export function clearTokens(): void {
  locallyCleared = true;
  sessionCleared();
  for (const key of [ACCESS_KEY, REFRESH_KEY]) {
    try { sessionStorage.removeItem(key); }
    catch {
      // If removal alone is denied, erase the value without abandoning cleanup.
      try { sessionStorage.setItem(key, ""); } catch { /* Local identity is already fenced. */ }
    }
  }
  clearSessionMarker();
}

export function isAuthenticated(): boolean {
  return Boolean(getAccessToken());
}
