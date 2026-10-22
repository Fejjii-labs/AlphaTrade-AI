import type { WorkerEffectiveObservation } from "@/lib/api/types";

/** Display label for a worker heartbeat. Never the raw liveness token. */
export type WorkerFlagFreshness = "Fresh" | "Stale" | "Not reported";

export function workerFlagFreshness(
  observation: Pick<WorkerEffectiveObservation, "source" | "stale" | "health_state"> | null | undefined,
): WorkerFlagFreshness {
  if (!observation || observation.source !== "worker") return "Not reported";
  if (observation.stale || observation.health_state !== "RUNNING") return "Stale";
  return "Fresh";
}

export function describedWorkerHealth(
  health: string | null | undefined,
  stale: boolean | undefined,
): WorkerFlagFreshness {
  if (!health || health === "UNAVAILABLE") return "Not reported";
  if (stale || health === "STALE") return "Stale";
  if (health === "RUNNING") return "Fresh";
  return "Not reported";
}

export function formatWorkerFlag(value: boolean | undefined, reported: boolean): string {
  if (!reported) return "Not reported";
  return value ? "On" : "Off";
}
