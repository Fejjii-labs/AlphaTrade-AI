"use client";

import { useCallback, useState } from "react";

import { ErrorState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusTile, type StatusTone } from "@/components/ui/status-tile";
import { useAsyncData } from "@/hooks/useAsyncData";
import { api, ApiError } from "@/lib/api";
import type { BloFinSyncSnapshotItem } from "@/lib/api/types";
import { formatDateTime } from "@/lib/format";

export type SyncState = { kind: "ok"; snapshot: BloFinSyncSnapshotItem } | { kind: "none" };

/** One status word, plus one short reason only when something is wrong. */
export function exchangeStatus(state: SyncState): { word: string; tone: StatusTone; reason: string | null } {
  if (state.kind === "none") return { word: "Not connected", tone: "unknown", reason: null };
  const { health_status: health, is_stale: stale } = state.snapshot;
  if (health === "unavailable" || health === "degraded") {
    return { word: "Error", tone: "down", reason: "BloFin did not answer the last sync." };
  }
  if (stale || health === "stale") return { word: "Connected", tone: "warn", reason: "Last sync is old." };
  return { word: "Connected", tone: "ok", reason: null };
}

async function loadLatest(): Promise<SyncState> {
  try {
    return { kind: "ok", snapshot: await api.exchange.blofinSyncLatest() };
  } catch (error) {
    // No snapshot yet, or not allowed to read it: nothing is connected for this user.
    if (error instanceof ApiError && (error.status === 404 || error.status === 403)) return { kind: "none" };
    throw error;
  }
}

/** Settings > Exchange: the BloFin demo connection in one card. Never places orders. */
export function ExchangeStatusCard() {
  const loader = useCallback(loadLatest, []);
  const { data, loading, error, reload } = useAsyncData(loader, []);
  const [synced, setSynced] = useState<SyncState | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [syncFailed, setSyncFailed] = useState(false);
  const state = synced ?? data;

  async function syncNow() {
    setSyncing(true);
    setSyncFailed(false);
    try {
      const result = await api.exchange.blofinSync();
      setSynced({ kind: "ok", snapshot: result.snapshot });
    } catch {
      setSyncFailed(true);
    } finally {
      setSyncing(false);
    }
  }

  const status = state ? exchangeStatus(state) : null;
  const snapshot = state?.kind === "ok" ? state.snapshot : null;
  return (
    <section className="space-y-3 rounded-card border border-border-subtle bg-surface-1 p-4" aria-busy={loading || undefined}>
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-base font-medium text-text-primary">BloFin demo</h2>
        <Button size="sm" disabled={syncing || loading} onClick={() => void syncNow()}>
          {syncing ? "Syncing…" : "Sync now"}
        </Button>
      </div>
      {loading && !synced ? (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4" data-testid="exchange-status-skeleton">
          {[0, 1, 2, 3].map((key) => (
            <Skeleton key={key} className="h-14 w-full" />
          ))}
        </div>
      ) : error && !synced ? (
        <ErrorState message={error} onRetry={() => void reload()} className="py-4" />
      ) : status ? (
        <div className="space-y-2" data-testid="exchange-status">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <StatusTile name="Status" tone={status.tone} state={status.word} />
            <div data-testid="exchange-last-sync">
              <StatusTile
                name="Last sync"
                tone="unknown"
                state={snapshot ? formatDateTime(snapshot.synced_at) : "—"}
              />
            </div>
            <StatusTile name="Balances" tone="unknown" state={snapshot ? String(snapshot.balance_count) : "—"} />
            <StatusTile name="Positions" tone="unknown" state={snapshot ? String(snapshot.position_count) : "—"} />
          </div>
          {status.reason ? <p className="text-xs text-text-secondary">{status.reason}</p> : null}
        </div>
      ) : null}
      {syncFailed ? (
        <p role="alert" className="text-xs text-danger">
          Sync failed. Try again.
        </p>
      ) : null}
    </section>
  );
}
