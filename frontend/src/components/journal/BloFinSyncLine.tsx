"use client";

import { useCallback, useState } from "react";

import { useNativeActivity } from "@/components/activity/useNativeActivity";
import { Button } from "@/components/ui/button";
import { useAsyncData } from "@/hooks/useAsyncData";
import { api } from "@/lib/api";
import type { BloFinSyncSnapshotItem } from "@/lib/api/types";
import { formatPrice, formatQuantity } from "@/lib/format";
import { nativeIdentity, nativeTime } from "@/lib/native-activity";

/** "2 min ago" style age; whole minutes, hours or days. */
export function ageText(iso: string, now: number = Date.now()): string {
  const minutes = Math.max(0, Math.floor((now - new Date(iso).getTime()) / 60_000));
  if (!Number.isFinite(minutes)) return "";
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  return `${Math.floor(hours / 24)} d ago`;
}

export function syncStatusText(snapshot: BloFinSyncSnapshotItem | null, failed: boolean): string {
  if (failed) return "Sync failed";
  if (!snapshot) return "Not synced yet";
  if (snapshot.health_status === "unavailable") return "Sync failed";
  return `Synced ${ageText(snapshot.synced_at)}`;
}

/** One line: BloFin sync state and a Sync button. */
export function BloFinSyncLine({ onSynced }: { onSynced?: () => void }) {
  const [syncing, setSyncing] = useState(false);
  const [failed, setFailed] = useState(false);
  const loader = useCallback(async () => {
    try {
      return await api.exchange.blofinSyncLatest();
    } catch {
      return null;
    }
  }, []);
  const { data, reload } = useAsyncData(loader, []);

  async function sync() {
    setSyncing(true);
    setFailed(false);
    try {
      await api.exchange.blofinSync();
      await reload();
      onSynced?.();
    } catch {
      setFailed(true);
    } finally {
      setSyncing(false);
    }
  }

  const text = syncStatusText(data ?? null, failed);
  return (
    <div className="flex items-center justify-between gap-3 text-sm" data-testid="blofin-sync-line">
      <span className={text === "Sync failed" ? "text-danger" : "text-text-secondary"} role="status">
        BloFin · {text}
      </span>
      <Button variant="outline" size="sm" className="h-10" disabled={syncing} onClick={() => void sync()}>
        {syncing ? "Syncing…" : "Sync"}
      </Button>
    </div>
  );
}

function FillsList() {
  const { page, busy, error, more } = useNativeActivity("fill", 0);
  if (busy && !page) return <p role="status" className="text-sm">Loading…</p>;
  if (error) return <p role="alert" className="text-sm text-danger">Fills unavailable.</p>;
  if (!page || page.identity_status !== "verified") return <p className="text-sm text-text-muted">No fills.</p>;
  const fills = page.items.filter((item) => item.kind === "fill");
  if (!fills.length) return <p className="text-sm text-text-muted">No fills.</p>;
  return (
    <>
      <ul className="divide-y divide-border-subtle text-sm">
        {fills.map((item) => (
          <li key={nativeIdentity(page, item)} className="grid grid-cols-[1fr_auto] gap-1 py-2 sm:grid-cols-[1fr_1fr_1fr_auto]" data-testid="blofin-fill-row">
            <span className="font-medium">{item.instrument} · {item.side === "buy" ? "Buy" : "Sell"}</span>
            <span className="tabular-nums">{formatQuantity(item.quantity)} @ {formatPrice(item.price ?? item.average_price)}</span>
            <span className="tabular-nums text-text-secondary">Fee {item.fee == null ? "—" : formatPrice(item.fee, { maximumFractionDigits: 4 })}</span>
            <span className="text-xs text-text-secondary">{nativeTime(item.occurred_at_ms)}</span>
          </li>
        ))}
      </ul>
      {page.next_cursor ? (
        <Button variant="ghost" size="sm" disabled={busy} onClick={more}>Older fills</Button>
      ) : null}
    </>
  );
}

/** BloFin fills, loaded only when expanded. */
export function BloFinFills() {
  const [open, setOpen] = useState(false);
  return (
    <details className="text-sm" onToggle={(event) => setOpen(event.currentTarget.open)} data-testid="blofin-fills">
      <summary className="min-h-11 cursor-pointer py-2 text-text-secondary">BloFin fills</summary>
      {open ? <FillsList /> : null}
    </details>
  );
}
