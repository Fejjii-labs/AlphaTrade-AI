import type { StatusTone } from "@/components/ui/status-tile";
import type { WorkerRuntimeObservation } from "@/lib/api/types";

export interface ScannerStatus {
  tone: StatusTone;
  /** One plain line, e.g. "Scanning · last scan 2 min ago". */
  text: string;
  /** Optional second sentence for a warning. Never a raw code. */
  detail?: string;
}

/** "just now", "2 min ago", "3 h ago", "2 days ago". */
export function timeAgo(iso: string, now: number = Date.now()): string {
  const seconds = Math.max(0, (now - Date.parse(iso)) / 1000);
  if (!Number.isFinite(seconds)) return "at an unknown time";
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.floor(hours / 24)} days ago`;
}

/**
 * The bot's scanner, read from the worker's own heartbeats in `/health`:
 * `worker_runtime.paper_worker` (written around every scan cycle, with the
 * cycle status) and `.watcher` (last scan time). The older per-coin
 * assignment status (`paper_monitoring_status`) is not used here.
 */
export function scannerStatus(
  runtime: WorkerRuntimeObservation | null | undefined,
  now: number = Date.now(),
): ScannerStatus {
  const worker = runtime?.paper_worker;
  const watcher = runtime?.watcher;
  if (!runtime || runtime.available === false || !worker?.heartbeat_at) {
    return { tone: "unknown", text: "Scanner status not reported yet" };
  }
  const lastScan = watcher?.last_scan_at ?? worker.heartbeat_at;
  const when = `last scan ${timeAgo(lastScan, now)}`;
  if (worker.health_state !== "RUNNING") {
    return {
      tone: "down",
      text: `Scans stopped · ${when}`,
      detail: "The bot's worker has not checked in recently, so it is not scanning for trades.",
    };
  }
  // The cycle status the worker logs as `watcher_status` in `paper_worker_health`.
  const state = worker.activation_state;
  if (state === "monitoring") {
    return {
      tone: "warn",
      text: `Watching only · ${when}`,
      detail: "The kill switch is on, so the bot scans but does not open trades.",
    };
  }
  if (state === "refused" || state === "disarmed") {
    return {
      tone: "warn",
      text: `Scanner is switched off · ${when}`,
      detail: "The worker is running but its scanner is not enabled.",
    };
  }
  if (state === "failed" || state === "degraded") {
    return {
      tone: "warn",
      text: `Scanning with errors · ${when}`,
      detail: "Some scans reported a problem. The bot keeps retrying.",
    };
  }
  return { tone: "ok", text: `Scanning · ${when}` };
}
