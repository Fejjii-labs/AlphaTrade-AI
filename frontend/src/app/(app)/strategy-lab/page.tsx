"use client";

import Link from "next/link";
import { useCallback } from "react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { WatcherMonitoringPanel } from "@/components/WatcherMonitoringPanel";
import { useAsyncData } from "@/hooks/useAsyncData";
import { api } from "@/lib/api";
import { SETUP_TYPE_OPTIONS } from "@/lib/setup-types";
import { strategyStatusFor } from "@/lib/strategy-status";

function setupLabel(value: string) {
  return SETUP_TYPE_OPTIONS.find((o) => o.value === value)?.label ?? value;
}

export default function StrategyLabPage() {
  const loader = useCallback(() => api.strategies.list(), []);
  const { data, loading, error, reload } = useAsyncData(loader, []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Strategy Lab</h1>
        <p className="text-sm text-zinc-400">
          Review strategy cards and discuss changes with Agent.
        </p>
      </div>

      <WatcherMonitoringPanel compact />

      <div>
        <Link
          href="/agent?intent=strategy"
          className="inline-flex rounded-md bg-zinc-100 px-4 py-2 text-sm font-medium text-zinc-900 hover:bg-white"
        >
          Discuss a strategy with Agent
        </Link>
      </div>

      {loading ? <LoadingState label="Loading strategies…" /> : null}
      {error ? <ErrorState message={error} onRetry={() => void reload()} /> : null}

      {data?.items.length ? (
        <div className="grid gap-4 md:grid-cols-2">
          {data.items.map((strategy) => {
            const view = strategyStatusFor(strategy);
            return (
              <Link key={strategy.id} href={`/strategy-lab/${strategy.id}`} data-testid="strategy-card">
                <Card className="transition hover:border-zinc-600">
                  <CardHeader>
                    <div className="flex items-start justify-between gap-2">
                      <CardTitle className="text-base">{strategy.name}</CardTitle>
                      <Badge variant={view.variant} data-testid="strategy-status-badge">
                        {view.label}
                      </Badge>
                    </div>
                  </CardHeader>
                  <CardContent className="space-y-1 text-sm text-zinc-300">
                    <p>Setup: {setupLabel(strategy.setup_type)}</p>
                    <p>Version: {strategy.current_version}</p>
                    <p className="text-zinc-400" data-testid="strategy-next-action">
                      Next: {view.nextAction}
                    </p>
                  </CardContent>
                </Card>
              </Link>
            );
          })}
        </div>
      ) : null}

      {data && !data.items.length ? (
        <EmptyState
          title="No strategies yet"
          description="Create a strategy card from the AI Workspace or API to populate the library."
        />
      ) : null}
    </div>
  );
}
