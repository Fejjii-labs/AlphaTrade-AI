"use client";

import { useCallback, useState } from "react";

import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useAsyncData } from "@/hooks/useAsyncData";
import { api, ApiError } from "@/lib/api";
import type { BloFinSyncHealthStatus, BloFinSyncSnapshotItem } from "@/lib/api/types";

function healthVariant(
  status: BloFinSyncHealthStatus,
): "success" | "warning" | "danger" | "muted" {
  switch (status) {
    case "ok":
      return "success";
    case "degraded":
    case "stale":
      return "warning";
    case "unavailable":
      return "danger";
    default:
      return "muted";
  }
}

type LoadResult =
  | { empty: true; forbidden: false; snapshot: null }
  | { empty: false; forbidden: false; snapshot: BloFinSyncSnapshotItem }
  | { empty: false; forbidden: true; snapshot: null };

export function BloFinSyncPanel() {
  const [syncing, setSyncing] = useState(false);
  const [syncError, setSyncError] = useState<string | null>(null);

  const loader = useCallback(async (): Promise<LoadResult> => {
    try {
      const snapshot = await api.exchange.blofinSyncLatest();
      return { empty: false, forbidden: false, snapshot };
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        return { empty: true, forbidden: false, snapshot: null };
      }
      if (error instanceof ApiError && error.status === 403) {
        return { empty: false, forbidden: true, snapshot: null };
      }
      throw error;
    }
  }, []);

  const { data, loading, error, reload } = useAsyncData(loader, []);

  async function runSync() {
    setSyncError(null);
    setSyncing(true);
    try {
      await api.exchange.blofinSync();
      await reload();
    } catch (err) {
      setSyncError(err instanceof Error ? err.message : "BloFin sync failed.");
    } finally {
      setSyncing(false);
    }
  }

  if (loading) return <LoadingState label="Loading BloFin demo sync status…" />;
  if (error) return <ErrorState message={error} onRetry={() => void reload()} />;
  if (!data) {
    return <ErrorState message="BloFin sync status unavailable." onRetry={() => void reload()} />;
  }
  if (data.forbidden) {
    return (
      <div data-testid="blofin-sync-forbidden" className="space-y-2">
        <h2 className="text-lg font-medium text-text-primary">BloFin demo sync</h2>
        <p className="text-sm text-text-secondary">You do not have permission to view BloFin sync status.</p>
      </div>
    );
  }

  const snapshot = data.snapshot;

  return (
    <section data-testid="blofin-sync-panel" className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-medium text-text-primary">BloFin demo sync</h2>
          <p className="text-sm text-text-secondary">
            Read-only account and position reconciliation. Never places or cancels orders.
          </p>
        </div>
        <Button disabled={syncing} onClick={() => void runSync()}>
          {syncing ? "Syncing…" : "Sync now"}
        </Button>
      </div>

      {syncError ? (
        <p className="text-sm text-rose-300" role="alert">
          {syncError}
        </p>
      ) : null}

      {data.empty || !snapshot ? (
        <EmptyState
          title="No BloFin demo snapshot yet"
          description="Run a read-only sync when BloFin demo mode is configured."
        />
      ) : (
        <div className="space-y-3 rounded-lg border border-border-subtle bg-surface-1 p-4">
          <div className="flex flex-wrap items-center gap-3">
            <Badge variant={healthVariant(snapshot.health_status)}>{snapshot.health_status}</Badge>
            {snapshot.is_stale ? <Badge variant="warning">stale</Badge> : null}
            <span className="text-xs text-text-muted">
              Synced {new Date(snapshot.synced_at).toLocaleString()} · {snapshot.provider} ·{" "}
              {snapshot.exchange_mode}
            </span>
          </div>
          <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
            <div>
              <dt className="text-text-muted">Balances</dt>
              <dd className="text-text-primary">{snapshot.balance_count}</dd>
            </div>
            <div>
              <dt className="text-text-muted">Positions</dt>
              <dd className="text-text-primary">{snapshot.position_count}</dd>
            </div>
            <div>
              <dt className="text-text-muted">Read-only</dt>
              <dd className="text-text-primary">
                {snapshot.provenance?.read_only === false ? "no" : "yes"}
              </dd>
            </div>
            <div>
              <dt className="text-text-muted">Order mutations</dt>
              <dd className="text-text-primary">never</dd>
            </div>
          </dl>
          {snapshot.stale_reason ? (
            <p className="text-xs text-amber-200">{snapshot.stale_reason}</p>
          ) : null}
          {snapshot.error_summary ? (
            <p className="text-xs text-rose-300">{snapshot.error_summary}</p>
          ) : null}
        </div>
      )}
    </section>
  );
}
