"use client";

import { SessionScoped } from "@/components/settings/SessionScoped";
import { SettingsReadout } from "@/components/settings/SettingsReadout";
import { WatcherAssignmentsSection } from "@/components/WatcherAssignmentsSection";
import { WatcherWatchlistSection } from "@/components/WatcherWatchlistSection";
import { WorkerRuntimeReadout } from "@/components/WorkerRuntimeReadout";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import { useAppContext } from "@/contexts/AppContext";
import { useWatcherMonitoring } from "@/hooks/useWatcherMonitoring";
import { formatDateTime } from "@/lib/format";

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
      <SettingsReadout
        rows={[
          [
            "Watcher",
            monitoring.loading
              ? "Refreshing…"
              : monitoring.error
                ? "Unavailable"
                : (monitoring.data?.paper_monitoring_status ?? "Unavailable"),
          ],
          [
            "Last scan",
            monitoring.data?.last_scan_at ? formatDateTime(monitoring.data.last_scan_at) : "Unavailable",
          ],
        ]}
      />
      <WatcherWatchlistSection />
      <WatcherAssignmentsSection />
      <WorkerRuntimeReadout
        observation={health?.worker_flags_source === "worker" ? health.worker_effective : null}
      />
    </div>
  );
}
