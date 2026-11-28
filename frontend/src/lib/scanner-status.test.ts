import { describe, expect, it } from "vitest";

import type { WorkerComponentObservation, WorkerRuntimeObservation } from "@/lib/api/types";
import { scannerStatus, timeAgo } from "@/lib/scanner-status";

const NOW = Date.parse("2026-11-27T12:00:00Z");
const at = (secondsAgo: number) => new Date(NOW - secondsAgo * 1000).toISOString();

function runtime(worker: WorkerComponentObservation, watcher?: WorkerComponentObservation): WorkerRuntimeObservation {
  return { available: true, paper_worker: worker, watcher };
}

describe("timeAgo", () => {
  it("speaks in plain units", () => {
    expect(timeAgo(at(10), NOW)).toBe("just now");
    expect(timeAgo(at(150), NOW)).toBe("2 min ago");
    expect(timeAgo(at(3 * 3600), NOW)).toBe("3 h ago");
    expect(timeAgo(at(3 * 86400), NOW)).toBe("3 days ago");
  });
});

describe("scannerStatus", () => {
  it("says Scanning with the last scan time when the worker heartbeat is fresh", () => {
    const status = scannerStatus(
      runtime(
        { health_state: "RUNNING", activation_state: "running", heartbeat_at: at(5) },
        { health_state: "RUNNING", activation_state: "running", last_scan_at: at(120) },
      ),
      NOW,
    );
    expect(status).toEqual({ tone: "ok", text: "Scanning · last scan 2 min ago" });
  });

  it("warns clearly when scans stop", () => {
    const status = scannerStatus(
      runtime({ health_state: "STALE", activation_state: "STALE", heartbeat_at: at(900) }),
      NOW,
    );
    expect(status.tone).toBe("down");
    expect(status.text).toBe("Scans stopped · last scan 15 min ago");
    expect(status.detail).toMatch(/not checked in/);
  });

  it("names the kill switch, a switched-off scanner and scan errors in plain words", () => {
    const fresh = (activation_state: string) =>
      scannerStatus(runtime({ health_state: "RUNNING", activation_state, heartbeat_at: at(5), last_error_code: "x_y" }), NOW);
    expect(fresh("monitoring").text).toBe("Watching only · last scan just now");
    expect(fresh("refused").text).toBe("Scanner is switched off · last scan just now");
    expect(fresh("disarmed").tone).toBe("warn");
    expect(fresh("degraded").text).toBe("Scanning with errors · last scan just now");
    expect(fresh("failed").detail).not.toMatch(/_/);
  });

  it("does not invent a state when nothing was reported", () => {
    expect(scannerStatus(undefined, NOW)).toEqual({ tone: "unknown", text: "Scanner status not reported yet" });
    expect(scannerStatus({ available: false }, NOW).tone).toBe("unknown");
    expect(scannerStatus(runtime({ health_state: "UNAVAILABLE" }), NOW).tone).toBe("unknown");
  });
});
