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
import type { AuthResponse, MeResponse } from "@/lib/api/types";
import { sanitizeNextPath } from "@/lib/auth/boundary";
import { clearTokens, getRefreshToken, isAuthenticated, setTokens } from "@/lib/auth/session";
import { useAppContext } from "@/contexts/AppContext";

interface AuthContextValue {
  user: MeResponse["user"] | null;
  organization: MeResponse["organization"] | null;
  loading: boolean;
  isAuthenticated: boolean;
  login: (email: string, password: string, nextPath?: string) => Promise<void>;
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
  const [loading, setLoading] = useState(true);

  const refreshProfile = useCallback(async () => {
    if (!isAuthenticated()) {
      setUser(null);
      setOrganization(null);
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
  }, []);

  const login = useCallback(
    async (email: string, password: string, nextPath?: string) => {
      const response = await api.auth.login({ email, password });
      await applyAuthResponse(response);
      if (mustVerifyEmail && !response.user.email_verified) {
        router.replace("/verify-email");
      } else {
        router.replace(sanitizeNextPath(nextPath));
      }
    },
    [applyAuthResponse, mustVerifyEmail, router],
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
    try {
      await logoutRequest;
    } catch {
      // Ignore logout failures; local session is cleared regardless.
    } finally {
      if (generation === sessionGeneration()) {
        setUser(null);
        setOrganization(null);
        router.replace("/login");
      }
    }
  }, [router]);

  const value = useMemo(
    () => ({
      user,
      organization,
      loading,
      isAuthenticated: Boolean(user) && isAuthenticated(),
      login,
      register,
      logout,
      refreshProfile,
    }),
    [user, organization, loading, login, register, logout, refreshProfile],
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
