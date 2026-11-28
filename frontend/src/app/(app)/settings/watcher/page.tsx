"use client";

import { ScannerStatusLine } from "@/components/autonomous/ScannerStatusLine";
import { SessionScoped } from "@/components/settings/SessionScoped";
import { WatcherWatchlistSection } from "@/components/WatcherWatchlistSection";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import { useAppContext } from "@/contexts/AppContext";

export default function MarketsSettingsPage() {
  return (
    <SessionScoped>
      <WatcherSettings />
    </SessionScoped>
  );
}

/**
 * The coins the bot watches, and its scanner status from the worker's own
 * heartbeat. The older per-coin assignment status (`paper_monitoring_status`,
 * "Coin strategies") is not shown: the bot no longer scans through it.
 */
function WatcherSettings() {
  const { loading, refreshStatus } = useAppContext();
  return (
    <div className="page-sections" data-testid="settings-watcher">
      <PageHeader
        title="Markets"
        actions={
          <Button
            variant="outline"
            className="h-11"
            aria-label="Refresh markets"
            disabled={loading}
            onClick={() => void refreshStatus()}
          >
            Refresh
          </Button>
        }
      />
      <ScannerStatusLine />
      <WatcherWatchlistSection />
    </div>
  );
}
