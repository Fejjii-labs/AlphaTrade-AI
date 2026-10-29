"use client";

import { useMemo, useState } from "react";

import {
  TraderDashboardView,
  type TraderDashboardData,
} from "@/components/dashboard/TraderDashboardView";
import { describeSafetyPosture } from "@/components/workflows";
import { useSafetyPosture } from "@/contexts/AppContext";
import { useDashboardSources } from "@/hooks/useDashboardSources";
import { api } from "@/lib/api";

type DashboardPayloads = {
  [K in keyof TraderDashboardData]: NonNullable<TraderDashboardData[K]["data"]>;
};

type DashboardLoaders = {
  [K in keyof DashboardPayloads]: () => Promise<DashboardPayloads[K]>;
};

export default function DashboardPage() {
  const [demoRefreshKey, setDemoRefreshKey] = useState(0);
  const { executionMode, realTradingEnabled } = useSafetyPosture();

  // All requests start in parallel and each section renders as soon as its own
  // data arrives; the account cards mount immediately and fetch alongside.
  const loaders = useMemo<DashboardLoaders>(
    () => ({
      portfolio: () => api.performance.portfolio(),
      journal: () => api.journal.listTrades({ limit: 8 }),
      strategyStats: () =>
        api.journal.statistics({ group_by: "strategy", limit: 8 }),
      summary: () => api.dashboard.summary(),
      watcher: () => api.marketWatcher.monitoring(),
      market: () => api.canonical.getMarketStatus(),
      alerts: () => api.alerts.list({ limit: 8 }),
    }),
    [],
  );

  const { data, loading, reload } = useDashboardSources<DashboardPayloads>(loaders);

  const posture = describeSafetyPosture(
    data.summary.data?.safety.execution_mode ?? executionMode,
    data.summary.data?.safety.real_trading_enabled ?? realTradingEnabled,
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
    />
  );
}
