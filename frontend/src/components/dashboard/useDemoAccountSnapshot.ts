"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  demoAccountApi,
  type DashboardDemoAccount,
} from "@/lib/api/dashboard-demo-account";

// At most 20 scheduled attempts/hour/tab; server also enforces 30/org/hour.
export const DEMO_REFRESH_INTERVAL_MS = 180_000;
export const DEMO_MAX_BACKOFF_MS = 900_000;
export const DEMO_REQUEST_TIMEOUT_MS = 30_000;

const hasSnapshot = (value: DashboardDemoAccount | null) =>
  value && ["ok", "degraded", "stale"].includes(value.status);

export function useDemoAccountSnapshot(refreshKey: number) {
  const [account, setAccount] = useState<DashboardDemoAccount | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const saved = useRef<DashboardDemoAccount | null>(null);
  const failures = useRef(0);
  const nextDue = useRef(Date.now() + DEMO_REFRESH_INTERVAL_MS);
  const generation = useRef(0);
  const mounted = useRef(false);
  const flight = useRef<{
    native: boolean;
    controller: AbortController;
  } | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const schedule = useRef<() => void>(() => {});

  const load = useCallback(async (native = false, automatic = false) => {
    if (!mounted.current) return;
    // Native sync is single-flight. A newer saved read may supersede an older read.
    if (flight.current) {
      if (flight.current.native || native || automatic) return;
      flight.current.controller.abort();
    }
    const request = ++generation.current;
    const controller = new AbortController();
    flight.current = { native, controller };
    setBusy(true);
    let timeout: ReturnType<typeof setTimeout> | undefined;
    let onAbort: (() => void) | undefined;
    try {
      const result = await Promise.race([
        native
          ? demoAccountApi.refresh(controller.signal)
          : demoAccountApi.latest(controller.signal),
        new Promise<never>((_, reject) => {
          onAbort = () => reject(new Error("Account request cancelled"));
          controller.signal.addEventListener("abort", onAbort, { once: true });
          timeout = setTimeout(() => {
            controller.abort();
          }, DEMO_REQUEST_TIMEOUT_MS);
        }),
      ]);
      if (!mounted.current || request !== generation.current) return;
      // Never retain a successful snapshot under a different/unknown account binding.
      if (saved.current && result.account_id !== saved.current.account_id) {
        saved.current = null;
        setAccount(null);
      }
      const failed = result.status === "unavailable" || !!result.refresh_error;
      if (!failed || hasSnapshot(result) || !hasSnapshot(saved.current)) {
        saved.current = result;
        setAccount(result);
      }
      if (failed) {
        throw new Error("Native account sync unavailable");
      }
      setError(null);
      failures.current = 0;
      if (native || automatic) {
        nextDue.current = Date.now() + DEMO_REFRESH_INTERVAL_MS;
      }
    } catch {
      if (!mounted.current || request !== generation.current) return;
      // Preserve successful account evidence, including its original timestamp.
      setAccount(saved.current);
      setError(
        "Demo account refresh failed. Showing the last successful snapshot when available. Retry or check Exchange settings.",
      );
      failures.current += 1;
      nextDue.current =
        Date.now() +
        Math.min(
          DEMO_MAX_BACKOFF_MS,
          DEMO_REFRESH_INTERVAL_MS * 2 ** Math.min(failures.current, 3),
        );
    } finally {
      clearTimeout(timeout);
      if (onAbort) controller.signal.removeEventListener("abort", onAbort);
      if (mounted.current && request === generation.current) {
        flight.current = null;
        setBusy(false);
        setNow(Date.now());
        schedule.current();
      }
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    const stopTimer = () => {
      if (timer.current !== null) clearTimeout(timer.current);
      timer.current = null;
    };
    schedule.current = () => {
      stopTimer();
      if (
        document.visibilityState === "hidden" ||
        flight.current ||
        saved.current?.status === "inactive"
      ) return;
      timer.current = setTimeout(() => {
        if (document.visibilityState !== "hidden") {
          void load(saved.current?.can_refresh === true, true);
        }
      }, Math.max(0, nextDue.current - Date.now()));
    };
    const onVisibility = () => {
      setNow(Date.now());
      schedule.current();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      mounted.current = false;
      generation.current += 1;
      flight.current?.controller.abort();
      flight.current = null;
      stopTimer();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [load]);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  useEffect(() => {
    if (!account?.expires_at) return;
    const expires = Date.parse(account.expires_at);
    if (!Number.isFinite(expires)) return;
    const expiryTimer = setTimeout(
      () => setNow(Date.now()),
      Math.max(0, expires - Date.now()),
    );
    return () => clearTimeout(expiryTimer);
  }, [account?.expires_at]);

  const expired = !!account?.expires_at && now >= Date.parse(account.expires_at);
  const status = hasSnapshot(account) && (expired || error) ? "stale" : account?.status;
  return { account, busy, error, status, hasData: hasSnapshot(account), load };
}
