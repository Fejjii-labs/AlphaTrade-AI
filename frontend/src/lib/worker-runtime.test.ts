import { describe, expect, it } from "vitest";

import type { WorkerEffectiveObservation } from "@/lib/api/types";
import {
  describedWorkerHealth,
  formatWorkerFlag,
  workerFlagFreshness,
} from "@/lib/worker-runtime";

function observation(
  overrides: Partial<WorkerEffectiveObservation> = {},
): WorkerEffectiveObservation {
  return {
    source: "worker",
    health_state: "RUNNING",
    stale: false,
    autonomy_mode: "internal_paper",
    autonomous_internal_paper_env_ceiling: true,
    autonomous_internal_paper_db_enabled: true,
    autonomous_internal_paper_effective: true,
    watcher_orchestration_enabled: true,
    watcher_paper_staging_activation: true,
    telegram_paper_activation_armed: true,
    telegram_interaction_enabled: true,
    telegram_alerts_enabled: true,
    automatic_telegram_delivery_enabled: true,
    watchlist: ["BTCUSDT"],
    real_trading_enabled: false,
    ...overrides,
  };
}

describe("worker runtime display", () => {
  it("labels a fresh worker report without using the liveness token", () => {
    expect(workerFlagFreshness(observation())).toBe("Fresh");
    expect(describedWorkerHealth("RUNNING", false)).toBe("Fresh");
  });

  it("labels a stale worker report", () => {
    expect(workerFlagFreshness(observation({ stale: true, health_state: "STALE" }))).toBe(
      "Stale",
    );
    expect(describedWorkerHealth("STALE", true)).toBe("Stale");
  });

  it("does not treat a missing report as worker-off", () => {
    expect(workerFlagFreshness(undefined)).toBe("Not reported");
    expect(workerFlagFreshness(observation({ source: "unavailable" }))).toBe("Not reported");
    expect(formatWorkerFlag(false, false)).toBe("Not reported");
    expect(describedWorkerHealth("UNAVAILABLE", true)).toBe("Not reported");
  });
});
