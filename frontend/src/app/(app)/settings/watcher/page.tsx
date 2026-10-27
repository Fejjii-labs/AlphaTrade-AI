"use client";

import { SessionScoped } from "@/components/settings/SessionScoped";
import { WatcherAssignmentsSection } from "@/components/WatcherAssignmentsSection";
import { WatcherWatchlistSection } from "@/components/WatcherWatchlistSection";
import { WorkerRuntimeReadout } from "@/components/WorkerRuntimeReadout";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import { StatusTile, type StatusTone } from "@/components/ui/status-tile";
import { useAppContext } from "@/contexts/AppContext";
import { useWatcherMonitoring } from "@/hooks/useWatcherMonitoring";
import type { WorkerEffectiveObservation } from "@/lib/api/types";
import { formatDateTime } from "@/lib/format";
import { workerFlagFreshness } from "@/lib/worker-runtime";

type TileState = { tone: StatusTone; state: string };

function watcherTile(monitoring: ReturnType<typeof useWatcherMonitoring>): TileState {
  if (monitoring.loading) return { tone: "unknown", state: "Loading" };
  const status = monitoring.error ? null : monitoring.data?.paper_monitoring_status;
  if (status === "RUNNING") return { tone: "ok", state: "Running" };
  if (status === "DEGRADED") return { tone: "warn", state: "Degraded" };
  if (status === "STALE") return { tone: "warn", state: "Stale" };
  if (status === "STOPPED") return { tone: "down", state: "Stopped" };
  if (status === "BLOCKED") return { tone: "down", state: "Blocked" };
  return { tone: "unknown", state: "Unknown" };
}

function workerTile(worker: WorkerEffectiveObservation | null | undefined): TileState {
  const freshness = workerFlagFreshness(worker);
  if (freshness === "Fresh") return { tone: "ok", state: "Running" };
  if (freshness === "Stale") return { tone: "warn", state: "Stale" };
  return { tone: "unknown", state: "Not reported" };
}

export default function WatcherSettingsPage() {
  return (
    <SessionScoped>
      <WatcherSettings />
    </SessionScoped>
  );
}

function WatcherSettings() {
  const { health } = useAppContext();
  const monitoring = useWatcherMonitoring();
  const worker = health?.worker_flags_source === "worker" ? health.worker_effective : null;
  return (
    <div className="page-sections" data-testid="settings-watcher">
      <PageHeader
        title="Watcher"
        actions={
          <Button
            variant="outline"
            className="h-11"
            disabled={monitoring.loading}
            onClick={() => void monitoring.reload()}
          >
            Refresh watcher
          </Button>
        }
      />
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        <StatusTile name="Watcher" {...watcherTile(monitoring)} />
        <StatusTile
          name="Last scan"
          tone={monitoring.data?.last_scan_at ? "ok" : "unknown"}
          state={monitoring.data?.last_scan_at ? formatDateTime(monitoring.data.last_scan_at) : "None yet"}
        />
        <StatusTile name="Worker" {...workerTile(worker)} />
      </div>
      <WatcherWatchlistSection />
      <WatcherAssignmentsSection />
      <details className="rounded-card border border-border-subtle bg-surface-1 p-4">
        <summary className="cursor-pointer text-sm">Worker details</summary>
        <div className="pt-2">
          <WorkerRuntimeReadout observation={worker} />
        </div>
      </details>
    </div>
  );
}
