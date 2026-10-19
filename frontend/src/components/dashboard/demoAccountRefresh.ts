/** Owner refresh cadence. Matches BLOFIN_DEMO_SNAPSHOT_SYNC_SECONDS (30–60). */
export const DEMO_REFRESH_INTERVAL_MS = 45_000;
export const DEMO_MAX_BACKOFF_MS = 900_000;
export const DEMO_REQUEST_TIMEOUT_MS = 30_000;

export function demoRefreshIntervalMs(seconds?: number | null): number {
  if (typeof seconds === "number" && seconds >= 30 && seconds <= 60) {
    return seconds * 1000;
  }
  return DEMO_REFRESH_INTERVAL_MS;
}
