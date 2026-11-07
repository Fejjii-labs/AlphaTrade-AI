"use client";

import { useDemoAccountSnapshot } from "@/components/dashboard/useDemoAccountSnapshot";
import { formatDateTime } from "@/lib/format";

/** Owner refresh for /exchange. Readers only reload the saved snapshot. */
export function ExchangeDemoAccountSync() {
  const { account, status, error } = useDemoAccountSnapshot(0);
  const stale = status === "stale";

  return (
    <section data-testid="exchange-demo-sync" className="space-y-2">
      <h2 className="text-lg font-medium text-text-primary">Demo account sync</h2>
      <p className="text-sm text-text-secondary">
        Read-only balances and positions. This page does not place orders.
      </p>
      {account?.synced_at ? (
        <p
          data-testid="exchange-last-synced"
          className={stale ? "text-sm text-amber-300" : "text-sm text-text-primary"}
        >
          Last synced {formatDateTime(account.synced_at)}
          {stale ? " · stale" : ""}
        </p>
      ) : (
        <p className="text-sm text-text-secondary">No demo snapshot yet.</p>
      )}
      {status ? (
        <p className="text-xs uppercase tracking-wide text-text-muted">{status}</p>
      ) : null}
      {error ? (
        <p role="alert" className="text-sm text-rose-300">
          {error}
        </p>
      ) : null}
      {account?.performance?.activity_sync_status === "disabled" ? (
        <p role="status" className="text-sm text-amber-300">
          Activity sync disabled. Win rate stays withheld.
        </p>
      ) : null}
    </section>
  );
}
