import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { clearTokens, getAccessToken } from "@/lib/auth/session";
import { onSessionCleared } from "@/lib/auth/session-events";
import { recoverySessionId } from "@/lib/auth/recovery-session";
import { AuthProvider, useAuth } from "./AuthContext";
import type { AuthResponse } from "@/lib/api/types";

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));
vi.mock("./AppContext", () => ({ useAppContext: () => ({ health: { must_verify_email: false } }) }));
vi.mock("@/lib/api", () => ({
  api: { auth: { login: vi.fn(), register: vi.fn(), logout: vi.fn(), me: vi.fn() } },
  ApiError: class extends Error {},
}));
const response: AuthResponse = {
  user: { id: "user", email: "synthetic@example.com", role: "owner", risk_profile: "conservative", timezone: "UTC", is_active: true, email_verified: true, created_at: "2026-10-10T12:00:00Z" },
  organization: { id: "org", name: "Synthetic", created_at: "2026-10-10T12:00:00Z" },
  tokens: { access_token: "local-access", refresh_token: "local-refresh", token_type: "bearer", expires_in: 900 },
};
const keys = ["alphatrade:experiment-draft:old", "alphatrade:screening:old"];
beforeEach(() => {
  clearTokens(); sessionStorage.clear(); vi.resetAllMocks();
  vi.mocked(api.auth.login).mockResolvedValue(response);
  vi.mocked(api.auth.logout).mockResolvedValue({ message: "Logged out" });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); clearTokens(); });
async function signedIn() {
  const hook = renderHook(useAuth, { wrapper: AuthProvider });
  await waitFor(() => expect(hook.result.current.loading).toBe(false));
  await act(async () => { await hook.result.current.login(response.user.email, "local-password"); });
  return hook;
}

it("the real authentication lifecycle invalidates off-panel requests on logout and same-account login", async () => {
  const { result } = await signedIn();
  const original = recoverySessionId("org", "user"); expect(original).toBeTruthy();
  for (const key of keys) sessionStorage.setItem(key, "pending");
  await act(async () => { await result.current.logout(); });
  expect(result.current.user).toBeNull(); expect(getAccessToken()).toBeNull();
  for (const key of keys) expect(sessionStorage.getItem(key)).toBeNull();
  await act(async () => { await result.current.login(response.user.email, "local-password"); });
  expect(recoverySessionId("org", "user")).not.toBe(original);
  expect(result.current.isAuthenticated).toBe(true);
});

it("a confirmed profile account change rotates recovery while a same-account profile read preserves it", async () => {
  const { result } = await signedIn();
  const original = recoverySessionId("org", "user");
  vi.mocked(api.auth.me).mockResolvedValue({ user: response.user, organization: response.organization });
  await act(async () => { await result.current.refreshProfile(); });
  expect(recoverySessionId("org", "user")).toBe(original);
  for (const key of keys) sessionStorage.setItem(key, "pending");
  vi.mocked(api.auth.me).mockResolvedValue({ user: { ...response.user, id: "other-user" }, organization: { ...response.organization, id: "other-org" } });
  await act(async () => { await result.current.refreshProfile(); });
  expect(result.current.organization?.id).toBe("other-org");
  expect(recoverySessionId("org", "user")).toBeNull(); expect(recoverySessionId("other-org", "other-user")).toBeTruthy();
  for (const key of keys) expect(sessionStorage.getItem(key)).toBeNull();
});

it("logout clears provider identity and navigation despite denied storage and a failing cleanup listener", async () => {
  const { result } = await signedIn();
  for (const key of keys) sessionStorage.setItem(key, "pending");
  const next = vi.fn(); const stop1 = onSessionCleared(() => { throw new Error("cleanup denied"); }); const stop2 = onSessionCleared(next);
  vi.spyOn(window, "sessionStorage", "get").mockImplementation(() => { throw new DOMException("Denied", "SecurityError"); });
  try {
    await act(async () => { await result.current.logout(); });
    expect(result.current.user).toBeNull(); expect(result.current.organization).toBeNull(); expect(result.current.isAuthenticated).toBe(false);
    expect(getAccessToken()).toBeNull(); expect(recoverySessionId("org", "user")).toBeNull();
    expect(next).toHaveBeenCalledOnce(); expect(replace).toHaveBeenLastCalledWith("/login");
    expect(document.cookie).not.toContain("alphatrade_session=1");
  } finally { stop1(); stop2(); }
});
