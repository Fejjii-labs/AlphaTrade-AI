"use client";

import { useMemo, useState } from "react";

import {
  TraderDashboardView,
  type DashboardVenue,
  type TraderDashboardData,
} from "@/components/dashboard/TraderDashboardView";
import { DEFAULT_SCOPE } from "@/components/dashboard/performance/performance-model";
import { describeSafetyPosture } from "@/components/workflows";
import { useSafetyPosture } from "@/contexts/AppContext";
import { useDashboardSources } from "@/hooks/useDashboardSources";
import { api } from "@/lib/api";

type DashboardPayloads = {
  [K in keyof TraderDashboardData]: NonNullable<TraderDashboardData[K]["data"]>;
};

type DashboardLoaders = {
  [K in keyof DashboardPayloads]: (() => Promise<DashboardPayloads[K]>) | null;
};

export default function DashboardPage() {
  const [demoRefreshKey, setDemoRefreshKey] = useState(0);
  const [venue, setVenue] = useState<DashboardVenue>(DEFAULT_SCOPE);
  const { executionMode, realTradingEnabled } = useSafetyPosture();
  const simulator = venue === "paper";

  // All requests start in parallel and each section renders as soon as its own
  // data arrives; the account cards mount immediately and fetch alongside.
  // The summary (safety posture + daily discipline) is needed in every venue;
  // the simulator sources load only while the Paper venue is shown.
  const loaders = useMemo<DashboardLoaders>(
    () => ({
      portfolio: simulator ? () => api.performance.portfolio() : null,
      journal: simulator ? () => api.journal.listTrades({ limit: 8 }) : null,
      summary: () => api.dashboard.summary(),
      watcher: simulator ? () => api.marketWatcher.monitoring() : null,
      market: simulator ? () => api.canonical.getMarketStatus() : null,
      alerts: simulator ? () => api.alerts.list({ limit: 8 }) : null,
    }),
    [simulator],
  );

  const { data, loading, reload } = useDashboardSources<DashboardPayloads>(loaders);

  const realEnabled =
    data.summary.data?.safety.real_trading_enabled ?? realTradingEnabled;
  const posture = describeSafetyPosture(
    data.summary.data?.safety.execution_mode ?? executionMode,
    realEnabled,
  );

  return (
    <TraderDashboardView
      data={data as TraderDashboardData}
      posture={posture}
      onRetry={() => {
        setDemoRefreshKey((key) => key + 1);
        void reload();
      }}
      refreshing={loading}
      demoRefreshKey={demoRefreshKey}
      realTradingEnabled={realEnabled}
      venue={venue}
      onVenueChange={setVenue}
    />
  );
}
