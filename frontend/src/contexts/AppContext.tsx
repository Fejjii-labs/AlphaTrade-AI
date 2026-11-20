"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { api } from "@/lib/api";
import { friendlyError } from "@/lib/api/client";
import type {
  HealthResponse,
  KillSwitchStatus,
  ProviderStatusResponse,
} from "@/lib/api/types";
import { appConfig } from "@/lib/config";
import { isAuthenticated } from "@/lib/auth/session";

/** Health and kill-switch posture refresh cadence (~60 s, FP2-105). */
export const POSTURE_REFRESH_INTERVAL_MS = 60_000;

interface AppContextValue {
  health: HealthResponse | null;
  providers: ProviderStatusResponse | null;
  killSwitchActive: boolean;
  killSwitchStatus: KillSwitchStatus | null;
  killSwitchError: string | null;
  killSwitchBusy: boolean;
  refreshKillSwitch: () => Promise<void>;
  setKillSwitchActive: (active: boolean, reason: string) => Promise<void>;
  refreshStatus: () => Promise<void>;
  loading: boolean;
  /** Health/posture verification error; provider failures surface as providers === null. */
  error: string | null;
}

const AppContext = createContext<AppContextValue | null>(null);

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [providers, setProviders] = useState<ProviderStatusResponse | null>(
    null,
  );
  const [killSwitchStatus, setKillSwitchStatus] =
    useState<KillSwitchStatus | null>(null);
  const [killSwitchError, setKillSwitchError] = useState<string | null>(null);
  const [killSwitchBusy, setKillSwitchBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Per-source request-generation guards: only the latest refresh of a source
  // may apply its response, so a slow older request can never overwrite newer
  // posture (FP2-105). Each source also tracks its own in-flight flag so
  // background ticks never stack overlapping requests for the same source
  // while a hanging sibling cannot block the others.
  const healthGenerationRef = useRef(0);
  const providersGenerationRef = useRef(0);
  const killSwitchGenerationRef = useRef(0);
  const healthInFlightRef = useRef(false);
  const providersInFlightRef = useRef(false);
  const killSwitchInFlightRef = useRef(false);
  const foregroundRefreshesRef = useRef(0);

  const runHealthRefresh = useCallback(
    async ({ background }: { background: boolean }) => {
      if (background && healthInFlightRef.current) return;
      const generation = ++healthGenerationRef.current;
      healthInFlightRef.current = true;
      try {
        const healthRes = await api.health.get();
        if (generation !== healthGenerationRef.current) return;
        setHealth(healthRes);
        setError(null);
      } catch (err) {
        if (generation !== healthGenerationRef.current) return;
        // Fail closed: after a failed refresh the posture is no longer
        // verified — never keep presenting the old health snapshot as current
        // (FP2-101 class). Consumers render explicit "unverified" for null.
        setHealth(null);
        setError(friendlyError(err));
      } finally {
        if (generation === healthGenerationRef.current) {
          healthInFlightRef.current = false;
        }
      }
    },
    [],
  );

  const runProvidersRefresh = useCallback(
    async ({ background }: { background: boolean }) => {
      if (background && providersInFlightRef.current) return;
      const generation = ++providersGenerationRef.current;
      providersInFlightRef.current = true;
      try {
        const providersRes = await api.providers.status();
        if (generation !== providersGenerationRef.current) return;
        setProviders(providersRes);
      } catch {
        if (generation !== providersGenerationRef.current) return;
        // Provider failure means "providers unknown" (FP2-110); it must not
        // erase a successfully verified health posture, so only providers is
        // cleared here.
        setProviders(null);
      } finally {
        if (generation === providersGenerationRef.current) {
          providersInFlightRef.current = false;
        }
      }
    },
    [],
  );

  const runKillSwitchRefresh = useCallback(
    async ({ background }: { background: boolean }) => {
      if (!isAuthenticated()) {
        setKillSwitchStatus(null);
        return;
      }
      if (background && killSwitchInFlightRef.current) return;
      const generation = ++killSwitchGenerationRef.current;
      killSwitchInFlightRef.current = true;
      try {
        const status = await api.risk.killSwitch();
        if (generation !== killSwitchGenerationRef.current) return;
        setKillSwitchStatus(status);
        setKillSwitchError(null);
      } catch (err) {
        if (generation !== killSwitchGenerationRef.current) return;
        // Read failures must not invent an inactive local state: keep the last
        // authoritative kill-switch state and surface an explicit error.
        setKillSwitchError(friendlyError(err));
      } finally {
        if (generation === killSwitchGenerationRef.current) {
          killSwitchInFlightRef.current = false;
        }
      }
    },
    [],
  );

  const refreshKillSwitch = useCallback(
    () => runKillSwitchRefresh({ background: false }),
    [runKillSwitchRefresh],
  );

  const runStatusRefresh = useCallback(
    async ({ background }: { background: boolean }) => {
      if (!background) {
        foregroundRefreshesRef.current += 1;
        setLoading(true);
        setError(null);
      }
      try {
        // Each source refreshes independently: a slow, failed, or hanging
        // source never blocks or erases a successful sibling. Exactly one
        // request per source is issued per refresh cycle.
        await Promise.allSettled([
          runHealthRefresh({ background }),
          runProvidersRefresh({ background }),
          runKillSwitchRefresh({ background }),
        ]);
      } finally {
        if (!background) {
          foregroundRefreshesRef.current -= 1;
          if (foregroundRefreshesRef.current === 0) setLoading(false);
        }
      }
    },
    [runHealthRefresh, runProvidersRefresh, runKillSwitchRefresh],
  );

  const refreshStatus = useCallback(
    () => runStatusRefresh({ background: false }),
    [runStatusRefresh],
  );

  useEffect(() => {
    void refreshStatus();
  }, [refreshStatus]);

  // Periodic + focus/visibility posture refresh (FP2-105). Background ticks
  // are silent (no global loading flicker) and never overlap.
  useEffect(() => {
    const refreshInBackground = () => {
      void runStatusRefresh({ background: true });
    };
    const onVisibilityOrFocus = () => {
      if (document.visibilityState === "hidden") return;
      refreshInBackground();
    };

    // Hidden tabs skip ticks (each tick is three rate-limited API reads); the
    // visibilitychange handler refreshes as soon as the tab is shown again.
    const intervalId = window.setInterval(
      onVisibilityOrFocus,
      POSTURE_REFRESH_INTERVAL_MS,
    );
    window.addEventListener("focus", onVisibilityOrFocus);
    document.addEventListener("visibilitychange", onVisibilityOrFocus);
    return () => {
      window.clearInterval(intervalId);
      window.removeEventListener("focus", onVisibilityOrFocus);
      document.removeEventListener("visibilitychange", onVisibilityOrFocus);
    };
  }, [runStatusRefresh]);

  const setKillSwitchActive = useCallback(
    async (active: boolean, reason: string) => {
      setKillSwitchBusy(true);
      setKillSwitchError(null);
      // The mutation response is authoritative: invalidate any in-flight
      // background read so a stale status can never overwrite it. The
      // invalidated read can no longer clear its in-flight flag, so reset it
      // here to keep background kill-switch refreshes unblocked.
      killSwitchGenerationRef.current += 1;
      killSwitchInFlightRef.current = false;
      try {
        const body = {
          confirm: true,
          reason,
          expected_version: killSwitchStatus?.version ?? null,
        };
        const status = active
          ? await api.risk.activateKillSwitch(body)
          : await api.risk.deactivateKillSwitch(body);
        // Discard reads that started during the mutation window as well
        // (and unblock future background reads they can no longer release).
        killSwitchGenerationRef.current += 1;
        killSwitchInFlightRef.current = false;
        setKillSwitchStatus(status);
        await runHealthRefresh({ background: false });
        window.dispatchEvent(new Event("alphatrade:status-changed"));
      } catch (err) {
        setKillSwitchError(friendlyError(err, "Couldn't update. Try again."));
        throw err;
      } finally {
        setKillSwitchBusy(false);
      }
    },
    [killSwitchStatus?.version, runHealthRefresh],
  );

  const value = useMemo<AppContextValue>(
    () => ({
      health,
      providers,
      killSwitchActive: Boolean(killSwitchStatus?.execution_blocked),
      killSwitchStatus,
      killSwitchError,
      killSwitchBusy,
      refreshKillSwitch,
      setKillSwitchActive,
      refreshStatus,
      loading,
      error,
    }),
    [
      health,
      providers,
      killSwitchStatus,
      killSwitchError,
      killSwitchBusy,
      refreshKillSwitch,
      setKillSwitchActive,
      refreshStatus,
      loading,
      error,
    ],
  );

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useAppContext() {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useAppContext must be used within AppProvider");
  return ctx;
}

export interface SafetyPosture {
  /** Execution mode reported by backend /health, or null until verified. */
  executionMode: string | null;
  /** Real-trading flag reported by backend /health, or null until verified. */
  realTradingEnabled: boolean | null;
  providerMode: string;
  /** True only after /health has been loaded — never inferred from build config. */
  postureKnown: boolean;
}

export function useSafetyPosture(): SafetyPosture {
  const { health } = useAppContext();
  return {
    executionMode: health?.execution_mode ?? null,
    realTradingEnabled: health?.real_trading_enabled ?? null,
    providerMode: appConfig.providerMode,
    postureKnown: health != null,
  };
}
