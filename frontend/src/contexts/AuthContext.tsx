"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { useRouter } from "next/navigation";

import { PrivateQueryProvider } from "@/components/query/PrivateQueryProvider";
import { sessionCleared, sessionGeneration } from "@/lib/auth/session-events";
import { bindRecoverySession, recoverySession } from "@/lib/auth/recovery-session";
import { api, ApiError } from "@/lib/api";
import type { AuthResponse, MembershipRole, MeResponse, TwoFactorChallengeResponse } from "@/lib/api/types";
import { forgetRememberedDevice, rememberDevice, rememberedDeviceToken } from "@/lib/auth/remembered-device";
import { sanitizeNextPath } from "@/lib/auth/boundary";
import { clearTokens, getRefreshToken, isAuthenticated, setTokens } from "@/lib/auth/session";
import { useAppContext } from "@/contexts/AppContext";

/** Result of the password step: signed in, or a second factor is needed. */
export type LoginOutcome =
  | { status: "signed_in" }
  | { status: "two_factor_required"; challenge: TwoFactorChallengeResponse };

export type SecondFactor = { code: string } | { recoveryCode: string };

interface AuthContextValue {
  user: MeResponse["user"] | null;
  organization: MeResponse["organization"] | null;
  /** Role in the current organization (owner/trader/viewer); null when unknown. */
  membershipRole: MembershipRole | null;
  loading: boolean;
  isAuthenticated: boolean;
  login: (email: string, password: string, nextPath?: string) => Promise<LoginOutcome>;
  completeTwoFactorLogin: (
    email: string,
    pendingToken: string,
    factor: SecondFactor,
    rememberThisDevice: boolean,
    nextPath?: string,
  ) => Promise<void>;
  register: (email: string, password: string, organizationName: string) => Promise<void>;
  logout: () => Promise<void>;
  refreshProfile: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  // Single shared /health source (FP2-105): read the verification policy from
  // AppContext instead of issuing a duplicate /health request. Conservative
  // default (verify) until posture is verified.
  const { health } = useAppContext();
  const mustVerifyEmail = health?.must_verify_email ?? true;
  const [user, setUser] = useState<MeResponse["user"] | null>(null);
  const [organization, setOrganization] = useState<MeResponse["organization"] | null>(null);
  const [membershipRole, setMembershipRole] = useState<MembershipRole | null>(null);
  const [loading, setLoading] = useState(true);

  const refreshProfile = useCallback(async () => {
    if (!isAuthenticated()) {
      setUser(null);
      setOrganization(null);
      setMembershipRole(null);
      return;
    }
    const generation = sessionGeneration();
    const me = await api.auth.me();
    if (generation !== sessionGeneration()) return;
    const previous = recoverySession();
    if (previous && (previous.userId !== me.user.id || previous.organizationId !== me.organization.id)) sessionCleared();
    bindRecoverySession(me.organization.id, me.user.id);
    setUser(me.user);
    setOrganization(me.organization);
    setMembershipRole(me.membership_role ?? null);
  }, []);

  useEffect(() => {
    const generation = sessionGeneration();
    let active = true;
    void (async () => {
      try {
        await refreshProfile();
      } catch {
        if (!active || generation !== sessionGeneration()) return;
        clearTokens();
        setUser(null);
        setOrganization(null);
      setMembershipRole(null);
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => { active = false; };
  }, [refreshProfile]);

  const applyAuthResponse = useCallback(async (response: AuthResponse) => {
    sessionCleared();
    setTokens(response.tokens.access_token, response.tokens.refresh_token || undefined);
    bindRecoverySession(response.organization.id, response.user.id);
    setUser(response.user);
    setOrganization(response.organization);
    setMembershipRole(response.membership_role ?? null);
  }, []);

  const finishSignIn = useCallback(
    async (response: AuthResponse, nextPath?: string) => {
      await applyAuthResponse(response);
      if (mustVerifyEmail && !response.user.email_verified) {
        router.replace("/verify-email");
      } else {
        router.replace(sanitizeNextPath(nextPath));
      }
    },
    [applyAuthResponse, mustVerifyEmail, router],
  );

  const login = useCallback(
    async (email: string, password: string, nextPath?: string): Promise<LoginOutcome> => {
      const deviceToken = rememberedDeviceToken(email);
      const response = await api.auth.login({
        email,
        password,
        ...(deviceToken ? { device_token: deviceToken } : {}),
      });
      if ("two_factor_required" in response && response.two_factor_required) {
        // A stored grant that no longer skips the code was revoked or expired.
        if (deviceToken) forgetRememberedDevice(email);
        return { status: "two_factor_required", challenge: response };
      }
      await finishSignIn(response as AuthResponse, nextPath);
      return { status: "signed_in" };
    },
    [finishSignIn],
  );

  const completeTwoFactorLogin = useCallback(
    async (
      email: string,
      pendingToken: string,
      factor: SecondFactor,
      rememberThisDevice: boolean,
      nextPath?: string,
    ) => {
      const response = await api.auth.loginTwoFactor({
        pending_token: pendingToken,
        ...("code" in factor ? { code: factor.code } : { recovery_code: factor.recoveryCode }),
        remember_device: rememberThisDevice,
      });
      if (response.device_token && response.device_token_expires_at) {
        rememberDevice(email, response.device_token, response.device_token_expires_at);
      }
      await finishSignIn(response, nextPath);
    },
    [finishSignIn],
  );

  const register = useCallback(
    async (email: string, password: string, organizationName: string) => {
      const response = await api.auth.register({ email, password, organization_name: organizationName });
      await applyAuthResponse(response);
      if (mustVerifyEmail && !response.user.email_verified) {
        router.replace("/verify-email");
      } else {
        router.replace("/");
      }
    },
    [applyAuthResponse, mustVerifyEmail, router],
  );

  const logout = useCallback(async () => {
    const refreshToken = getRefreshToken();
    const logoutRequest = api.auth.logout(refreshToken ?? undefined);
    clearTokens();
    const generation = sessionGeneration();
    setUser(null);
    setOrganization(null);
    setMembershipRole(null);
    try {
      await logoutRequest;
    } catch {
      // Ignore logout failures; local session is cleared regardless.
    } finally {
      if (generation === sessionGeneration()) {
        setUser(null);
        setOrganization(null);
      setMembershipRole(null);
        router.replace("/login");
      }
    }
  }, [router]);

  const value = useMemo(
    () => ({
      user,
      organization,
      membershipRole,
      loading,
      isAuthenticated: Boolean(user) && isAuthenticated(),
      login,
      completeTwoFactorLogin,
      register,
      logout,
      refreshProfile,
    }),
    [user, organization, membershipRole, loading, login, completeTwoFactorLogin, register, logout, refreshProfile],
  );

  return <AuthContext.Provider value={value}>
    <PrivateQueryProvider organizationId={organization?.id ?? null} userId={user?.id ?? null}>
      {children}
    </PrivateQueryProvider>
  </AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}

export function useRequireAuth() {
  const auth = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!auth.loading && !isAuthenticated()) {
      const next = sanitizeNextPath(window.location.pathname + window.location.search);
      router.replace(next !== "/" ? `/login?next=${encodeURIComponent(next)}` : "/login");
    }
  }, [auth.loading, router]);

  return auth;
}

export function getAuthErrorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "Authentication failed";
}
